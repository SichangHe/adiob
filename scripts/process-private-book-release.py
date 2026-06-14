#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any


DEFAULT_PRIVATE_ROOT = Path("../adiob-private-artifacts")
DEFAULT_LOCAL_ROOT = Path("local/owned-books")
DEFAULT_REPO = "SichangHe/adiob"
DEFAULT_RELEASE_TAG = "audio-owned-chunks-v3"
DEFAULT_CHUNK_SEGMENTS = 48
DEFAULT_CHUNK_EXT = ".m4a"
DEFAULT_MAX_TTS_CHARS = 1800
CHUNK_TIMING_TOLERANCE_SEC = 0.05
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate, upload, and link release audio for private catalog books."
    )
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--local-root", type=Path, default=DEFAULT_LOCAL_ROOT)
    parser.add_argument("--book-id", action="append", default=[])
    parser.add_argument("--all-books", action="store_true")
    parser.add_argument("--all-published", action="store_true")
    parser.add_argument("-R", "--repo", default=DEFAULT_REPO)
    parser.add_argument("--release-tag", default=DEFAULT_RELEASE_TAG)
    parser.add_argument("--chunk-segments", type=int, default=DEFAULT_CHUNK_SEGMENTS)
    parser.add_argument("--chunk-ext", default=DEFAULT_CHUNK_EXT)
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--lang", default="a")
    parser.add_argument("--max-tts-chars", type=int, default=DEFAULT_MAX_TTS_CHARS)
    parser.add_argument("--max-chars", type=int, default=0)
    parser.add_argument("--start-line", type=int)
    parser.add_argument("--skip-front-matter", action="store_true")
    parser.add_argument("--include-front-matter", action="store_true")
    parser.add_argument("--clobber", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--confirm-rights",
        action="store_true",
        help="Confirm selected books and generated audio may be publicly distributed.",
    )
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def root_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return repo_root() / path


def require_private_root(path: Path) -> Path:
    private_root = root_path(path).resolve(strict=False)
    public_root = repo_root().resolve(strict=False)
    if private_root == public_root or private_root.is_relative_to(public_root):
        raise SystemExit("private artifact root must be outside the public repo")
    if private_root.is_symlink():
        raise SystemExit(f"refusing symlink private root: {private_root}")
    return private_root


def load_local_builder() -> ModuleType:
    path = repo_root() / "scripts" / "build-local-owned-demo.py"
    spec = importlib.util.spec_from_file_location("local_owned_builder", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def write_json(path: Path, value: dict[str, Any]) -> None:
    write_file(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def require_catalog_text(private_root: Path, book: dict[str, Any]) -> Path:
    value = book.get("text")
    book_id = book.get("id")
    if not isinstance(value, str) or not value:
        raise SystemExit(f"private catalog entry {book_id} is missing text")
    path = private_root / value
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(private_root.resolve(strict=False)):
        raise SystemExit(f"private catalog entry {book_id} has unsafe text path")
    if not path.is_file():
        raise SystemExit(f"missing private text artifact: {path}")
    return path


def selected_books(catalog: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    books = catalog.get("books")
    if not isinstance(books, list):
        raise SystemExit("private catalog must contain a books list")
    by_id = {book.get("id"): book for book in books if isinstance(book, dict)}
    ids = list(dict.fromkeys(args.book_id))
    if args.all_books:
        ids.extend(
            book["id"]
            for book in books
            if isinstance(book, dict) and isinstance(book.get("id"), str)
        )
    if args.all_published:
        ids.extend(
            book["id"]
            for book in books
            if isinstance(book, dict)
            and isinstance(book.get("id"), str)
            and book.get("publish") is True
        )
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise SystemExit("pass --book-id, --all-books, or --all-published")
    selected = []
    for book_id in ids:
        if SAFE_ID.fullmatch(book_id) is None:
            raise SystemExit(f"unsafe catalog book id: {book_id}")
        book = by_id.get(book_id)
        if book is None:
            raise SystemExit(f"unknown private catalog book id: {book_id}")
        selected.append(book)
    return selected


def chunk_ext(value: str) -> str:
    ext = value if value.startswith(".") else f".{value}"
    if ext not in {".wav", ".m4a", ".mp4", ".aac", ".mp3"}:
        raise SystemExit("--chunk-ext must be one of wav, m4a, mp4, aac, or mp3")
    return ext


def local_book_dir(args: argparse.Namespace, book_id: str) -> Path:
    return root_path(args.local_root) / book_id


def should_skip_front_matter(args: argparse.Namespace) -> bool:
    if args.skip_front_matter and args.include_front_matter:
        raise SystemExit("choose either --skip-front-matter or --include-front-matter")
    return args.skip_front_matter or (
        not args.include_front_matter and args.start_line is None
    )


def build_local_manifest(
    builder: ModuleType,
    args: argparse.Namespace,
    book: dict[str, Any],
    text_path: Path,
) -> Path:
    book_id = str(book["id"])
    out_dir = local_book_dir(args, book_id)
    out_rel = Path(os.path.relpath(out_dir, repo_root()))
    skip_front_matter = should_skip_front_matter(args)
    excerpt = builder.read_excerpt(
        text_path,
        args.max_chars,
        args.start_line,
        skip_front_matter,
    )
    segments = builder.rough_segments(builder.split_segments(excerpt))
    manifest = {
        "id": book_id,
        "title": str(book.get("title") or book_id),
        "author": str(book.get("author") or ""),
        "source": "Ignored local manifest built from a private artifact catalog entry.",
        "license": str(book.get("license") or ""),
        "localOnly": True,
        "cover": (out_rel / "cover.svg").as_posix(),
        "durationSec": segments[-1]["endSec"],
        "textProcessing": {
            "frontMatter": "skipped" if skip_front_matter else "included",
            "segmentUnit": "paragraph",
        },
        "segments": segments,
    }
    cover = builder.cover_svg(manifest["title"], manifest["author"])
    if not args.dry_run:
        write_json(out_dir / "manifest.json", manifest)
        write_file(out_dir / "cover.svg", cover)
    print(
        f"manifest {book_id}: segments={len(segments)} "
        f"chars={sum(len(segment['text']) for segment in segments)}"
    )
    return out_dir / "manifest.json"


def run_command(cmd: list[str], dry_run: bool) -> None:
    print("+", " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, cwd=repo_root(), check=True)


def generate_audio(args: argparse.Namespace, manifest: Path, book_id: str) -> None:
    rel_manifest = Path(os.path.relpath(manifest, repo_root()))
    rel_chunk_dir = Path(os.path.relpath(local_book_dir(args, book_id) / "chunks", repo_root()))
    cmd = [
        "uv",
        "run",
        "--with",
        "kokoro>=0.9.4",
        "--with",
        "soundfile",
        "scripts/generate-kokoro-audio.py",
        "--manifest",
        rel_manifest.as_posix(),
        "--chunk-dir",
        rel_chunk_dir.as_posix(),
        "--chunk-segments",
        str(args.chunk_segments),
        "--chunk-ext",
        chunk_ext(args.chunk_ext),
        "--voice",
        args.voice,
        "--lang",
        args.lang,
        "--max-tts-chars",
        str(args.max_tts_chars),
        "--confirm-local-owned-use",
        "--rough-timings",
        "--batch-segments",
    ]
    run_command(cmd, args.dry_run)


def canonical_repo(repo: str, dry_run: bool) -> str:
    if dry_run:
        return repo
    result = subprocess.run(
        ["gh", "repo", "view", repo, "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=repo_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def require_staged_release_repo(repo: str) -> None:
    if repo.lower() != DEFAULT_REPO.lower():
        raise SystemExit(
            f"staged release URLs must use {DEFAULT_REPO}; got {repo}"
        )


def release_url_prefix(repo: str, tag: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/"


def has_complete_release_chunks(
    manifest: dict[str, Any], repo: str, tag: str, assets: set[str] | None = None
) -> bool:
    generation = manifest.get("generation")
    if not isinstance(generation, dict) or generation.get("fullBook") is not True:
        return False
    chunks = manifest.get("audioChunks")
    segments = manifest.get("segments")
    if not isinstance(chunks, list) or not chunks:
        return False
    if not isinstance(segments, list) or not segments:
        return False
    previous_end_sec = 0.0
    prefix = release_url_prefix(repo, tag)
    for chunk in chunks:
        if not isinstance(chunk, dict):
            return False
        path = chunk.get("path")
        if not isinstance(path, str) or not path.startswith(prefix):
            return False
        if assets is not None and Path(path).name not in assets:
            return False
        try:
            start_sec = float(chunk.get("startSec", -1))
            end_sec = float(chunk.get("endSec", -1))
        except (TypeError, ValueError):
            return False
        if (
            abs(start_sec - previous_end_sec) > CHUNK_TIMING_TOLERANCE_SEC
            or end_sec <= start_sec
        ):
            return False
        previous_end_sec = end_sec
    last_segment = segments[-1]
    if not isinstance(last_segment, dict):
        return False
    try:
        last_end_sec = float(last_segment.get("endSec", -1))
    except (TypeError, ValueError):
        return False
    return abs(previous_end_sec - last_end_sec) <= CHUNK_TIMING_TOLERANCE_SEC


def existing_linked_manifest_complete(
    private_root: Path, book: dict[str, Any], repo: str, tag: str
) -> bool:
    generated = book.get("generated")
    book_id = str(book["id"])
    if not isinstance(generated, dict):
        return False
    path = private_root / f"generated/{book_id}/manifest.json"
    if generated.get("manifest") != f"generated/{book_id}/manifest.json":
        return False
    if not path.is_file():
        return False
    return has_complete_release_chunks(read_json(path), repo, tag, existing_release_assets(repo, tag))


def mark_generation(
    args: argparse.Namespace, manifest: dict[str, Any], book_id: str, repo: str
) -> None:
    segments = manifest.get("segments") or []
    manifest["generation"] = {
        "bookId": book_id,
        "fullBook": args.max_chars == 0 and args.start_line is None,
        "segmentCount": len(segments),
        "textChars": sum(
            len(str(segment.get("text", "")))
            for segment in segments
            if isinstance(segment, dict)
        ),
        "releaseRepo": repo,
        "releaseTag": args.release_tag,
        "timing": "batched rough segment timing",
    }


def existing_release_assets(repo: str, tag: str) -> set[str]:
    result = subprocess.run(
        [
            "gh",
            "release",
            "view",
            tag,
            "-R",
            repo,
            "--json",
            "assets",
            "-q",
            ".assets[].name",
        ],
        cwd=repo_root(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def upload_release_assets(
    args: argparse.Namespace,
    repo: str,
    book_id: str,
    manifest: dict[str, Any],
    manifest_path: Path,
) -> None:
    chunks = manifest.get("audioChunks")
    if not isinstance(chunks, list) or not chunks:
        raise SystemExit(f"generated manifest has no audioChunks: {book_id}")
    if args.dry_run:
        print(f"would upload {len(chunks)} chunks for {book_id}")
        return
    existing_assets = existing_release_assets(repo, args.release_tag)
    with tempfile.TemporaryDirectory(prefix=f"adiob-release-{book_id}-") as tmp:
        upload_files = []
        tmp_dir = Path(tmp)
        for index, chunk in enumerate(chunks, start=1):
            path = chunk.get("path") if isinstance(chunk, dict) else None
            if not isinstance(path, str) or not path:
                raise SystemExit(f"audio chunk is missing path: {book_id}")
            source = (manifest_path.parent / path).resolve(strict=False)
            if not source.is_file():
                raise SystemExit(f"missing generated audio chunk: {source}")
            asset_name = f"{book_id}-chunk-{index:03d}{source.suffix}"
            chunk["path"] = (
                f"{release_url_prefix(repo, args.release_tag)}{asset_name}"
            )
            if asset_name in existing_assets and not args.clobber:
                continue
            upload_path = tmp_dir / asset_name
            shutil.copyfile(source, upload_path)
            upload_files.append(upload_path)
        view = subprocess.run(
            ["gh", "release", "view", args.release_tag, "-R", repo],
            cwd=repo_root(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if view.returncode != 0:
            subprocess.run(
                [
                    "gh",
                    "release",
                    "create",
                    args.release_tag,
                    "-R",
                    repo,
                    "--title",
                    args.release_tag,
                    "--notes",
                    "Audiobook chunk assets for rights-cleared adiob content.",
                ],
                cwd=repo_root(),
                check=True,
            )
        if upload_files:
            upload_cmd = ["gh", "release", "upload", args.release_tag]
            upload_cmd.extend(str(path) for path in upload_files)
            if args.clobber:
                upload_cmd.append("--clobber")
            upload_cmd.extend(["-R", repo])
            subprocess.run(upload_cmd, cwd=repo_root(), check=True)
        else:
            print(f"release assets already exist for {book_id}")


def copy_private_generated(
    private_root: Path,
    catalog: dict[str, Any],
    book: dict[str, Any],
    manifest_path: Path,
    manifest: dict[str, Any],
    dry_run: bool,
) -> None:
    book_id = str(book["id"])
    target = private_root / "generated" / book_id
    chunks_dir = target / "chunks"
    if dry_run:
        print(f"would update private generated artifact: {target}")
        return
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)
    source_cover = manifest_path.parent / "cover.svg"
    target.mkdir(parents=True, exist_ok=True)
    if source_cover.is_file():
        shutil.copyfile(source_cover, target / "cover.svg")
    write_json(target / "manifest.json", manifest)
    book["generated"] = {
        "manifest": f"generated/{book_id}/manifest.json",
        "cover": f"generated/{book_id}/cover.svg",
    }
    write_json(private_root / "books.json", catalog)


def process_book(
    builder: ModuleType,
    args: argparse.Namespace,
    private_root: Path,
    catalog: dict[str, Any],
    repo: str,
    book: dict[str, Any],
) -> None:
    book_id = str(book["id"])
    if not args.force and existing_linked_manifest_complete(
        private_root, book, repo, args.release_tag
    ):
        print(f"skip {book_id}: complete linked release chunks already exist")
        return
    text_path = require_catalog_text(private_root, book)
    manifest_path = build_local_manifest(builder, args, book, text_path)
    generate_audio(args, manifest_path, book_id)
    if args.dry_run:
        return
    manifest = read_json(manifest_path)
    mark_generation(args, manifest, book_id, repo)
    upload_release_assets(args, repo, book_id, manifest, manifest_path)
    copy_private_generated(private_root, catalog, book, manifest_path, manifest, args.dry_run)
    duration = manifest.get("durationSec")
    chunks = len(manifest.get("audioChunks") or [])
    print(f"linked {book_id}: duration={duration} chunks={chunks}")


def main() -> None:
    args = parse_args()
    if not args.confirm_rights and not args.dry_run:
        raise SystemExit("pass --confirm-rights for public release audio generation")
    private_root = require_private_root(args.private_root)
    catalog_path = private_root / "books.json"
    if not catalog_path.is_file():
        raise SystemExit(f"missing private catalog: {catalog_path}")
    catalog = read_json(catalog_path)
    books = selected_books(catalog, args)
    repo = canonical_repo(args.repo, args.dry_run)
    require_staged_release_repo(repo)
    builder = load_local_builder()
    for book in books:
        process_book(builder, args, private_root, catalog, repo, book)


if __name__ == "__main__":
    main()
