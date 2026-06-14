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

DEFAULT_OUT_DIR = Path("local/owned-books/local-demo")
DEFAULT_ID = "local-demo"
DEFAULT_TITLE = "Local Demo"
DEFAULT_AUTHOR = ""
DEFAULT_MAX_CHARS = 0
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z])")
BODY_HEADING = re.compile(
    r"^(introduction|introductory|prologue|chapter\b|part\b|book\b|section\b|\d{1,3}\.)",
    re.IGNORECASE,
)
PAGE_NUMBER = re.compile(r"^(?:\d+|[ivxlcdm]+)$", re.IGNORECASE)
SPLIT_INITIAL_CAP = re.compile(r"\b([A-Z])\s+([A-Z]{4,})\b")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an ignored local adiob manifest from owned-access text."
    )
    parser.add_argument("--text", required=True, type=Path)
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
    parser.add_argument(
        "--start-line",
        type=int,
        help="1-based source line where manifest text starts.",
    )
    parser.add_argument(
        "--skip-front-matter",
        action="store_true",
        help="Start at the first introduction/prologue/chapter body heading.",
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


def clean_line(text: str) -> str:
    return SPLIT_INITIAL_CAP.sub(r"\1\2", normalize_space(text))


def paragraph_text(lines: list[str]) -> str:
    parts: list[str] = []
    for line in lines:
        if parts and parts[-1].endswith("-") and line[:1].islower():
            parts[-1] = parts[-1][:-1] + line
        else:
            parts.append(line)
    return clean_line(" ".join(parts))


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


def normalized_source_paragraphs(path: Path) -> list[tuple[int, str]]:
    paragraphs: list[tuple[int, str]] = []
    lines: list[str] = []
    start_line = 0

    def flush() -> None:
        nonlocal lines, start_line
        if lines:
            paragraphs.append((start_line, paragraph_text(lines)))
            lines = []
            start_line = 0

    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            text = clean_line(line)
            if not text or PAGE_NUMBER.fullmatch(text):
                flush()
                continue
            if not lines:
                start_line = line_number
            lines.append(text)
    flush()
    return paragraphs


def body_heading_index(paragraphs: list[tuple[int, str]]) -> int | None:
    for index, (_, paragraph) in enumerate(paragraphs):
        if not BODY_HEADING.search(paragraph):
            continue
        following = [text for _, text in paragraphs[index + 1 : index + 10]]
        if sum(len(text) >= 40 for text in following) >= 2:
            return index
    return None


def start_index(
    paragraphs: list[tuple[int, str]], start_line: int | None, skip_front_matter: bool
) -> int:
    if start_line is not None:
        if start_line < 1:
            raise SystemExit("--start-line must be at least 1")
        for index, (line_number, _) in enumerate(paragraphs):
            if line_number >= start_line:
                return index
        raise SystemExit("--start-line is after the end of the source text")
    if skip_front_matter:
        index = body_heading_index(paragraphs)
        if index is None:
            raise SystemExit("could not find an introduction/prologue/chapter body heading")
        return index
    return 0


def read_excerpt(
    path: Path, max_chars: int, start_line: int | None, skip_front_matter: bool
) -> str:
    if max_chars and max_chars < 600:
        raise SystemExit("--max-chars must be at least 600")
    chunks: list[str] = []
    n_chars = 0
    paragraphs = normalized_source_paragraphs(path)
    for _, text in paragraphs[start_index(paragraphs, start_line, skip_front_matter) :]:
        if max_chars and chunks and n_chars + len(text) + 2 > max_chars:
            break
        chunks.append(text)
        n_chars += len(text) + 2
        if max_chars and n_chars >= max_chars:
            break
    excerpt = "\n\n".join(chunks).strip()
    if not excerpt:
        raise SystemExit("no text found in owned input")
    return excerpt


def split_segments(excerpt: str) -> list[str]:
    paragraphs = [
        normalize_space(part) for part in re.split(r"\n{2,}", excerpt) if part.strip()
    ]
    segments = (
        paragraphs
        if len(paragraphs) > 1
        else [part.strip() for part in SENTENCE_BREAK.split(excerpt) if part.strip()]
    )
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
    segments = rough_segments(
        split_segments(
            read_excerpt(
                text_path,
                args.max_chars,
                args.start_line,
                args.skip_front_matter,
            )
        )
    )
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
