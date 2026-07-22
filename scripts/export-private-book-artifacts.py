#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any


SUPPORTED_SUFFIXES = {".pdf", ".epub", ".docx", ".mobi"}
DEFAULT_INCLUDE_LIST = "top-level-english-files.json"
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
PUBLIC_DEMOS = {
    "The Elements of Style, William Strunk, Jr..pdf": {
        "id": "the-elements-of-style",
        "title": "The Elements of Style",
        "author": "William Strunk Jr.",
        "demo": "the-elements-of-style",
        "license": "Public-domain source text. Takedown requests and rights concerns can be sent to the repository owner.",
    },
    "Walden - Henry David Thoreau.pdf": {
        "id": "walden",
        "title": "Walden",
        "author": "Henry David Thoreau",
        "demo": "walden",
        "license": "Public-domain source text. Takedown requests and rights concerns can be sent to the repository owner.",
    },
}
SOURCE_METADATA = {
    "DMV/California_Driver_Handbook_10-01-24-Passed-accessible-DL-600-Rev.-1-2024.pdf": {
        "title": "California Driver's Handbook",
        "author": "California Department of Motor Vehicles",
    },
    "DMV/Words_and_Phrases_for_Class_C_Driving_Tests_DL-80-EN-R3-2022-Access-Secured.pdf": {
        "title": "Words and Phrases for Class C Driving Tests",
        "author": "California Department of Motor Vehicles",
    },
    "DMV/road_sign_chart_DL-37-R11-2009-English-Secured.pdf": {
        "title": "California Road Sign Chart",
        "author": "California Department of Motor Vehicles",
    },
    "Elon Musk - PDF Room.pdf": {"title": "Elon Musk", "author": "Walter Isaacson"},
    "The Art of Computer Programming.pdf": {
        "title": "The Art of Computer Programming, Volume 1: Fundamental Algorithms",
        "author": "Donald E. Knuth",
        "language": "z",
        "voice": "zf_xiaobei",
        "ocrLanguage": "chi_sim+eng",
    },
    "The Intelligent Investor - BENJAMIN GRAHAM.pdf": {
        "title": "The Intelligent Investor",
        "author": "Benjamin Graham",
    },
    "dokumen.pub_the-man-who-solved-the-market-how-jim-simons-launched-the-quant-revolution-hardcovernbsped-073521798x-9780735217980.epub": {
        "title": "The Man Who Solved the Market",
        "author": "Gregory Zuckerman",
    },
    "弗兰克尔自传：活出生命的意义 - 维克多·弗兰克尔（中亚）.mobi": {
        "title": "弗兰克尔自传：活出生命的意义",
        "author": "维克多·弗兰克尔",
        "language": "z",
        "voice": "zf_xiaobei",
    },
    "苏东坡传.docx": {
        "title": "苏东坡传",
        "author": "林语堂",
        "language": "z",
        "voice": "zf_xiaobei",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export top-level English book text into a private artifact repo."
    )
    parser.add_argument("--book-dir", required=True, type=Path)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--include-list", type=Path)
    parser.add_argument(
        "--append",
        action="store_true",
        help="Keep existing catalog entries and export only newly audited sources.",
    )
    parser.add_argument(
        "--refresh-source",
        action="append",
        default=[],
        help="Re-extract an existing append-mode source that has no generated release.",
    )
    parser.add_argument("--local-demo-root", type=Path, default=Path("local/owned-books"))
    parser.add_argument(
        "--confirm-private-repo-output",
        action="store_true",
        help="Confirm output is a private repository, not the public Pages repo.",
    )
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_extractor() -> ModuleType:
    path = repo_root() / "scripts" / "extract-owned-book-text.py"
    spec = importlib.util.spec_from_file_location("owned_book_extractor", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def append_catalog(path: Path) -> dict[str, Any]:
    catalog = read_json(path)
    if not isinstance(catalog, dict):
        raise SystemExit("existing private catalog must be a JSON object")
    books = catalog.get("books")
    failures = catalog.get("failures")
    if not isinstance(books, list) or not all(isinstance(book, dict) for book in books):
        raise SystemExit("existing private catalog must contain only object books")
    if not isinstance(failures, list) or not all(
        isinstance(failure, dict) for failure in failures
    ):
        raise SystemExit("existing private catalog must contain only object failures")
    return catalog


def refresh_book_id(book: dict[str, Any], source_file: str) -> str:
    if book.get("generated") is not None:
        raise SystemExit(f"refusing to refresh generated source: {source_file}")
    book_id = book.get("id")
    if not isinstance(book_id, str) or SAFE_ID.fullmatch(book_id) is None:
        raise SystemExit(f"refusing unsafe refresh book id: {source_file}")
    if book.get("text") != f"texts/{book_id}.txt":
        raise SystemExit(f"refusing unsafe refresh text path: {source_file}")
    return book_id


def slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value or "book"


def title_from_stem(stem: str) -> str:
    text = re.sub(r"[_-]+", " ", stem)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def include_list_path(private_root: Path, include_list: Path | None) -> Path:
    if include_list is None:
        return private_root / DEFAULT_INCLUDE_LIST
    return root_path(include_list)


def read_include_list(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"missing private audited book include list: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SystemExit("private include list must be a JSON string array")
    if len(value) != len(set(value)):
        raise SystemExit("private include list contains duplicate paths")
    return value


def audited_book_files(book_dir: Path, names: list[str]) -> list[Path]:
    files = []
    for name in names:
        relative = Path(name)
        if (
            name in {"", ".", ".."}
            or relative.is_absolute()
            or posixpath.normpath(name) != name
            or not (book_dir / relative)
            .resolve(strict=False)
            .is_relative_to(book_dir.resolve())
        ):
            raise SystemExit(f"include list entry must be a safe relative path: {name}")
        path = book_dir / relative
        if not path.is_file():
            raise SystemExit(f"audited book file is missing: {name}")
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise SystemExit(f"unsupported audited book file: {name}")
        files.append(path)
    return files


def unique_id(path: Path, used: set[str], known_id: str | None = None) -> str:
    base = known_id or slug(path.stem)
    candidate = base
    if candidate in used:
        candidate = f"{base}-{path.suffix.lower().lstrip('.')}"
    index = 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def copy_demo(private_root: Path, book_id: str, demo_name: str, local_demo_root: Path) -> dict[str, str] | None:
    source = root_path(local_demo_root) / demo_name
    paths = {
        "manifest": source / "manifest.json",
        "audio": source / "demo.m4a",
        "cover": source / "cover.svg",
    }
    if not all(path.is_file() for path in paths.values()):
        return None
    target = private_root / "generated" / book_id
    target.mkdir(parents=True, exist_ok=True)
    for name, source_path in paths.items():
        shutil.copyfile(source_path, target / source_path.name)
    return {
        "manifest": f"generated/{book_id}/manifest.json",
        "audio": f"generated/{book_id}/demo.m4a",
        "cover": f"generated/{book_id}/cover.svg",
    }


def main() -> None:
    args = parse_args()
    if not args.confirm_private_repo_output:
        raise SystemExit("pass --confirm-private-repo-output for private artifact export")
    book_dir = root_path(args.book_dir)
    if not book_dir.is_dir():
        raise SystemExit(f"book directory does not exist: {book_dir}")
    private_root = require_private_root(args.private_root)
    include_names = read_include_list(include_list_path(private_root, args.include_list))
    refresh_sources = set(args.refresh_source)
    if refresh_sources and not args.append:
        raise SystemExit("--refresh-source requires --append")
    if not refresh_sources.issubset(include_names):
        raise SystemExit("--refresh-source must name audited include-list paths")
    extractor = load_extractor()
    catalog_path = private_root / "books.json"
    catalog = (
        append_catalog(catalog_path)
        if args.append and catalog_path.is_file()
        else {}
    )
    existing_books = catalog.get("books", [])
    existing_failures = catalog.get("failures", [])
    books = list(existing_books)
    failures = [
        failure
        for failure in existing_failures
        if failure.get("sourceFile") not in include_names
    ]
    used = {
        str(book["id"])
        for book in books
        if isinstance(book.get("id"), str)
    }
    existing_by_source = {
        str(book["sourceFile"]): book
        for book in books
        if isinstance(book.get("sourceFile"), str)
    }
    for source in audited_book_files(book_dir, include_names):
        source_file = source.relative_to(book_dir).as_posix()
        existing_book = existing_by_source.get(source_file)
        if existing_book is not None and source_file not in refresh_sources:
            existing_book.update(SOURCE_METADATA.get(source_file, {}))
            print(f"kept existing {source_file}")
            continue
        known = PUBLIC_DEMOS.get(source_file, {})
        metadata = SOURCE_METADATA.get(source_file, {})
        if existing_book is not None:
            book_id = refresh_book_id(existing_book, source_file)
        else:
            book_id = unique_id(source, used, known.get("id"))
        try:
            text = extractor.normalize_text(
                extractor.extract_text(
                    source,
                    str(metadata.get("ocrLanguage") or "eng"),
                )
            )
            if len(text.strip()) < 100:
                raise ValueError("extracted text is unexpectedly short")
        except SystemExit as exc:
            error = str(exc)
        except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as exc:
            error = str(exc)
        else:
            error = ""
        if error:
            failures.append(
                {
                    "id": book_id,
                    "sourceFile": source_file,
                    "error": error,
                }
            )
            print(f"failed {source.name}: {error}")
            continue
        text_path = private_root / "texts" / f"{book_id}.txt"
        write_file(text_path, text)
        if existing_book is not None:
            existing_book.update(metadata)
            print(f"refreshed {source.name} -> texts/{book_id}.txt")
            continue
        generated = None
        publish = bool(known)
        if publish:
            generated = copy_demo(private_root, book_id, known["demo"], args.local_demo_root)
            if generated is None:
                raise SystemExit(f"missing generated demo for publishable book: {source.name}")
        book = {
                "id": book_id,
                "title": known.get("title") or title_from_stem(source.stem),
                "author": known.get("author") or "",
                "sourceFile": source_file,
                "text": f"texts/{book_id}.txt",
                "publish": publish,
                "license": known.get("license")
                or "Private text artifact. Do not publish without explicit review.",
                "generated": generated,
            }
        book.update(metadata)
        books.append(book)
        print(f"exported {source.name} -> texts/{book_id}.txt")
    catalog.update(
        {
            "schema": 1,
            "source": "Audited files from the local book directory.",
            "books": books,
            "failures": failures,
        }
    )
    write_json(private_root / "books.json", catalog)
    print(f"wrote {private_root / 'books.json'}")


if __name__ == "__main__":
    main()
