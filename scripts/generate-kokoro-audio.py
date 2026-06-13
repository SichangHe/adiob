#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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
        "--rough-timings",
        action="store_true",
        help="Update manifest timings by text length after generation.",
    )
    return parser.parse_args()


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
    if not args.confirm_rights:
        raise SystemExit(
            "pass --confirm-rights only for public-domain, permissively licensed, or user-provided text"
        )
    manifest = load_manifest(args.manifest)
    text = segment_text(manifest)
    out = args.out or Path(str(manifest.get("audio", "")))
    if not str(out):
        raise SystemExit("provide --out or set `audio` in the manifest")
    require_tools(out)
    with tempfile.TemporaryDirectory(prefix="adiob-kokoro-") as tmp:
        wav = Path(tmp) / "audio.wav"
        write_kokoro_wav(text, wav, args.lang, args.voice)
        encode_audio(wav, out)
    duration_sec = probe_duration_sec(out)
    if args.rough_timings:
        apply_rough_timings(manifest, duration_sec, out, args.voice, args.lang)
        args.manifest.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    print(f"wrote {out} ({duration_sec:.3f}s)")


if __name__ == "__main__":
    main()
