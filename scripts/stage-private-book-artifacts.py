#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import posixpath
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_ARTIFACT_SUBDIR = "archive-cache-a17"
DEFAULT_SEGMENT_PAGE_SIZE = 48
DEFAULT_TEXT_PAGE_CHARS = 12000
ROUGH_CHARS_PER_SEC = 13.0
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z])")
BODY_HEADING = re.compile(
    r"^(introduction|introductory|prologue|chapter\b|chapter\s+[ivxlcdm0-9]+)\b",
    re.IGNORECASE,
)
PAGE_HEADING = re.compile(
    r"^(introduction|prologue|epilogue|chapter\b|part\b|book\b|section\b|\d{1,3}\.)",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage selected private book artifacts into a Pages build."
    )
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--site-root", required=True, type=Path)
    parser.add_argument("--reader-path", required=True)
    parser.add_argument("--artifact-subdir", default=DEFAULT_ARTIFACT_SUBDIR)
    parser.add_argument(
        "--segment-page-size", type=int, default=DEFAULT_SEGMENT_PAGE_SIZE
    )
    parser.add_argument(
        "--text-page-chars", type=int, default=DEFAULT_TEXT_PAGE_CHARS
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as tmp:
        tmp.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as tmp:
        tmp.write(value)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value):
        raise SystemExit(f"unsafe private book id: {value}")
    return value


def expected_generated_path(book_id: str, name: str) -> str:
    return f"generated/{book_id}/{name}"


def require_generated_path(
    book_id: str, generated: dict[str, Any], key: str, name: str
) -> Path:
    expected = expected_generated_path(book_id, name)
    actual = generated.get(key)
    if actual != expected:
        raise SystemExit(
            f"private catalog entry {book_id} must set generated.{key} to {expected}"
        )
    return Path(expected)


def require_catalog_path(private_root: Path, book_id: str, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise SystemExit(f"private catalog entry {book_id} is missing text")
    path = private_root / value
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(private_root.resolve(strict=False)):
        raise SystemExit(f"private catalog entry {book_id} has unsafe text path")
    if not path.is_file():
        raise SystemExit(f"missing private artifact: {path}")
    return path


def copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"missing private artifact: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def split_chunks(items: list[Any], chunk_size: int) -> list[list[Any]]:
    if chunk_size < 1:
        raise SystemExit("chunk size must be at least 1")
    return [
        items[index : index + chunk_size] for index in range(0, len(items), chunk_size)
    ]


def write_page_chunks(
    target_dir: Path, pages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    page_dir = target_dir / "pages"
    if page_dir.exists():
        shutil.rmtree(page_dir)
    refs = []
    for index, page in enumerate(pages, start=1):
        segments = page["segments"]
        name = f"pages/page-{index:03d}.json"
        write_json(
            target_dir / name,
            {"schema": 1, "title": page["title"], "segments": segments},
        )
        refs.append(
            {
                "path": name,
                "title": page["title"],
                "count": len(segments),
                "startSec": segments[0]["startSec"],
                "endSec": segments[-1]["endSec"],
            }
        )
    return refs


def reader_relative(reader_path: str, target: str) -> str:
    rel = posixpath.relpath(target, start=reader_path.strip("/"))
    if rel.startswith("../"):
        return rel
    return f"./{rel}"


def stage_book(
    private_root: Path,
    site_root: Path,
    book: dict[str, Any],
    reader_path: str,
    artifact_subdir: str,
    segment_page_size: int,
    text_page_chars: int,
) -> dict[str, str]:
    book_id = safe_id(book["id"])
    target_dir = site_root / artifact_subdir / book_id
    generated = book.get("generated")
    if isinstance(generated, dict):
        public_manifest = stage_generated_book(
            private_root, target_dir, book_id, book, generated, segment_page_size
        )
    else:
        public_manifest = stage_text_book(
            private_root, target_dir, book_id, book, text_page_chars
        )
    write_json(target_dir / "manifest.json", public_manifest)
    return {
        "id": book_id,
        "title": public_manifest["title"],
        "author": public_manifest["author"],
        "manifest": reader_relative(
            reader_path,
            f"{artifact_subdir}/{book_id}/manifest.json",
        ),
    }


def stage_generated_book(
    private_root: Path,
    target_dir: Path,
    book_id: str,
    book: dict[str, Any],
    generated: dict[str, Any],
    segment_page_size: int,
) -> dict[str, Any]:
    source_manifest = private_root / require_generated_path(
        book_id, generated, "manifest", "manifest.json"
    )
    source_audio = private_root / require_generated_path(
        book_id, generated, "audio", "demo.m4a"
    )
    copy_file(source_audio, target_dir / "demo.m4a")
    manifest = read_json(source_manifest)
    write_text(
        target_dir / "cover.svg",
        cover_svg(
            str(book.get("title") or manifest.get("title") or book_id),
            str(book.get("author") or manifest.get("author") or ""),
        ),
    )
    segments = public_segments_from(book_id, manifest)
    pages = segment_pages_from(segments, segment_page_size)
    refs = write_page_chunks(target_dir, pages)
    return public_manifest_from(
        book_id,
        book,
        manifest,
        refs,
        audio="demo.m4a",
        cover="cover.svg",
        timing="generated audio segment timing",
    )


def stage_text_book(
    private_root: Path,
    target_dir: Path,
    book_id: str,
    book: dict[str, Any],
    text_page_chars: int,
) -> dict[str, Any]:
    text_path = require_catalog_path(private_root, book_id, book.get("text"))
    pages = text_pages_from(text_path, text_page_chars)
    refs = write_page_chunks(target_dir, pages)
    cover = cover_svg(str(book.get("title") or book_id), str(book.get("author") or ""))
    write_text(target_dir / "cover.svg", cover)
    return public_manifest_from(
        book_id,
        book,
        {},
        refs,
        audio=None,
        cover="cover.svg",
        timing="rough text timing",
    )


def public_segments_from(book_id: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        raise SystemExit(f"manifest has no public segments: {book_id}")
    public_segments = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise SystemExit(f"manifest segment is not an object: {book_id}")
        public_segments.append(
            {
                "id": str(segment["id"]),
                "startSec": float(segment["startSec"]),
                "endSec": float(segment["endSec"]),
                "text": str(segment["text"]),
            }
        )
    return public_segments


def segment_page_title(segments: list[dict[str, Any]], index: int) -> str:
    first = segments[0]["text"].strip()
    words = first.split()
    if PAGE_HEADING.search(first):
        return " ".join(words[:10])
    return f"Page {index}"


def segment_pages_from(
    segments: list[dict[str, Any]], segment_page_size: int
) -> list[dict[str, Any]]:
    pages = []
    for index, chunk in enumerate(split_chunks(segments, segment_page_size), start=1):
        pages.append(
            {
                "title": segment_page_title(chunk, index),
                "segments": chunk,
            }
        )
    return pages


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalized_lines(path: Path) -> list[str]:
    with path.open(encoding="utf-8") as source:
        return [text for line in source if (text := normalize_space(line))]


def is_body_heading(line: str) -> bool:
    return (
        BODY_HEADING.search(line) is not None
        and len(line) <= 90
        and re.search(r"\.{4,}", line) is None
    )


def is_page_heading(line: str) -> bool:
    return (
        PAGE_HEADING.search(line) is not None
        and len(line) <= 120
        and re.search(r"\.{4,}", line) is None
    )


def body_start_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if not is_body_heading(line):
            continue
        following = lines[index + 1 : index + 16]
        if sum(len(text) >= 40 for text in following) >= 8:
            return index
    return 0


def split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in SENTENCE_BREAK.split(text) if part.strip()]
    return parts or [text]


def rough_segments(
    texts: list[str], page_index: int, start_sec: float
) -> tuple[list[dict[str, Any]], float]:
    current_sec = start_sec
    segments = []
    for index, text in enumerate(texts, start=1):
        duration_sec = max(2.0, len(text) / ROUGH_CHARS_PER_SEC)
        end_sec = current_sec + duration_sec
        segments.append(
            {
                "id": f"p{page_index:03d}-s{index:03d}",
                "startSec": round(current_sec, 3),
                "endSec": round(end_sec, 3),
                "text": text,
            }
        )
        current_sec = end_sec
    return segments, current_sec


def title_from_lines(lines: list[str], index: int) -> str:
    for line in lines[:4]:
        if is_page_heading(line):
            return line[:80]
    return f"Page {index}"


def text_pages_from(path: Path, page_chars: int) -> list[dict[str, Any]]:
    if page_chars < 1000:
        raise SystemExit("--text-page-chars must be at least 1000")
    lines = normalized_lines(path)
    if not lines:
        raise SystemExit(f"private text is empty: {path}")
    lines = lines[body_start_index(lines) :]
    page_lines: list[str] = []
    page_char_count = 0
    raw_pages: list[list[str]] = []
    for line in lines:
        starts_page = is_page_heading(line) and page_char_count >= 1000
        too_large = page_char_count + len(line) + 1 > page_chars and page_lines
        if starts_page or too_large:
            raw_pages.append(page_lines)
            page_lines = []
            page_char_count = 0
        page_lines.append(line)
        page_char_count += len(line) + 1
    if page_lines:
        raw_pages.append(page_lines)
    pages = []
    current_sec = 0.0
    for index, lines_for_page in enumerate(raw_pages, start=1):
        text = normalize_space(" ".join(lines_for_page))
        segments, current_sec = rough_segments(
            split_sentences(text), index, current_sec
        )
        pages.append(
            {
                "title": title_from_lines(lines_for_page, index),
                "segments": segments,
            }
        )
    return pages


def cover_svg(title: str, author: str) -> str:
    safe_title = html.escape(title)
    safe_author = html.escape(author)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="900" viewBox="0 0 900 900" role="img" aria-labelledby="title desc">
  <title id="title">{safe_title}</title>
  <desc id="desc">adiob generated reader cover.</desc>
  <rect width="900" height="900" fill="#102326"/>
  <rect x="56" y="56" width="788" height="788" fill="none" stroke="#d7a339" stroke-width="10"/>
  <text x="96" y="180" fill="#d7a339" font-family="Georgia, serif" font-size="42" letter-spacing="4">ADIOB</text>
  <foreignObject x="96" y="270" width="708" height="280">
    <div xmlns="http://www.w3.org/1999/xhtml" style="color:#ffffff;font-family:Georgia,serif;font-size:68px;line-height:0.98;font-weight:700">{safe_title}</div>
  </foreignObject>
  <text x="96" y="690" fill="#f5f8f7" font-family="Inter, Arial, sans-serif" font-size="40">{safe_author}</text>
  <text x="96" y="766" fill="#9fb3af" font-family="Inter, Arial, sans-serif" font-size="28">processed book text</text>
</svg>
"""


def public_manifest_from(
    book_id: str,
    book: dict[str, Any],
    manifest: dict[str, Any],
    page_refs: list[dict[str, Any]],
    audio: str | None,
    cover: str,
    timing: str,
) -> dict[str, Any]:
    public_manifest = {
        "id": str(manifest.get("id") or f"{book_id}-reader"),
        "title": str(book.get("title") or manifest.get("title") or book_id),
        "author": str(book.get("author") or manifest.get("author") or ""),
        "source": "Private artifact workflow selected this reader entry.",
        "license": "",
        "privateArtifactWorkflow": True,
        "cover": cover,
        "durationSec": round(float(page_refs[-1]["endSec"]), 3),
        "segmentCount": sum(ref["count"] for ref in page_refs),
        "pageCount": len(page_refs),
        "pages": page_refs,
        "timing": timing,
    }
    if audio is not None:
        public_manifest["audio"] = audio
    return public_manifest


def main() -> None:
    args = parse_args()
    private_root = args.private_root.resolve(strict=False)
    site_root = args.site_root.resolve(strict=True)
    public_catalog_path = site_root / "data" / "books.json"
    public_catalog = read_json(public_catalog_path)
    books = public_catalog.get("books")
    if not isinstance(books, list):
        raise SystemExit("public catalog must contain a books list")
    by_id = {entry["id"]: entry for entry in books}
    private_catalog_path = private_root / "books.json"
    staged_private_entries = []
    staged_audio_entries = []
    if private_catalog_path.is_file():
        private_catalog = read_json(private_catalog_path)
        for book in private_catalog.get("books", []):
            entry = stage_book(
                private_root,
                site_root,
                book,
                args.reader_path,
                args.artifact_subdir,
                args.segment_page_size,
                args.text_page_chars,
            )
            staged_private_entries.append(entry["id"])
            if isinstance(book.get("generated"), dict):
                staged_audio_entries.append(entry["id"])
            by_id[entry["id"]] = entry
    else:
        print("no private book catalog found; staging public catalog only")
    staged_catalog = {
        "defaultBook": (
            staged_audio_entries[0]
            if staged_audio_entries
            else staged_private_entries[0]
            if staged_private_entries
            else public_catalog.get("defaultBook")
        ),
        "books": list(by_id.values()),
    }
    reader_catalog_path = site_root / args.reader_path / "catalog.json"
    write_json(reader_catalog_path, staged_catalog)
    print(f"staged {len(staged_catalog['books'])} catalog entries")


if __name__ == "__main__":
    main()
