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


SUPPORTED_SUFFIXES = {".pdf", ".epub"}
DEFAULT_INCLUDE_LIST = "top-level-english-files.json"
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export top-level English book text into a private artifact repo."
    )
    parser.add_argument("--book-dir", required=True, type=Path)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--include-list", type=Path)
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
        raise SystemExit(f"missing private top-level English include list: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SystemExit("private include list must be a JSON string array")
    return value


def top_level_english_files(book_dir: Path, names: list[str]) -> list[Path]:
    files = []
    for name in names:
        if (
            Path(name).name != name
            or posixpath.basename(name) != name
            or name in {"", ".", ".."}
        ):
            raise SystemExit(f"include list entry must be a top-level filename: {name}")
        path = book_dir / name
        if not path.is_file():
            raise SystemExit(f"audited top-level English file is missing: {name}")
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise SystemExit(f"unsupported audited English file: {name}")
        files.append(path)
    return files


def unique_id(path: Path, used: set[str]) -> str:
    known = PUBLIC_DEMOS.get(path.name)
    base = known["id"] if known else slug(path.stem)
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
    extractor = load_extractor()
    used: set[str] = set()
    books = []
    failures = []
    for source in top_level_english_files(book_dir, include_names):
        book_id = unique_id(source, used)
        known = PUBLIC_DEMOS.get(source.name, {})
        try:
            text = extractor.normalize_text(extractor.extract_text(source))
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
                    "sourceFile": source.name,
                    "error": error,
                }
            )
            print(f"failed {source.name}: {error}")
            continue
        text_path = private_root / "texts" / f"{book_id}.txt"
        write_file(text_path, text)
        generated = None
        publish = bool(known)
        if publish:
            generated = copy_demo(private_root, book_id, known["demo"], args.local_demo_root)
            if generated is None:
                raise SystemExit(f"missing generated demo for publishable book: {source.name}")
        books.append(
            {
                "id": book_id,
                "title": known.get("title") or title_from_stem(source.stem),
                "author": known.get("author") or "",
                "sourceFile": source.name,
                "text": f"texts/{book_id}.txt",
                "publish": publish,
                "license": known.get("license")
                or "Private text artifact. Do not publish without explicit review.",
                "generated": generated,
            }
        )
        print(f"exported {source.name} -> texts/{book_id}.txt")
    write_json(
        private_root / "books.json",
        {
            "schema": 1,
            "source": "Top-level English files from the local book directory.",
            "books": books,
            "failures": failures,
        },
    )
    print(f"wrote {private_root / 'books.json'}")


if __name__ == "__main__":
    main()
