#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any


DEFAULT_PRIVATE_ROOT = Path("../adiob-private-artifacts")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set every stageable private catalog book to publish: true."
    )
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--dry-run", action="store_true")
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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
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


def load_stager() -> ModuleType:
    path = repo_root() / "scripts" / "stage-private-book-artifacts.py"
    spec = importlib.util.spec_from_file_location("private_book_stager", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_book_id(stager: ModuleType, book: dict[str, Any]) -> str:
    value = stager.require_book_id(book)
    if not isinstance(value, str) or not value:
        raise SystemExit("private catalog entry has an invalid id")
    return value


def require_stageable_books(
    private_root: Path, stager: ModuleType, books: list[dict[str, Any]]
) -> None:
    missing = []
    for book in books:
        entry_id = require_book_id(stager, book)
        generated = book.get("generated")
        if not isinstance(generated, dict) or not stager.generated_has_full_release_audio(
            private_root, entry_id, generated
        ):
            missing.append(entry_id)
    if not missing:
        return
    print(
        "refusing to set publish: true because these entries are missing "
        "full release-backed generated audio:"
    )
    for entry_id in missing:
        print(f"- {entry_id}")
    print(
        "run scripts/process-private-book-release.py --private-root "
        "../adiob-private-artifacts --all-books --release-tag "
        "audio-owned-chunks-v3 -R SichangHe/adiob --confirm-rights"
    )
    raise SystemExit(1)


def catalog_books(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    books = catalog.get("books")
    if not isinstance(books, list):
        raise SystemExit("private catalog must contain a books list")
    parsed = []
    for book in books:
        if not isinstance(book, dict):
            raise SystemExit("private catalog books must be objects")
        parsed.append(book)
    return parsed


def main() -> None:
    args = parse_args()
    private_root = require_private_root(args.private_root)
    catalog_path = private_root / "books.json"
    if not catalog_path.is_file():
        raise SystemExit(f"missing private catalog: {catalog_path}")
    catalog = read_json(catalog_path)
    books = catalog_books(catalog)
    stager = load_stager()
    require_stageable_books(private_root, stager, books)
    changed = []
    for book in books:
        if book.get("publish") is not True:
            book["publish"] = True
            changed.append(require_book_id(stager, book))
    if args.dry_run:
        print(f"would set publish: true for {len(changed)} private catalog entries")
        return
    if changed:
        write_json(catalog_path, catalog)
    print(f"set publish: true for {len(changed)} private catalog entries")


if __name__ == "__main__":
    main()
