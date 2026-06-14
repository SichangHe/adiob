#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import tempfile
import textwrap
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType


def load_extractor() -> ModuleType:
    path = Path(__file__).with_name("extract-owned-book-text.py")
    spec = importlib.util.spec_from_file_location("owned_book_extractor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect_exit(fn: Callable[[], object]) -> None:
    try:
        fn()
    except SystemExit:
        return
    raise AssertionError("expected SystemExit")


def with_fake_repo(module: ModuleType, root: Path) -> None:
    module.repo_root = lambda: root


def check_output_boundary(module: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="adiob-extractor-check-") as tmp:
        root = Path(tmp)
        with_fake_repo(module, root)
        owned = root / "owned-text"
        owned.mkdir()
        (root / "public.txt").write_text("public", encoding="utf-8")

        expect_exit(lambda: module.require_boundary(Path("../leak.txt")))
        expect_exit(lambda: module.require_boundary(root.parent / "leak.txt"))

        link = owned / "link.txt"
        link.symlink_to(root / "public.txt")
        expect_exit(lambda: module.write_owned_file(link, "private"))

        parent_link = owned / "parent-link"
        parent_link.symlink_to(owned, target_is_directory=True)
        expect_exit(lambda: module.write_owned_file(parent_link / "out.txt", "private"))

        hard = owned / "hard.txt"
        os.link(root / "public.txt", hard)
        module.write_owned_file(hard, "private")
        if (root / "public.txt").read_text(encoding="utf-8") != "public":
            raise AssertionError("hardlink target was modified")
        if hard.read_text(encoding="utf-8") != "private":
            raise AssertionError("hardlink path was not replaced")

    with tempfile.TemporaryDirectory(prefix="adiob-extractor-check-") as tmp:
        root = Path(tmp) / "repo"
        target = Path(tmp) / "outside"
        root.mkdir()
        target.mkdir()
        (root / "owned-text").symlink_to(target, target_is_directory=True)
        with_fake_repo(module, root)
        expect_exit(lambda: module.write_owned_file(Path("owned-text/out.txt"), "private"))


def check_epub_uri_paths(module: ModuleType) -> None:
    container = textwrap.dedent(
        """\
        <?xml version="1.0"?>
        <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
          <rootfiles>
            <rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/>
          </rootfiles>
        </container>
        """
    )
    package = textwrap.dedent(
        """\
        <package xmlns="http://www.idpf.org/2007/opf" version="2.0">
          <manifest>
            <item id="c1" href="chapter%201.xhtml#frag" media-type="application/xhtml+xml"/>
          </manifest>
          <spine>
            <itemref idref="c1"/>
          </spine>
        </package>
        """
    )
    with tempfile.TemporaryDirectory(prefix="adiob-extractor-check-") as tmp:
        epub_path = Path(tmp) / "book.epub"
        with zipfile.ZipFile(epub_path, "w") as epub:
            epub.writestr("META-INF/container.xml", container)
            epub.writestr("OPS/package.opf", package)
            epub.writestr(
                "OPS/chapter 1.xhtml",
                "<html><body><p>hello encoded chapter</p></body></html>",
            )
        if "hello encoded chapter" not in module.extract_epub(epub_path):
            raise AssertionError("encoded EPUB href was not extracted")


def main() -> None:
    module = load_extractor()
    check_output_boundary(module)
    check_epub_uri_paths(module)


if __name__ == "__main__":
    main()
