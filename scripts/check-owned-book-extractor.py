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
from unittest.mock import patch


def load_extractor() -> ModuleType:
    path = Path(__file__).with_name("extract-owned-book-text.py")
    spec = importlib.util.spec_from_file_location("owned_book_extractor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_exporter() -> ModuleType:
    path = Path(__file__).with_name("export-private-book-artifacts.py")
    spec = importlib.util.spec_from_file_location("private_book_exporter", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_release_processor() -> ModuleType:
    path = Path(__file__).with_name("process-private-book-release.py")
    spec = importlib.util.spec_from_file_location("private_book_release", path)
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


def check_pdf_ocr_fallback(module: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="adiob-extractor-check-") as tmp:
        source = Path(tmp) / "scan.pdf"
        source.write_bytes(b"scan")
        results = [
            type("Result", (), {"stdout": "1\n2\n3\n"})(),
            type("Result", (), {"stdout": ""})(),
            type("Result", (), {"stdout": "Recovered words " * 30})(),
        ]
        with patch.object(module.shutil, "which", return_value="/bin/tool"), patch.object(
            module.subprocess, "run", side_effect=results
        ) as run:
            text = module.extract_pdf(source)
        if "Recovered words" not in text:
            raise AssertionError("OCR text was not returned")
        if "--force-ocr" not in run.call_args_list[1].args[0]:
            raise AssertionError("sparse text fallback did not force OCR")
        if run.call_args_list[1].args[0][
            run.call_args_list[1].args[0].index("--language") + 1
        ] != "eng":
            raise AssertionError("OCR language was not passed to OCRmyPDF")


def check_exporter_boundaries(module: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="adiob-exporter-check-") as tmp:
        root = Path(tmp)
        nested = root / "nested"
        nested.mkdir()
        source = nested / "book.docx"
        source.write_bytes(b"docx")
        if module.audited_book_files(root, ["nested/book.docx"]) != [source]:
            raise AssertionError("nested audited path was not accepted")
        for unsafe in ("../book.pdf", "/book.pdf", "nested/../book.pdf"):
            expect_exit(lambda unsafe=unsafe: module.audited_book_files(root, [unsafe]))
        include = root / "include.json"
        include.write_text('["book.pdf", "book.pdf"]', encoding="utf-8")
        expect_exit(lambda: module.read_include_list(include))
        catalog = root / "books.json"
        catalog.write_text('{"books": [null], "failures": []}', encoding="utf-8")
        expect_exit(lambda: module.append_catalog(catalog))
        catalog.write_text("[]", encoding="utf-8")
        expect_exit(lambda: module.append_catalog(catalog))
        valid = {
            "id": "safe-book",
            "text": "texts/safe-book.txt",
            "generated": None,
        }
        if module.refresh_book_id(valid, "book.pdf") != "safe-book":
            raise AssertionError("valid refresh entry was rejected")
        if valid != {
            "id": "safe-book",
            "text": "texts/safe-book.txt",
            "generated": None,
        }:
            raise AssertionError("refresh validation mutated the catalog entry")
        for invalid in (
            {"id": "../../target", "text": "texts/../../target.txt", "generated": None},
            {"id": "safe-book", "text": "../target.txt", "generated": None},
            {"id": "safe-book", "text": "texts/safe-book.txt", "generated": {}},
        ):
            expect_exit(lambda invalid=invalid: module.refresh_book_id(invalid, "book.pdf"))


def check_release_voice(module: ModuleType) -> None:
    args = type("Args", (), {"voice": None, "lang": None})()
    voice, language = module.book_voice_lang(
        args,
        {"voice": "zf_xiaobei", "language": "z"},
    )
    if (voice, language) != ("zf_xiaobei", "z"):
        raise AssertionError("catalog voice and language were not selected")
    args = type("Args", (), {"voice": "af_bella", "lang": "a"})()
    if module.book_voice_lang(args, {"voice": "zf_xiaobei", "language": "z"}) != (
        "af_bella",
        "a",
    ):
        raise AssertionError("command-line voice and language did not override catalog")


def main() -> None:
    module = load_extractor()
    check_output_boundary(module)
    check_epub_uri_paths(module)
    check_pdf_ocr_fallback(module)
    check_exporter_boundaries(load_exporter())
    check_release_voice(load_release_processor())


if __name__ == "__main__":
    main()
