#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SAMPLE_RATE_HZ = 24000
DEFAULT_MAX_TTS_CHARS = 1800
DEFAULT_CHUNK_SEGMENTS = 48
DEFAULT_CHUNK_EXT = ".m4a"
MIN_MAX_TTS_CHARS = 400


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate rights-cleared audiobook audio with Kokoro-82M."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="Book manifest JSON with segment text.",
    )
    parser.add_argument(
        "--out", type=Path, help="Output audio path. Defaults to manifest audio."
    )
    parser.add_argument(
        "--chunk-dir",
        type=Path,
        help="Directory for page/chapter-sized audio chunks.",
    )
    parser.add_argument(
        "--chunk-segments",
        type=int,
        default=DEFAULT_CHUNK_SEGMENTS,
        help="Maximum manifest segments per generated audio chunk.",
    )
    parser.add_argument(
        "--chunk-ext",
        default=DEFAULT_CHUNK_EXT,
        help="Audio extension for chunk files.",
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        help="Generate only the first N chunks for a local validation slice.",
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        help="Write updated rough-timing manifest to this path.",
    )
    parser.add_argument("--voice", default="af_heart", help="Kokoro voice id.")
    parser.add_argument(
        "--lang", default="a", help="Kokoro language code. `a` is American English."
    )
    parser.add_argument(
        "--confirm-rights",
        action="store_true",
        help="Confirm the source text may be generated and published.",
    )
    parser.add_argument(
        "--confirm-local-owned-use",
        action="store_true",
        help="Confirm local-only generation from owned-access text.",
    )
    parser.add_argument(
        "--rough-timings",
        action="store_true",
        help="Update manifest timings from generated segment audio duration.",
    )
    parser.add_argument(
        "--max-tts-chars",
        type=int,
        default=DEFAULT_MAX_TTS_CHARS,
        help="Maximum characters sent to one Kokoro pipeline call.",
    )
    return parser.parse_args()


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def root_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return repo_root() / path


def lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(root_path(path)))


def resolved_under(path: Path, parent: Path) -> bool:
    return (
        root_path(path)
        .resolve(strict=False)
        .is_relative_to(parent.resolve(strict=False))
    )


def lexical_under(path: Path, parent: Path) -> bool:
    return lexical_path(path).is_relative_to(lexical_path(parent))


def strictly_under(path: Path, parent: Path) -> bool:
    return lexical_under(path, parent) and resolved_under(path, parent)


def maybe_under(path: Path, parent: Path) -> bool:
    return lexical_under(path, parent) or resolved_under(path, parent)


def require_local_owned_use(
    manifest_path: Path, manifest: dict[str, Any], output: Path
) -> None:
    root = repo_root()
    local_root = root / "local"
    if not strictly_under(manifest_path, local_root):
        raise SystemExit("local-owned manifests must be under ignored `local/`")
    if output.is_absolute() or not strictly_under(output, local_root):
        raise SystemExit(
            "local-owned audio output must be a relative path under ignored `local/`"
        )
    if manifest.get("localOnly") is not True:
        raise SystemExit("local-owned manifests must set `localOnly` to true")
    if manifest.get("releaseAudio"):
        raise SystemExit("local-owned manifests must not contain `releaseAudio`")


def manifest_requires_local_mode(manifest_path: Path, manifest: dict[str, Any]) -> bool:
    return manifest.get("localOnly") is True or maybe_under(
        manifest_path, repo_root() / "local"
    )


def validate_segments(segments: Any) -> list[dict[str, Any]]:
    if not isinstance(segments, list) or not segments:
        raise SystemExit("manifest must contain a non-empty `segments` list")
    for segment in segments:
        text = segment.get("text") if isinstance(segment, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise SystemExit("each segment must contain non-empty `text`")
    return segments


def manifest_ref_path(manifest_path: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise SystemExit("segment chunk is missing a relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit("segment chunk path must stay under the manifest directory")
    resolved = (manifest_path.parent / path).resolve(strict=False)
    if not resolved.is_relative_to(manifest_path.parent.resolve(strict=False)):
        raise SystemExit("segment chunk path escapes the manifest directory")
    return manifest_path.parent / path


def load_segment_chunk(manifest_path: Path, ref: dict[str, Any]) -> list[dict[str, Any]]:
    chunk = load_manifest(manifest_ref_path(manifest_path, ref.get("path")))
    raw_segments = chunk if isinstance(chunk, list) else chunk.get("segments")
    return validate_segments(raw_segments)


def manifest_segments(manifest_path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    segments = manifest.get("segments")
    if segments is not None:
        return validate_segments(segments)
    refs = manifest.get("pages")
    if not isinstance(refs, list) or not refs:
        refs = manifest.get("segmentChunks")
    if not isinstance(refs, list) or not refs:
        raise SystemExit("manifest must contain `segments`, `pages`, or `segmentChunks`")
    loaded_segments = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise SystemExit("segment chunk ref must be an object")
        loaded_segments.extend(load_segment_chunk(manifest_path, ref))
    return validate_segments(loaded_segments)


def manifest_write_path(
    manifest_path: Path, manifest_out: Path | None, local_owned: bool
) -> Path:
    path = manifest_path if manifest_out is None else root_path(manifest_out)
    if local_owned:
        local_root = repo_root() / "local"
        requested_path = manifest_path if manifest_out is None else manifest_out
        if (
            manifest_out is not None
            and manifest_out.is_absolute()
            or not strictly_under(requested_path, local_root)
        ):
            raise SystemExit(
                "local-owned manifest output must be a relative path under ignored `local/`"
            )
    if manifest_out is not None and manifest_out.is_absolute() and not local_owned:
        raise SystemExit("refusing to write an absolute manifest output path")
    return path


def manifest_relative_path(manifest_path: Path, target: Path) -> str:
    path = Path(os.path.relpath(target, manifest_path.parent))
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit("chunk audio files must live under the manifest output directory")
    return path.as_posix()


def require_tools(out: Path) -> None:
    if out.suffix.lower() != ".wav" and shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is required for non-wav output")
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is required to read generated duration")


def audio_ext(value: str) -> str:
    ext = value if value.startswith(".") else f".{value}"
    if ext in {".wav", ".m4a", ".mp4", ".aac", ".mp3"}:
        return ext
    raise SystemExit("--chunk-ext must be one of wav, m4a, mp4, aac, or mp3")


def split_segment_chunks(
    segments: list[dict[str, Any]], chunk_segments: int, max_chunks: int | None
) -> list[list[dict[str, Any]]]:
    if chunk_segments < 1:
        raise SystemExit("--chunk-segments must be at least 1")
    if max_chunks is not None and max_chunks < 1:
        raise SystemExit("--max-chunks must be at least 1")
    chunks = [
        segments[index : index + chunk_segments]
        for index in range(0, len(segments), chunk_segments)
    ]
    return chunks[:max_chunks] if max_chunks is not None else chunks


def split_tts_chunks(text: str, max_chars: int) -> list[str]:
    if max_chars < MIN_MAX_TTS_CHARS:
        raise SystemExit(f"--max-tts-chars must be at least {MIN_MAX_TTS_CHARS}")
    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        limit = max_chars + 1
        cuts = [
            remaining.rfind("\n\n", 0, limit),
            remaining.rfind("\n", 0, limit),
            remaining.rfind(". ", 0, limit) + 1,
            remaining.rfind("? ", 0, limit) + 1,
            remaining.rfind("! ", 0, limit) + 1,
            remaining.rfind(" ", 0, limit),
        ]
        cut = max(cut for cut in cuts if cut >= 0)
        if cut < max_chars // 2:
            cut = max_chars
        chunk = remaining[:cut].strip()
        if not chunk:
            raise SystemExit("could not split text into TTS chunks")
        chunks.append(chunk)
        remaining = remaining[cut:].strip()
    return chunks


def write_kokoro_wav(
    segments: list[dict[str, Any]], out: Path, lang: str, voice: str, max_tts_chars: int
) -> list[tuple[float, float]]:
    try:
        import soundfile as sf
        from kokoro import KPipeline
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "missing dependency; run with `uv run --with 'kokoro>=0.9.4' --with soundfile "
            "scripts/generate-kokoro-audio.py ...`"
        ) from exc

    pipeline = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M")
    wrote_audio = False
    current_frame = 0
    timings = []
    with sf.SoundFile(out, "w", samplerate=SAMPLE_RATE_HZ, channels=1) as wav:
        for segment in segments:
            text = segment["text"].strip()
            start_sec = current_frame / SAMPLE_RATE_HZ
            wrote_segment = False
            for chunk in split_tts_chunks(text, max_tts_chars):
                for _, _, audio in pipeline(chunk, voice=voice):
                    wav.write(audio)
                    current_frame += len(audio)
                    wrote_audio = True
                    wrote_segment = True
            if not wrote_segment:
                raise SystemExit("kokoro produced no audio for a segment")
            timings.append((start_sec, current_frame / SAMPLE_RATE_HZ))
    if not wrote_audio:
        raise SystemExit("kokoro produced no audio")
    return timings


def encode_audio(wav: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    suffix = out.suffix.lower()
    if suffix == ".wav":
        shutil.copyfile(wav, out)
        return
    codec = (
        ["-c:a", "aac", "-b:a", "96k"]
        if suffix in {".m4a", ".mp4", ".aac"}
        else ["-c:a", "libmp3lame", "-b:a", "128k"]
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(wav),
            *codec,
            str(out),
        ],
        check=True,
    )


def probe_duration_sec(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-hide_banner",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def apply_generated_timings(
    manifest: dict[str, Any],
    segments: list[dict[str, Any]],
    timings: list[tuple[float, float]],
    duration_sec: float,
    out: Path,
    voice: str,
    lang: str,
) -> None:
    if out.is_absolute():
        raise SystemExit("refusing to write an absolute audio path into the manifest")
    if len(timings) != len(segments):
        raise SystemExit("generated timing count does not match segment count")
    raw_duration_sec = timings[-1][1]
    if raw_duration_sec <= 0:
        raise SystemExit("generated audio duration is zero")
    scale = duration_sec / raw_duration_sec
    for index, segment in enumerate(segments):
        if index == len(segments) - 1:
            end_sec = duration_sec
        else:
            end_sec = timings[index][1] * scale
        segment["startSec"] = round(timings[index][0] * scale, 3)
        segment["endSec"] = round(end_sec, 3)
    manifest["segments"] = segments
    manifest.pop("pages", None)
    manifest.pop("segmentChunks", None)
    manifest["audio"] = str(out)
    manifest["durationSec"] = round(duration_sec, 3)
    manifest["voice"] = {
        "model": "hexgrad/Kokoro-82M",
        "tool": "kokoro",
        "voice": voice,
        "lang": lang,
        "timing": "generated segment audio duration",
    }


def apply_chunked_generated_timings(
    manifest: dict[str, Any],
    generated_segments: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    chunk_dir: Path,
    voice: str,
    lang: str,
) -> None:
    if chunk_dir.is_absolute():
        raise SystemExit("refusing to write an absolute chunk path into the manifest")
    if not chunks:
        raise SystemExit("generated no audio chunks")
    duration_sec = chunks[-1]["endSec"]
    manifest["segments"] = generated_segments
    manifest.pop("pages", None)
    manifest.pop("segmentChunks", None)
    manifest["audioChunks"] = chunks
    manifest.pop("audio", None)
    manifest["durationSec"] = round(duration_sec, 3)
    manifest["voice"] = {
        "model": "hexgrad/Kokoro-82M",
        "tool": "kokoro",
        "voice": voice,
        "lang": lang,
        "timing": "generated chunk segment audio duration",
        "chunkDir": str(chunk_dir),
    }


def generate_single_audio(args: argparse.Namespace, segments: list[dict[str, Any]]) -> None:
    manifest_path = root_path(args.manifest)
    manifest = load_manifest(manifest_path)
    manifest_out = args.out or Path(str(manifest.get("audio", "")))
    if not str(manifest_out):
        raise SystemExit("provide --out or set `audio` in the manifest")
    if args.confirm_local_owned_use:
        require_local_owned_use(manifest_path, manifest, manifest_out)
    audio_out = root_path(manifest_out)
    require_tools(audio_out)
    with tempfile.TemporaryDirectory(prefix="adiob-kokoro-") as tmp:
        wav = Path(tmp) / "audio.wav"
        timings = write_kokoro_wav(
            segments, wav, args.lang, args.voice, args.max_tts_chars
        )
        encode_audio(wav, audio_out)
    duration_sec = probe_duration_sec(audio_out)
    if args.rough_timings:
        apply_generated_timings(
            manifest, segments, timings, duration_sec, manifest_out, args.voice, args.lang
        )
        out_path = manifest_write_path(
            manifest_path, args.manifest_out, args.confirm_local_owned_use
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {audio_out} ({duration_sec:.3f}s)")


def generate_chunked_audio(
    args: argparse.Namespace, manifest: dict[str, Any], segments: list[dict[str, Any]]
) -> None:
    if args.out is not None:
        raise SystemExit("use either --out or --chunk-dir, not both")
    chunk_dir = args.chunk_dir
    if chunk_dir is None:
        raise SystemExit("provide --chunk-dir for chunked generation")
    manifest_path = root_path(args.manifest)
    if args.confirm_local_owned_use:
        require_local_owned_use(manifest_path, manifest, chunk_dir)
    ext = audio_ext(args.chunk_ext)
    chunks = split_segment_chunks(segments, args.chunk_segments, args.max_chunks)
    audio_dir = root_path(chunk_dir)
    require_tools(audio_dir / f"chunk-001{ext}")
    generated_segments: list[dict[str, Any]] = []
    chunk_refs: list[dict[str, Any]] = []
    manifest_target = manifest_write_path(
        manifest_path, args.manifest_out, args.confirm_local_owned_use
    )
    next_start_sec = 0.0
    segment_index = 0
    audio_dir.mkdir(parents=True, exist_ok=True)
    for chunk_index, chunk_segments in enumerate(chunks, start=1):
        relative_audio = chunk_dir / f"chunk-{chunk_index:03d}{ext}"
        audio_out = root_path(relative_audio)
        with tempfile.TemporaryDirectory(prefix="adiob-kokoro-") as tmp:
            wav = Path(tmp) / "audio.wav"
            timings = write_kokoro_wav(
                chunk_segments, wav, args.lang, args.voice, args.max_tts_chars
            )
            encode_audio(wav, audio_out)
        duration_sec = probe_duration_sec(audio_out)
        raw_duration_sec = timings[-1][1]
        if raw_duration_sec <= 0:
            raise SystemExit("generated audio chunk duration is zero")
        scale = duration_sec / raw_duration_sec
        for local_index, segment in enumerate(chunk_segments):
            segment_copy = dict(segment)
            if local_index == len(chunk_segments) - 1:
                end_sec = next_start_sec + duration_sec
            else:
                end_sec = next_start_sec + timings[local_index][1] * scale
            segment_copy["startSec"] = round(
                next_start_sec + timings[local_index][0] * scale, 3
            )
            segment_copy["endSec"] = round(end_sec, 3)
            generated_segments.append(segment_copy)
        chunk_ref = {
            "id": f"chunk-{chunk_index:03d}",
            "path": manifest_relative_path(manifest_target, audio_out),
            "startSec": round(next_start_sec, 3),
            "endSec": round(next_start_sec + duration_sec, 3),
            "durationSec": round(duration_sec, 3),
            "segmentStart": segment_index,
            "segmentCount": len(chunk_segments),
        }
        chunk_refs.append(chunk_ref)
        next_start_sec += duration_sec
        segment_index += len(chunk_segments)
        print(f"wrote {audio_out} ({duration_sec:.3f}s)")
    if args.rough_timings:
        apply_chunked_generated_timings(
            manifest, generated_segments, chunk_refs, chunk_dir, args.voice, args.lang
        )
        manifest_target.parent.mkdir(parents=True, exist_ok=True)
        manifest_target.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )


def main() -> None:
    args = parse_args()
    if args.confirm_rights and args.confirm_local_owned_use:
        raise SystemExit("choose one rights confirmation mode")
    if not args.confirm_rights and not args.confirm_local_owned_use:
        raise SystemExit(
            "pass --confirm-rights for publishable text or "
            "--confirm-local-owned-use for ignored local owned-book demos"
        )
    manifest_path = root_path(args.manifest)
    manifest = load_manifest(manifest_path)
    if args.confirm_rights and manifest_requires_local_mode(manifest_path, manifest):
        raise SystemExit(
            "local-owned manifests require --confirm-local-owned-use and ignored `local/` output"
        )
    segments = manifest_segments(manifest_path, manifest)
    if args.chunk_dir is not None:
        generate_chunked_audio(args, manifest, segments)
        return
    generate_single_audio(args, segments)


if __name__ == "__main__":
    main()
