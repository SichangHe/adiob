#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple


DEFAULT_PRIVATE_ROOT = Path("../adiob-private-artifacts")
DEFAULT_LOCAL_ROOT = Path("local/owned-books")
DEFAULT_REPO = "SichangHe/adiob"
DEFAULT_RELEASE_TAG = "audio-owned-chunks-v7"
DEFAULT_CHUNK_SEGMENTS = 48
DEFAULT_CHUNK_EXT = ".m4a"
DEFAULT_MAX_TTS_CHARS = 1800
CHUNK_TIMING_TOLERANCE_SEC = 0.05
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
SAFE_RELEASE_TAG = re.compile(r"[A-Za-z0-9._-]+")
SHA256 = re.compile(r"[0-9a-f]{64}")


class ReleaseAsset(NamedTuple):
    name: str
    size_bytes: int
    sha256: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate, upload, and link release audio for private catalog books."
    )
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--catalog", default="books.json")
    parser.add_argument("--local-root", type=Path, default=DEFAULT_LOCAL_ROOT)
    parser.add_argument("--book-id", action="append", default=[])
    parser.add_argument("--all-books", action="store_true")
    parser.add_argument("--all-published", action="store_true")
    parser.add_argument("--exclude-publish-opt-out", action="store_true")
    parser.add_argument("-R", "--repo", default=DEFAULT_REPO)
    parser.add_argument("--release-tag")
    parser.add_argument("--chunk-segments", type=int, default=DEFAULT_CHUNK_SEGMENTS)
    parser.add_argument("--chunk-ext", default=DEFAULT_CHUNK_EXT)
    parser.add_argument("--voice")
    parser.add_argument("--lang")
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


def private_catalog_path(private_root: Path, value: str) -> Path:
    path = Path(value)
    if path.name != value or path.suffix != ".json":
        raise SystemExit("--catalog must be a JSON filename at the private repo root")
    return private_root / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def narration_sha256(segments: list[dict[str, Any]]) -> str:
    value = [
        {"id": str(segment.get("id", "")), "text": str(segment.get("text", ""))}
        for segment in segments
    ]
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def narration_matches_source(
    builder: ModuleType, text_path: Path, manifest: dict[str, Any]
) -> bool:
    manifest_segments = manifest.get("segments")
    if not isinstance(manifest_segments, list):
        return False
    text_processing = manifest.get("textProcessing")
    skip_front_matter = (
        isinstance(text_processing, dict)
        and text_processing.get("frontMatter") == "skipped"
    )
    try:
        excerpt = builder.read_excerpt(text_path, 0, None, skip_front_matter)
    except SystemExit:
        return False
    source_segments = builder.rough_segments(builder.split_segments(excerpt))
    source_texts = [str(segment.get("text", "")) for segment in source_segments]
    narration_texts = [str(segment.get("text", "")) for segment in manifest_segments]
    return bool(narration_texts) and source_texts == narration_texts


def expected_segments(
    builder: ModuleType, args: argparse.Namespace, text_path: Path
) -> tuple[list[dict[str, Any]], bool]:
    skip_front_matter = should_skip_front_matter(args)
    try:
        excerpt = builder.read_excerpt(
            text_path, args.max_chars, args.start_line, skip_front_matter
        )
    except SystemExit:
        if args.skip_front_matter or args.include_front_matter or args.start_line:
            raise
        skip_front_matter = False
        excerpt = builder.read_excerpt(text_path, args.max_chars, None, False)
    return builder.rough_segments(builder.split_segments(excerpt)), skip_front_matter


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


def selected_books(
    catalog: dict[str, Any], args: argparse.Namespace
) -> list[dict[str, Any]]:
    books = catalog.get("books")
    if not isinstance(books, list):
        raise SystemExit("private catalog must contain a books list")
    by_id = {book.get("id"): book for book in books if isinstance(book, dict)}
    ids = list(dict.fromkeys(args.book_id))
    if args.all_books:
        ids.extend(
            book["id"]
            for book in books
            if isinstance(book, dict)
            and isinstance(book.get("id"), str)
            and (not args.exclude_publish_opt_out or book.get("publish") is not False)
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
    segments: list[dict[str, Any]],
    skip_front_matter: bool,
) -> Path:
    book_id = str(book["id"])
    out_dir = local_book_dir(args, book_id)
    out_rel = Path(os.path.relpath(out_dir, repo_root()))
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


def generate_audio(
    args: argparse.Namespace,
    manifest: Path,
    book: dict[str, Any],
) -> None:
    book_id = str(book["id"])
    voice, lang = book_voice_lang(args, book)
    rel_manifest = Path(os.path.relpath(manifest, repo_root()))
    rel_chunk_dir = Path(
        os.path.relpath(local_book_dir(args, book_id) / "chunks", repo_root())
    )
    cmd = [
        "uv",
        "run",
        *tts_dependency_args(lang),
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
        voice,
        "--lang",
        lang,
        "--max-tts-chars",
        str(args.max_tts_chars),
        "--confirm-local-owned-use",
        "--rough-timings",
        "--batch-segments",
    ]
    run_command(cmd, args.dry_run)


def tts_dependency_args(lang: str) -> list[str]:
    dependencies = ["kokoro>=0.9.4", "soundfile"]
    if lang == "z":
        dependencies.append("misaki[zh]>=0.9.4")
    return [item for dependency in dependencies for item in ("--with", dependency)]


def book_voice_lang(args: argparse.Namespace, book: dict[str, Any]) -> tuple[str, str]:
    return (
        str(args.voice or book.get("voice") or "af_heart"),
        str(args.lang or book.get("language") or "a"),
    )


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
        raise SystemExit(f"staged release URLs must use {DEFAULT_REPO}; got {repo}")


def release_url_prefix(repo: str, tag: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/"


def book_release_tag(
    args: argparse.Namespace, private_root: Path, book: dict[str, Any]
) -> str:
    candidates = [args.release_tag]
    release = book.get("release")
    if isinstance(release, dict):
        candidates.append(release.get("tag"))
    book_id = str(book["id"])
    manifest_path = private_root / f"generated/{book_id}/manifest.json"
    if manifest_path.is_file():
        generation = read_json(manifest_path).get("generation")
        if isinstance(generation, dict):
            candidates.append(generation.get("releaseTag"))
    candidates.append(DEFAULT_RELEASE_TAG)
    tag = next(value for value in candidates if isinstance(value, str) and value)
    if SAFE_RELEASE_TAG.fullmatch(tag) is None:
        raise SystemExit(f"unsafe release tag for {book_id}: {tag}")
    return tag


def valid_release_asset(asset: ReleaseAsset | None) -> bool:
    return (
        asset is not None
        and asset.size_bytes > 0
        and SHA256.fullmatch(asset.sha256) is not None
    )


def manifest_audio_shape_valid(manifest: dict[str, Any]) -> bool:
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
    for chunk in chunks:
        if not isinstance(chunk, dict):
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


def expected_asset_name(book_id: str, index: int, chunk: dict[str, Any]) -> str:
    path = chunk.get("path")
    suffix = Path(path).suffix if isinstance(path, str) else ""
    if suffix not in {".wav", ".m4a", ".mp4", ".aac", ".mp3"}:
        raise SystemExit(
            f"audio chunk has an unsafe extension: {book_id} chunk {index}"
        )
    return f"{book_id}-chunk-{index:03d}{suffix}"


def reconcile_existing_manifest(
    builder: ModuleType,
    args: argparse.Namespace,
    private_root: Path,
    catalog_path: Path,
    catalog: dict[str, Any],
    book: dict[str, Any],
    text_path: Path,
    repo: str,
    tag: str,
    assets: dict[str, ReleaseAsset] | None,
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
    manifest = read_json(path)
    manifest_segments = manifest.get("segments")
    if (
        not manifest_audio_shape_valid(manifest)
        or not isinstance(manifest_segments, list)
        or assets is None
    ):
        return False
    source_sha256 = sha256_file(text_path)
    manifest_narration_sha256 = narration_sha256(manifest_segments)
    generation = manifest["generation"]
    stored_source_sha256 = generation.get("sourceTextSha256")
    stored_narration_sha256 = generation.get("narrationTextSha256")
    if stored_source_sha256 not in (None, source_sha256):
        return False
    if stored_narration_sha256 not in (None, manifest_narration_sha256):
        raise SystemExit(f"generated manifest narration identity changed: {book_id}")
    if stored_source_sha256 is None and not narration_matches_source(
        builder, text_path, manifest
    ):
        return False
    chunks = manifest["audioChunks"]
    prefix = release_url_prefix(repo, tag)
    changed = False
    for index, chunk in enumerate(chunks, start=1):
        asset_name = expected_asset_name(book_id, index, chunk)
        asset = assets.get(asset_name)
        if not valid_release_asset(asset):
            return False
        assert asset is not None
        stored_sha256 = chunk.get("sha256")
        stored_size = chunk.get("sizeBytes")
        if stored_sha256 not in (None, asset.sha256) or stored_size not in (
            None,
            asset.size_bytes,
        ):
            raise SystemExit(
                f"release asset conflicts with recorded checksum: {repo} {tag} {asset_name}"
            )
        expected_path = f"{prefix}{asset_name}"
        for key, value in (
            ("path", expected_path),
            ("sha256", asset.sha256),
            ("sizeBytes", asset.size_bytes),
        ):
            if chunk.get(key) != value:
                chunk[key] = value
                changed = True
    expected_generation = {
        "releaseRepo": repo,
        "releaseTag": tag,
        "sourceTextSha256": source_sha256,
        "narrationTextSha256": manifest_narration_sha256,
        "identityStatus": generation.get("identityStatus")
        or "adopted-existing-release",
    }
    for key, value in expected_generation.items():
        if generation.get(key) != value:
            generation[key] = value
            changed = True
    expected_release = {
        "repository": repo,
        "tag": tag,
        "assetCount": len(chunks),
        "sourceTextSha256": source_sha256,
    }
    if book.get("release") != expected_release:
        book["release"] = expected_release
        changed = True
    if changed and not args.dry_run:
        write_json(path, manifest)
        write_json(catalog_path, catalog)
    verb = "would link" if args.dry_run and changed else "linked" if changed else "skip"
    print(f"{verb} {book_id}: {len(chunks)} verified release assets")
    return True


def mark_generation(
    args: argparse.Namespace,
    manifest: dict[str, Any],
    book_id: str,
    repo: str,
    tag: str,
    text_path: Path,
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
        "releaseTag": tag,
        "sourceTextSha256": sha256_file(text_path),
        "narrationTextSha256": narration_sha256(segments),
        "identityStatus": "generated-and-verified",
        "timing": "batched rough segment timing",
    }


def existing_release_assets(repo: str, tag: str) -> dict[str, ReleaseAsset] | None:
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
        ],
        cwd=repo_root(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if "release not found" in result.stderr.lower():
            return None
        raise SystemExit(
            result.stderr.strip() or f"could not read release {repo} {tag}"
        )
    value = json.loads(result.stdout)
    assets = {}
    for raw in value.get("assets", []):
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            continue
        digest = raw.get("digest")
        sha256 = digest.removeprefix("sha256:") if isinstance(digest, str) else ""
        size = raw.get("size")
        assets[raw["name"]] = ReleaseAsset(
            name=raw["name"],
            size_bytes=size if isinstance(size, int) else 0,
            sha256=sha256,
        )
    return assets


def upload_release_assets(
    args: argparse.Namespace,
    repo: str,
    tag: str,
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
    existing = existing_release_assets(repo, tag)
    existing_assets = existing or {}
    with tempfile.TemporaryDirectory(prefix=f"adiob-release-{book_id}-") as tmp:
        upload_files = []
        local_assets: dict[str, ReleaseAsset] = {}
        tmp_dir = Path(tmp)
        for index, chunk in enumerate(chunks, start=1):
            path = chunk.get("path") if isinstance(chunk, dict) else None
            if not isinstance(path, str) or not path:
                raise SystemExit(f"audio chunk is missing path: {book_id}")
            source = (manifest_path.parent / path).resolve(strict=False)
            if not source.is_file():
                raise SystemExit(f"missing generated audio chunk: {source}")
            asset_name = f"{book_id}-chunk-{index:03d}{source.suffix}"
            local_asset = ReleaseAsset(
                name=asset_name,
                size_bytes=source.stat().st_size,
                sha256=sha256_file(source),
            )
            local_assets[asset_name] = local_asset
            remote_asset = existing_assets.get(asset_name)
            if remote_asset == local_asset:
                continue
            if remote_asset is not None and not args.clobber:
                raise SystemExit(
                    "release asset name exists with different content; "
                    f"rerun with --clobber only after review: {repo} {tag} {asset_name}"
                )
            upload_path = tmp_dir / asset_name
            shutil.copyfile(source, upload_path)
            upload_files.append(upload_path)
        if existing is None:
            subprocess.run(
                [
                    "gh",
                    "release",
                    "create",
                    tag,
                    "-R",
                    repo,
                    "--title",
                    tag,
                    "--notes",
                    "Audiobook chunk assets for rights-cleared adiob content.",
                ],
                cwd=repo_root(),
                check=True,
            )
        if upload_files:
            upload_cmd = ["gh", "release", "upload", tag]
            upload_cmd.extend(str(path) for path in upload_files)
            if args.clobber:
                upload_cmd.append("--clobber")
            upload_cmd.extend(["-R", repo])
            subprocess.run(upload_cmd, cwd=repo_root(), check=True)
        else:
            print(f"release assets already exist for {book_id}")
    uploaded = existing_release_assets(repo, tag)
    if uploaded is None:
        raise SystemExit(f"release disappeared after upload: {repo} {tag}")
    prefix = release_url_prefix(repo, tag)
    for index, chunk in enumerate(chunks, start=1):
        asset_name = expected_asset_name(book_id, index, chunk)
        expected = local_assets[asset_name]
        actual = uploaded.get(asset_name)
        if actual != expected:
            raise SystemExit(
                f"release checksum verification failed: {repo} {tag} {asset_name}"
            )
        chunk["path"] = f"{prefix}{asset_name}"
        chunk["sha256"] = actual.sha256
        chunk["sizeBytes"] = actual.size_bytes


def copy_private_generated(
    private_root: Path,
    catalog_path: Path,
    catalog: dict[str, Any],
    book: dict[str, Any],
    manifest_path: Path,
    manifest: dict[str, Any],
    repo: str,
    tag: str,
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
    generation = manifest["generation"]
    book["release"] = {
        "repository": repo,
        "tag": tag,
        "assetCount": len(manifest["audioChunks"]),
        "sourceTextSha256": generation["sourceTextSha256"],
    }
    write_json(catalog_path, catalog)


def process_book(
    builder: ModuleType,
    args: argparse.Namespace,
    private_root: Path,
    catalog_path: Path,
    catalog: dict[str, Any],
    repo: str,
    book: dict[str, Any],
    release_assets: dict[str, dict[str, ReleaseAsset] | None],
) -> None:
    book_id = str(book["id"])
    release = book.get("release")
    if catalog_path.name == "internal-books.json" and (
        book.get("internalOnly") is not True
        or book.get("rightsConfirmed") is not True
        or not isinstance(release, dict)
        or not isinstance(release.get("tag"), str)
    ):
        raise SystemExit(
            "internal catalog entry must explicitly confirm internal use, rights, "
            f"and a release tag: {book_id}"
        )
    tag = book_release_tag(args, private_root, book)
    text_path = require_catalog_text(private_root, book)
    if tag not in release_assets:
        release_assets[tag] = existing_release_assets(repo, tag)
    if not args.force and reconcile_existing_manifest(
        builder,
        args,
        private_root,
        catalog_path,
        catalog,
        book,
        text_path,
        repo,
        tag,
        release_assets[tag],
    ):
        return
    segments, skipped_front_matter = expected_segments(builder, args, text_path)
    manifest_path = build_local_manifest(
        builder, args, book, segments, skipped_front_matter
    )
    generate_audio(args, manifest_path, book)
    if args.dry_run:
        return
    manifest = read_json(manifest_path)
    mark_generation(args, manifest, book_id, repo, tag, text_path)
    upload_release_assets(args, repo, tag, book_id, manifest, manifest_path)
    copy_private_generated(
        private_root,
        catalog_path,
        catalog,
        book,
        manifest_path,
        manifest,
        repo,
        tag,
        args.dry_run,
    )
    release_assets[tag] = existing_release_assets(repo, tag)
    duration = manifest.get("durationSec")
    chunks = len(manifest.get("audioChunks") or [])
    print(f"linked {book_id}: duration={duration} chunks={chunks}")


def main() -> None:
    args = parse_args()
    if not args.confirm_rights and not args.dry_run:
        raise SystemExit("pass --confirm-rights for public release audio generation")
    private_root = require_private_root(args.private_root)
    catalog_path = private_catalog_path(private_root, args.catalog)
    if not catalog_path.is_file():
        raise SystemExit(f"missing private catalog: {catalog_path}")
    catalog = read_json(catalog_path)
    books = selected_books(catalog, args)
    repo = canonical_repo(args.repo, args.dry_run)
    require_staged_release_repo(repo)
    builder = load_local_builder()
    release_assets: dict[str, dict[str, ReleaseAsset] | None] = {}
    for book in books:
        process_book(
            builder,
            args,
            private_root,
            catalog_path,
            catalog,
            repo,
            book,
            release_assets,
        )


if __name__ == "__main__":
    main()
