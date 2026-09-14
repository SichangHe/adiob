#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_PRIVATE_ROOT = Path("../adiob-private-artifacts")
DEFAULT_RELEASE_REPO = "SichangHe/adiob"
PRIVATE_REPO = "SichangHe/adiob-private-artifacts"
CATALOGS = ("books.json", "internal-books.json")
PRIVATE_ARTIFACT_REF = re.compile(
    r"(?m)^(\s*PRIVATE_BOOK_ARTIFACT_REF:\s*)[0-9a-f]{40}(\s*)$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish missing private-catalog audio and refresh its indexes."
    )
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("-R", "--repo", default=DEFAULT_RELEASE_REPO)
    parser.add_argument("--confirm-rights", action="store_true")
    parser.add_argument(
        "--clobber",
        action="store_true",
        help="Replace same-name release assets only when their checksums differ.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Commit and push private indexes and the pinned Pages deployment.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume --publish after an interrupted run.",
    )
    parser.add_argument(
        "--accept-resume-changes",
        action="store_true",
        help="Confirm that existing catalog-derived changes were reviewed.",
    )
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolved_private_root(value: Path) -> Path:
    path = value if value.is_absolute() else repo_root() / value
    private_root = path.resolve(strict=True)
    if private_root == repo_root() or private_root.is_relative_to(repo_root()):
        raise SystemExit("private artifact root must be outside the public repo")
    return private_root


def command_output(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.rstrip()


def run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def git_status(repo: Path) -> list[str]:
    output = command_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], repo
    )
    return output.splitlines() if output else []


def require_main(repo: Path) -> None:
    branch = command_output(["git", "branch", "--show-current"], repo)
    if branch != "main":
        raise SystemExit(f"publish requires the main branch: {repo}")


def require_clean_main(repo: Path) -> None:
    require_main(repo)
    if git_status(repo):
        raise SystemExit(f"publish requires a clean worktree: {repo}")


def canonical_origin(repo: Path) -> str:
    url = command_output(["git", "remote", "get-url", "origin"], repo)
    match = re.fullmatch(
        r"(?:git@github\.com:|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?", url
    )
    if match is None:
        raise SystemExit(f"unsupported origin URL: {repo}")
    return match.group(1)


def require_origins(public_root: Path, private_root: Path, release_repo: str) -> None:
    expected = ((public_root, release_repo), (private_root, PRIVATE_REPO))
    for path, repository in expected:
        if canonical_origin(path).lower() != repository.lower():
            raise SystemExit(f"origin mismatch for {path}; expected {repository}")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON root must be an object: {path}")
    return value


def has_symlink_component(path: Path, root: Path) -> bool:
    candidate = path
    while candidate != root:
        if candidate.is_symlink():
            return True
        if candidate.parent == candidate:
            return True
        candidate = candidate.parent
    return False


def scan_catalog_texts(private_root: Path) -> list[str]:
    seen_ids: set[str] = set()
    catalogs = []
    for name in CATALOGS:
        path = private_root / name
        if not path.is_file():
            if name == "books.json":
                raise SystemExit(f"missing required private catalog: {path}")
            continue
        books = read_json(path).get("books")
        if not isinstance(books, list):
            raise SystemExit(f"private catalog must contain a books list: {path}")
        for book in books:
            if not isinstance(book, dict):
                raise SystemExit(f"private catalog book must be an object: {path}")
            book_id = book.get("id")
            text = book.get("text")
            if not isinstance(book_id, str) or not isinstance(text, str):
                raise SystemExit(f"private catalog book is missing id or text: {path}")
            if book_id in seen_ids:
                raise SystemExit(f"duplicate private catalog book id: {book_id}")
            seen_ids.add(book_id)
            relative_text = Path(text)
            if relative_text.is_absolute() or ".." in relative_text.parts:
                raise SystemExit(f"missing or unsafe private text for {book_id}")
            lexical_text = private_root / relative_text
            text_path = lexical_text.resolve(strict=False)
            if (
                not text_path.is_relative_to(private_root)
                or not text_path.is_file()
                or has_symlink_component(lexical_text, private_root)
            ):
                raise SystemExit(f"missing or unsafe private text for {book_id}")
        catalogs.append(name)
    print(f"scanned {len(seen_ids)} cataloged private texts in {len(catalogs)} indexes")
    return catalogs


def process_catalog(args: argparse.Namespace, private_root: Path, catalog: str) -> None:
    cmd = [
        sys.executable,
        "scripts/process-private-book-release.py",
        "--private-root",
        str(private_root),
        "--catalog",
        catalog,
        "--all-books",
        "-R",
        args.repo,
    ]
    cmd.append("--exclude-publish-opt-out")
    if args.confirm_rights:
        cmd.append("--confirm-rights")
    if args.clobber:
        cmd.append("--clobber")
    if args.dry_run:
        cmd.append("--dry-run")
    run(cmd, repo_root())


def set_public_index(private_root: Path, dry_run: bool) -> None:
    cmd = [
        sys.executable,
        "scripts/set-private-books-publish.py",
        "--private-root",
        str(private_root),
    ]
    if dry_run:
        cmd.append("--dry-run")
    run(cmd, repo_root())


def verify_staged_index(private_root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="adiob-index-check-") as tmp:
        site_root = Path(tmp)
        for name in (
            "index.html",
            "field-notes-819a",
            "ASSET-LICENSE.md",
            "LICENSE",
            "data",
            "media",
            "src",
        ):
            source = repo_root() / name
            target = site_root / name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copyfile(source, target)
        run(
            [
                sys.executable,
                "scripts/stage-private-book-artifacts.py",
                "--private-root",
                str(private_root),
                "--site-root",
                str(site_root),
                "--reader-path",
                "field-notes-819a",
            ],
            repo_root(),
        )
        staged = read_json(site_root / "field-notes-819a/catalog.json")
        expected_ids = {
            book["id"] for book in read_json(site_root / "data/books.json")["books"]
        }
        for catalog in CATALOGS:
            path = private_root / catalog
            if path.is_file():
                expected_ids.update(book["id"] for book in read_json(path)["books"])
        require_staged_ids(staged, expected_ids)


def require_staged_ids(staged: dict[str, Any], expected_ids: set[str]) -> None:
    books = staged.get("books")
    if not isinstance(books, list) or any(not isinstance(book, dict) for book in books):
        raise SystemExit("staged Pages index must contain a books list")
    staged_ids = [book.get("id") for book in books]
    if any(not isinstance(book_id, str) for book_id in staged_ids):
        raise SystemExit("staged Pages index contains an invalid book id")
    if len(staged_ids) != len(set(staged_ids)) or set(staged_ids) != expected_ids:
        raise SystemExit("staged Pages index is inconsistent with source indexes")


def expected_private_paths(private_root: Path) -> tuple[set[str], set[str]]:
    files = {"books.json"}
    chunk_roots = set()
    for catalog in CATALOGS:
        path = private_root / catalog
        if not path.is_file():
            continue
        files.add(catalog)
        for book in read_json(path).get("books", []):
            book_id = book.get("id") if isinstance(book, dict) else None
            if (
                not isinstance(book_id, str)
                or re.fullmatch(r"[a-z0-9][a-z0-9-]*", book_id) is None
            ):
                raise SystemExit(f"invalid book id in {path}")
            root = f"generated/{book_id}"
            files.update({f"{root}/manifest.json", f"{root}/cover.svg"})
            chunk_roots.add(f"{root}/chunks/")
    return files, chunk_roots


def changed_private_paths(private_root: Path) -> list[str]:
    allowed_files, allowed_deleted_chunk_roots = expected_private_paths(private_root)
    paths = []
    for line in git_status(private_root):
        status = line[:2]
        if status not in {" M", "M ", " A", "A ", " D", "D ", "??"}:
            raise SystemExit(f"refusing unsupported private Git status: {line}")
        path = line[3:]
        if " -> " in path:
            raise SystemExit(f"refusing private rename during publish: {path}")
        deleted_chunk = status.strip() == "D" and any(
            path.startswith(root) for root in allowed_deleted_chunk_roots
        )
        if path not in allowed_files and not deleted_chunk:
            raise SystemExit(f"refusing to commit unrelated private change: {path}")
        paths.append(path)
    return paths


def commit_private(private_root: Path) -> str:
    changed = changed_private_paths(private_root)
    if changed:
        run(["git", "add", "--", *changed], private_root)
        run(
            ["git", "commit", "-m", "chore: sync audiobook releases and indexes"],
            private_root,
        )
    run(["git", "push", "origin", "main"], private_root)
    private_commit = command_output(["git", "rev-parse", "HEAD"], private_root)
    remote_commit = command_output(
        ["git", "rev-parse", "refs/remotes/origin/main"], private_root
    )
    if remote_commit != private_commit:
        raise SystemExit("private origin/main does not match the deployment commit")
    return private_commit


def update_pages_ref(private_commit: str) -> bool:
    if re.fullmatch(r"[0-9a-f]{40}", private_commit) is None:
        raise SystemExit("private commit must be a full lowercase Git SHA")
    path = repo_root() / ".github/workflows/pages.yml"
    current = path.read_text(encoding="utf-8")
    updated, n_replacements = PRIVATE_ARTIFACT_REF.subn(
        rf"\g<1>{private_commit}\g<2>", current
    )
    if n_replacements != 1:
        raise SystemExit("Pages workflow must contain exactly one pinned private ref")
    if updated == current:
        return False
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as tmp:
        tmp.write(updated)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)
    return True


def commit_public_index(private_commit: str) -> None:
    if update_pages_ref(private_commit):
        run(["git", "add", "--", ".github/workflows/pages.yml"], repo_root())
        run(["git", "commit", "-m", "chore: publish audiobook index"], repo_root())
    if git_status(repo_root()):
        raise SystemExit("refusing to push public repo with unrelated changes")
    run(["git", "push", "origin", "main"], repo_root())


# 🧑 "Have one script that scans the private repo for texts. Convert any that doesn't have audio already. Put them on releases. And then include them in indexes."
def main() -> None:
    args = parse_args()
    if args.publish and args.dry_run:
        raise SystemExit("choose either --dry-run or --publish")
    if args.resume and not args.publish:
        raise SystemExit("--resume requires --publish")
    if args.accept_resume_changes and not args.resume:
        raise SystemExit("--accept-resume-changes requires --resume")
    if not args.dry_run and not args.confirm_rights:
        raise SystemExit("pass --confirm-rights before generating or publishing audio")
    public_root = repo_root()
    private_root = resolved_private_root(args.private_root)
    require_origins(public_root, private_root, args.repo)
    if args.publish:
        require_clean_main(public_root)
        if args.resume:
            require_main(private_root)
            existing_changes = changed_private_paths(private_root)
            if existing_changes and not args.accept_resume_changes:
                raise SystemExit(
                    "review the existing private diff, then pass "
                    "--accept-resume-changes to include it"
                )
        else:
            require_clean_main(private_root)
    catalogs = scan_catalog_texts(private_root)
    for catalog in catalogs:
        process_catalog(args, private_root, catalog)
    if args.dry_run:
        return
    set_public_index(private_root, False)
    verify_staged_index(private_root)
    # 🧑 "Okay, you do need GitHub action to deploy the site. Reinstate it"
    if args.publish:
        private_commit = commit_private(private_root)
        commit_public_index(private_commit)


if __name__ == "__main__":
    main()
