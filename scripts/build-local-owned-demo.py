#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_TEXT = Path(
    "owned-text/the-contrarian-peter-thiel-and-the-rise-of-the-silicon-valley-oligarchs.txt"
)
DEFAULT_OUT_DIR = Path("local/owned-books/the-contrarian")
DEFAULT_ID = "the-contrarian-local"
DEFAULT_TITLE = (
    "The Contrarian: Peter Thiel and the Rise of the Silicon Valley Oligarchs"
)
DEFAULT_AUTHOR = "Max Chafkin"
DEFAULT_MAX_CHARS = 0
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z])")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an ignored local adiob manifest from owned-access text."
    )
    parser.add_argument("--text", type=Path, default=DEFAULT_TEXT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--id", default=DEFAULT_ID)
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--author", default=DEFAULT_AUTHOR)
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help="Maximum source characters to include; 0 includes the whole text.",
    )
    parser.add_argument("--audio-name", default="demo.m4a")
    parser.add_argument(
        "--confirm-local-owned-use",
        action="store_true",
        help="Confirm this ignored demo is only for local use from owned-access text.",
    )
    return parser.parse_args()


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


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def require_boundary(text_path: Path, out_dir: Path) -> None:
    root = repo_root()
    if not strictly_under(text_path, root / "owned-text"):
        raise SystemExit("input text must be under ignored `owned-text/`")
    if not strictly_under(out_dir, root / "local"):
        raise SystemExit("local demo output must be under ignored `local/`")


def write_local_file(path: Path, text: str) -> None:
    root = repo_root()
    if not strictly_under(path, root / "local"):
        raise SystemExit(f"output must stay under ignored `local/`: {path}")
    if path.is_symlink():
        raise SystemExit(f"refusing to overwrite symlink output: {path}")
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def read_excerpt(path: Path, max_chars: int) -> str:
    if max_chars and max_chars < 600:
        raise SystemExit("--max-chars must be at least 600")
    chunks: list[str] = []
    n_chars = 0
    with path.open(encoding="utf-8") as source:
        for line in source:
            text = normalize_space(line)
            if not text:
                continue
            chunks.append(text)
            n_chars += len(text) + 1
            if max_chars and n_chars >= max_chars + 800:
                break
    excerpt = normalize_space(" ".join(chunks))
    if not excerpt:
        raise SystemExit("no text found in owned input")
    if not max_chars:
        return excerpt
    if len(excerpt) <= max_chars:
        return excerpt
    end = max(
        excerpt.rfind(".", 0, max_chars),
        excerpt.rfind("?", 0, max_chars),
        excerpt.rfind("!", 0, max_chars),
    )
    if end >= max(400, max_chars // 2):
        return excerpt[: end + 1]
    return excerpt[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "."


def split_segments(excerpt: str) -> list[str]:
    segments = [part.strip() for part in SENTENCE_BREAK.split(excerpt) if part.strip()]
    if not segments:
        raise SystemExit("excerpt did not produce segments")
    return segments


def rough_segments(texts: list[str]) -> list[dict[str, Any]]:
    start_sec = 0.0
    segments: list[dict[str, Any]] = []
    for index, text in enumerate(texts, start=1):
        end_sec = start_sec + 4.0
        segments.append(
            {
                "id": f"s{index:03d}",
                "startSec": round(start_sec, 3),
                "endSec": round(end_sec, 3),
                "text": text,
            }
        )
        start_sec = end_sec
    return segments


def web_path(path: Path) -> str:
    return path.as_posix()


def cover_svg(title: str, author: str) -> str:
    safe_title = html.escape(title)
    safe_author = html.escape(author)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="900" viewBox="0 0 900 900" role="img" aria-labelledby="title desc">
  <title id="title">{safe_title}</title>
  <desc id="desc">Local-only adiob demo cover.</desc>
  <rect width="900" height="900" fill="#102326"/>
  <rect x="56" y="56" width="788" height="788" fill="none" stroke="#d7a339" stroke-width="10"/>
  <text x="96" y="180" fill="#d7a339" font-family="Georgia, serif" font-size="42" letter-spacing="4">LOCAL DEMO</text>
  <foreignObject x="96" y="270" width="708" height="280">
    <div xmlns="http://www.w3.org/1999/xhtml" style="color:#ffffff;font-family:Georgia,serif;font-size:68px;line-height:0.98;font-weight:700">{safe_title}</div>
  </foreignObject>
  <text x="96" y="690" fill="#f5f8f7" font-family="Inter, Arial, sans-serif" font-size="40">{safe_author}</text>
  <text x="96" y="766" fill="#9fb3af" font-family="Inter, Arial, sans-serif" font-size="28">ignored local text and audio</text>
</svg>
"""


def build_manifest(
    args: argparse.Namespace, text_path: Path, out_dir_rel: Path
) -> dict[str, Any]:
    audio_path = out_dir_rel / args.audio_name
    cover_path = out_dir_rel / "cover.svg"
    segments = rough_segments(split_segments(read_excerpt(text_path, args.max_chars)))
    return {
        "id": args.id,
        "title": args.title,
        "author": args.author,
        "source": "Ignored local owned-access text. Not a public repository asset.",
        "license": "Local owned-book demo only. Do not publish the text, manifest, or generated audio.",
        "localOnly": True,
        "audio": web_path(audio_path),
        "cover": web_path(cover_path),
        "durationSec": segments[-1]["endSec"],
        "segments": segments,
    }


def main() -> None:
    args = parse_args()
    if not args.confirm_local_owned_use:
        raise SystemExit(
            "pass --confirm-local-owned-use for ignored local owned-book demos"
        )
    root = repo_root()
    text_path = args.text if args.text.is_absolute() else root / args.text
    out_dir = args.out_dir if args.out_dir.is_absolute() else root / args.out_dir
    require_boundary(text_path, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir_rel = out_dir.relative_to(root)
    manifest = build_manifest(args, text_path, out_dir_rel)
    manifest_path = out_dir / "manifest.json"
    write_local_file(manifest_path, json.dumps(manifest, indent=2) + "\n")
    write_local_file(
        out_dir / "cover.svg",
        cover_svg(args.title, args.author),
    )
    print(f"wrote {manifest_path.relative_to(root)}")


if __name__ == "__main__":
    main()
