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
from urllib.parse import urlparse


DEFAULT_ARTIFACT_SUBDIR = "archive-cache-a17"
DEFAULT_SEGMENT_PAGE_SIZE = 48
CHUNK_TIMING_TOLERANCE_SEC = 0.05
RELEASE_AUDIO_HOST = "github.com"
RELEASE_AUDIO_PATH_PREFIX = "/SichangHe/adiob/releases/download/"
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


def copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"missing private artifact: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def release_audio_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != RELEASE_AUDIO_HOST
        or not parsed.path.startswith(RELEASE_AUDIO_PATH_PREFIX)
        or parsed.query
        or parsed.fragment
        or any(part in {"", ".", ".."} for part in parsed.path.split("/")[1:])
    ):
        return None
    return value


def safe_artifact_subdir(site_root: Path, value: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise SystemExit("artifact subdir must be a safe relative directory")
    target = (site_root / path).resolve(strict=False)
    root = site_root.resolve(strict=False)
    if target == root or not target.is_relative_to(root):
        raise SystemExit("artifact subdir must stay under site root")
    return path.as_posix()


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
) -> tuple[dict[str, str], bool]:
    book_id = safe_id(book["id"])
    target_dir = site_root / artifact_subdir / book_id
    generated = book.get("generated")
    if not isinstance(generated, dict) or not generated_has_full_release_audio(
        private_root, book_id, generated
    ):
        raise SystemExit(
            f"published private catalog entry {book_id} is missing full release audio"
        )
    public_manifest = stage_generated_book(
        private_root, target_dir, book_id, book, generated, segment_page_size
    )
    write_json(target_dir / "manifest.json", public_manifest)
    return (
        {
            "id": book_id,
            "title": public_manifest["title"],
            "author": public_manifest["author"],
            "manifest": reader_relative(
                reader_path,
                f"{artifact_subdir}/{book_id}/manifest.json",
            ),
        },
        "audio" in public_manifest or "audioChunks" in public_manifest,
    )


def generated_has_full_release_audio(
    private_root: Path, book_id: str, generated: dict[str, Any]
) -> bool:
    try:
        source_manifest = private_root / require_generated_path(
            book_id, generated, "manifest", "manifest.json"
        )
    except SystemExit:
        return False
    if not source_manifest.is_file():
        return False
    manifest = read_json(source_manifest)
    generation = manifest.get("generation")
    if not isinstance(generation, dict) or generation.get("fullBook") is not True:
        return False
    try:
        segments = public_segments_from(book_id, manifest)
        chunks = public_audio_chunks_from(book_id, source_manifest.parent, None, manifest)
    except (KeyError, IndexError, TypeError, ValueError, SystemExit):
        return False
    if not chunks or not segments:
        return False
    return abs(chunks[-1]["endSec"] - segments[-1]["endSec"]) <= CHUNK_TIMING_TOLERANCE_SEC


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
    manifest = read_json(source_manifest)
    audio_chunks = public_audio_chunks_from(
        book_id, source_manifest.parent, target_dir, manifest
    )
    audio = None
    if not audio_chunks:
        source_audio = private_root / require_generated_path(
            book_id, generated, "audio", "demo.m4a"
        )
        copy_file(source_audio, target_dir / "demo.m4a")
        audio = "demo.m4a"
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
        audio=audio,
        audio_chunks=audio_chunks,
        cover="cover.svg",
        timing=(
            "generated chunk audio segment timing"
            if audio_chunks
            else "generated audio segment timing"
        ),
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


def public_audio_chunks_from(
    book_id: str, source_dir: Path, target_dir: Path | None, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    chunks = manifest.get("audioChunks")
    if chunks is None:
        return []
    if not isinstance(chunks, list) or not chunks:
        raise SystemExit(f"manifest audioChunks must be a non-empty list: {book_id}")
    public_chunks = []
    previous_end_sec = 0.0
    for index, chunk in enumerate(chunks, start=1):
        if not isinstance(chunk, dict):
            raise SystemExit(f"manifest audio chunk is not an object: {book_id}")
        url = release_audio_url(chunk.get("path"))
        if url is None:
            raise SystemExit(f"manifest audio chunk is not a release URL: {book_id}")
        public_path = url
        start_sec = float(chunk["startSec"])
        end_sec = float(chunk["endSec"])
        if (
            abs(start_sec - previous_end_sec) > CHUNK_TIMING_TOLERANCE_SEC
            or end_sec <= start_sec
        ):
            raise SystemExit(f"manifest audio chunk timing is invalid: {book_id}")
        public_chunks.append(
            {
                "id": str(chunk.get("id") or f"chunk-{index:03d}"),
                "path": public_path,
                "startSec": start_sec,
                "endSec": end_sec,
                "durationSec": round(end_sec - start_sec, 3),
                "segmentStart": int(chunk.get("segmentStart", 0)),
                "segmentCount": int(chunk.get("segmentCount", 0)),
            }
        )
        previous_end_sec = end_sec
    return public_chunks


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
    audio_chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    page_duration_sec = float(page_refs[-1]["endSec"])
    duration_sec = page_duration_sec
    if audio_chunks:
        duration_sec = float(audio_chunks[-1]["endSec"])
        if abs(duration_sec - page_duration_sec) > CHUNK_TIMING_TOLERANCE_SEC:
            raise SystemExit("audio chunk timing does not match segment timing")
    public_manifest = {
        "id": str(manifest.get("id") or f"{book_id}-reader"),
        "title": str(book.get("title") or manifest.get("title") or book_id),
        "author": str(book.get("author") or manifest.get("author") or ""),
        "source": "Private artifact workflow selected this reader entry.",
        "license": "",
        "privateArtifactWorkflow": True,
        "cover": cover,
        "durationSec": round(duration_sec, 3),
        "segmentCount": sum(ref["count"] for ref in page_refs),
        "pageCount": len(page_refs),
        "pages": page_refs,
        "timing": timing,
    }
    if audio is not None:
        public_manifest["audio"] = audio
    if audio_chunks:
        public_manifest["audioChunks"] = audio_chunks
    return public_manifest


def main() -> None:
    args = parse_args()
    private_root = args.private_root.resolve(strict=False)
    site_root = args.site_root.resolve(strict=True)
    artifact_subdir = safe_artifact_subdir(site_root, args.artifact_subdir)
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
        artifact_root = site_root / artifact_subdir
        if artifact_root.exists():
            shutil.rmtree(artifact_root)
        private_catalog = read_json(private_catalog_path)
        for book in private_catalog.get("books", []):
            if not isinstance(book, dict) or book.get("publish") is not True:
                continue
            entry, has_audio = stage_book(
                private_root,
                site_root,
                book,
                args.reader_path,
                artifact_subdir,
                args.segment_page_size,
            )
            staged_private_entries.append(entry["id"])
            if has_audio:
                staged_audio_entries.append(entry["id"])
            by_id[entry["id"]] = entry
    else:
        print("no private book catalog found; staging public catalog only")
    staged_books = list(by_id.values())
    staged_catalog = {
        "defaultBook": (
            staged_audio_entries[0]
            if staged_audio_entries
            else staged_private_entries[0]
            if staged_private_entries
            else public_catalog.get("defaultBook")
        ),
        "books": staged_books,
    }
    reader_catalog_path = site_root / args.reader_path / "catalog.json"
    write_json(reader_catalog_path, staged_catalog)
    print(f"staged {len(staged_books)} catalog entries")


if __name__ == "__main__":
    main()
