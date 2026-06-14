#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_ARTIFACT_SUBDIR = "archive-cache-a17"
PUBLIC_BOOKS = {
    "the-elements-of-style": {
        "sourceFile": "The Elements of Style, William Strunk, Jr..pdf",
        "title": "The Elements of Style",
        "author": "William Strunk Jr.",
        "manifestId": "the-elements-of-style-local",
    },
    "walden": {
        "sourceFile": "Walden - Henry David Thoreau.pdf",
        "title": "Walden",
        "author": "Henry David Thoreau",
        "manifestId": "walden-local",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage selected private book artifacts into a Pages build."
    )
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--site-root", required=True, type=Path)
    parser.add_argument("--reader-path", required=True)
    parser.add_argument("--artifact-subdir", default=DEFAULT_ARTIFACT_SUBDIR)
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


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value):
        raise SystemExit(f"unsafe private book id: {value}")
    return value


def expected_generated_path(book_id: str, name: str) -> str:
    return f"generated/{book_id}/{name}"


def require_generated_path(book_id: str, generated: dict[str, Any], key: str, name: str) -> Path:
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
) -> dict[str, str]:
    book_id = safe_id(book["id"])
    allowed = PUBLIC_BOOKS.get(book_id)
    if allowed is None:
        raise SystemExit(f"book is not public-side allowlisted: {book_id}")
    if book.get("sourceFile") != allowed["sourceFile"]:
        raise SystemExit(f"book source file is not allowlisted: {book_id}")
    if book.get("title") != allowed["title"] or book.get("author") != allowed["author"]:
        raise SystemExit(f"book metadata is not allowlisted: {book_id}")
    generated = book.get("generated")
    if not isinstance(generated, dict):
        raise SystemExit(f"publishable book is missing generated artifacts: {book_id}")
    source_manifest = private_root / require_generated_path(
        book_id, generated, "manifest", "manifest.json"
    )
    source_audio = private_root / require_generated_path(
        book_id, generated, "audio", "demo.m4a"
    )
    source_cover = private_root / require_generated_path(
        book_id, generated, "cover", "cover.svg"
    )
    target_dir = site_root / artifact_subdir / book_id
    copy_file(source_audio, target_dir / "demo.m4a")
    copy_file(source_cover, target_dir / "cover.svg")
    manifest = read_json(source_manifest)
    if manifest.get("id") != allowed["manifestId"]:
        raise SystemExit(f"manifest id is not allowlisted: {book_id}")
    if manifest.get("title") != allowed["title"] or manifest.get("author") != allowed["author"]:
        raise SystemExit(f"manifest metadata is not allowlisted: {book_id}")
    public_manifest = public_manifest_from(book_id, allowed, book, manifest)
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


def public_manifest_from(
    book_id: str,
    allowed: dict[str, str],
    book: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
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
    return {
        "id": allowed["manifestId"],
        "title": allowed["title"],
        "author": allowed["author"],
        "source": "Private artifact workflow selected this demo.",
        "license": str(book.get("license") or manifest.get("license") or ""),
        "privateArtifactWorkflow": True,
        "audio": "demo.m4a",
        "cover": "cover.svg",
        "durationSec": float(manifest["durationSec"]),
        "segments": public_segments,
    }


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
    if private_catalog_path.is_file():
        private_catalog = read_json(private_catalog_path)
        for book in private_catalog.get("books", []):
            if not book.get("publish"):
                continue
            entry = stage_book(
                private_root,
                site_root,
                book,
                args.reader_path,
                args.artifact_subdir,
            )
            by_id[entry["id"]] = entry
    else:
        print("no private book catalog found; staging public catalog only")
    staged_catalog = {
        "defaultBook": public_catalog.get("defaultBook"),
        "books": list(by_id.values()),
    }
    reader_catalog_path = site_root / args.reader_path / "catalog.json"
    write_json(reader_catalog_path, staged_catalog)
    print(f"staged {len(staged_catalog['books'])} catalog entries")


if __name__ == "__main__":
    main()
