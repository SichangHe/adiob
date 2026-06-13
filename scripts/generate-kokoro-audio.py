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
        help="Update manifest timings by text length after generation.",
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
    manifest_path: Path, manifest: dict[str, Any], out: Path
) -> None:
    root = repo_root()
    local_root = root / "local"
    if not strictly_under(manifest_path, local_root):
        raise SystemExit("local-owned manifests must be under ignored `local/`")
    if out.is_absolute() or not strictly_under(out, local_root):
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


def segment_text(manifest: dict[str, Any]) -> str:
    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        raise SystemExit("manifest must contain a non-empty `segments` list")
    texts = []
    for segment in segments:
        text = segment.get("text") if isinstance(segment, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise SystemExit("each segment must contain non-empty `text`")
        texts.append(text.strip())
    return "\n".join(texts)


def require_tools(out: Path) -> None:
    if out.suffix.lower() != ".wav" and shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is required for non-wav output")
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is required to read generated duration")


def write_kokoro_wav(text: str, out: Path, lang: str, voice: str) -> None:
    try:
        import soundfile as sf
        from kokoro import KPipeline
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "missing dependency; run with `uv run --with 'kokoro>=0.9.4' --with soundfile "
            "scripts/generate-kokoro-audio.py ...`"
        ) from exc

    pipeline = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M")
    generator = pipeline(text, voice=voice)
    wrote_audio = False
    with sf.SoundFile(out, "w", samplerate=SAMPLE_RATE_HZ, channels=1) as wav:
        for _, _, audio in generator:
            wav.write(audio)
            wrote_audio = True
    if not wrote_audio:
        raise SystemExit("kokoro produced no audio")


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


def apply_rough_timings(
    manifest: dict[str, Any], duration_sec: float, out: Path, voice: str, lang: str
) -> None:
    if out.is_absolute():
        raise SystemExit("refusing to write an absolute audio path into the manifest")
    segments = manifest["segments"]
    weights = [max(1, len(segment["text"].strip())) for segment in segments]
    total = sum(weights)
    start_sec = 0.0
    for index, segment in enumerate(segments):
        if index == len(segments) - 1:
            end_sec = duration_sec
        else:
            end_sec = start_sec + (duration_sec * weights[index] / total)
        segment["startSec"] = round(start_sec, 3)
        segment["endSec"] = round(end_sec, 3)
        start_sec = end_sec
    manifest["audio"] = str(out)
    manifest["durationSec"] = round(duration_sec, 3)
    manifest["voice"] = {
        "model": "hexgrad/Kokoro-82M",
        "tool": "kokoro",
        "voice": voice,
        "lang": lang,
        "timing": "rough text-length allocation",
    }


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
    text = segment_text(manifest)
    manifest_out = args.out or Path(str(manifest.get("audio", "")))
    if not str(manifest_out):
        raise SystemExit("provide --out or set `audio` in the manifest")
    if args.confirm_local_owned_use:
        require_local_owned_use(manifest_path, manifest, manifest_out)
    audio_out = root_path(manifest_out)
    require_tools(audio_out)
    with tempfile.TemporaryDirectory(prefix="adiob-kokoro-") as tmp:
        wav = Path(tmp) / "audio.wav"
        write_kokoro_wav(text, wav, args.lang, args.voice)
        encode_audio(wav, audio_out)
    duration_sec = probe_duration_sec(audio_out)
    if args.rough_timings:
        apply_rough_timings(manifest, duration_sec, manifest_out, args.voice, args.lang)
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    print(f"wrote {audio_out} ({duration_sec:.3f}s)")


if __name__ == "__main__":
    main()
