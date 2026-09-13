#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
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


def load_publisher() -> ModuleType:
    path = Path(__file__).with_name("publish-private-audiobooks.py")
    spec = importlib.util.spec_from_file_location("private_audiobook_publisher", path)
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
        expect_exit(
            lambda: module.write_owned_file(Path("owned-text/out.txt"), "private")
        )


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
        with (
            patch.object(module.shutil, "which", return_value="/bin/tool"),
            patch.object(module.subprocess, "run", side_effect=results) as run,
        ):
            text = module.extract_pdf(source)
        if "Recovered words" not in text:
            raise AssertionError("OCR text was not returned")
        if "--force-ocr" not in run.call_args_list[1].args[0]:
            raise AssertionError("sparse text fallback did not force OCR")
        if (
            run.call_args_list[1].args[0][
                run.call_args_list[1].args[0].index("--language") + 1
            ]
            != "eng"
        ):
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
            expect_exit(
                lambda invalid=invalid: module.refresh_book_id(invalid, "book.pdf")
            )


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
    if "misaki[zh]>=0.9.4" not in module.tts_dependency_args("z"):
        raise AssertionError("Chinese TTS dependencies were not selected")
    if "misaki[zh]>=0.9.4" in module.tts_dependency_args("a"):
        raise AssertionError("Chinese TTS dependencies leaked into English generation")


def check_release_integrity_relink(module: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="adiob-release-check-") as tmp:
        private_root = Path(tmp)
        book_id = "test-book"
        text_path = private_root / "texts/test-book.txt"
        manifest_path = private_root / "generated/test-book/manifest.json"
        text_path.parent.mkdir()
        manifest_path.parent.mkdir(parents=True)
        text_path.write_text("test narration\n", encoding="utf-8")
        segments = [
            {"id": "s001", "text": "test narration", "startSec": 0, "endSec": 2}
        ]
        manifest = {
            "generation": {"fullBook": True},
            "segments": segments,
            "audioChunks": [
                {
                    "id": "chunk-001",
                    "path": "https://github.com/old/repo/releases/download/audio-v1/test-book-chunk-001.m4a",
                    "startSec": 0,
                    "endSec": 2,
                }
            ],
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        book = {
            "id": book_id,
            "text": "texts/test-book.txt",
            "generated": {"manifest": "generated/test-book/manifest.json"},
        }
        catalog = {"books": [book]}
        catalog_path = private_root / "books.json"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        args = type("Args", (), {"dry_run": False})()
        asset = module.ReleaseAsset("test-book-chunk-001.m4a", 42, "a" * 64)
        if not module.reconcile_existing_manifest(
            module.load_local_builder(),
            args,
            private_root,
            catalog_path,
            catalog,
            book,
            text_path,
            "SichangHe/adiob",
            "audio-v1",
            {asset.name: asset},
        ):
            raise AssertionError("complete remote audio was not relinked")
        linked = json.loads(manifest_path.read_text(encoding="utf-8"))
        chunk = linked["audioChunks"][0]
        if chunk["sha256"] != "a" * 64 or chunk["sizeBytes"] != 42:
            raise AssertionError("release integrity metadata was not recorded")
        if "/SichangHe/adiob/releases/" not in chunk["path"]:
            raise AssertionError("release URL was not corrected")
        if book["release"]["sourceTextSha256"] != module.sha256_file(text_path):
            raise AssertionError("catalog source identity was not recorded")
        linked["generation"].pop("sourceTextSha256")
        linked["generation"].pop("narrationTextSha256")
        linked["textProcessing"] = {"frontMatter": "skipped"}
        manifest_path.write_text(json.dumps(linked), encoding="utf-8")
        text_path.write_text("omitted middle\n\ntest narration\n", encoding="utf-8")
        if module.reconcile_existing_manifest(
            module.load_local_builder(),
            args,
            private_root,
            catalog_path,
            catalog,
            book,
            text_path,
            "SichangHe/adiob",
            "audio-v1",
            {asset.name: asset},
        ):
            raise AssertionError("legacy audio was adopted for different source text")


def check_release_conflict(module: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="adiob-conflict-check-") as tmp:
        root = Path(tmp)
        audio = root / "chunk-001.m4a"
        audio.write_bytes(b"new audio")
        manifest_path = root / "manifest.json"
        manifest = {
            "audioChunks": [{"path": "chunk-001.m4a", "startSec": 0, "endSec": 1}]
        }
        args = type("Args", (), {"dry_run": False, "clobber": False})()
        conflict = module.ReleaseAsset("test-book-chunk-001.m4a", 1, "b" * 64)
        with patch.object(
            module, "existing_release_assets", return_value={conflict.name: conflict}
        ):
            expect_exit(
                lambda: module.upload_release_assets(
                    args,
                    "SichangHe/adiob",
                    "audio-v1",
                    "test-book",
                    manifest,
                    manifest_path,
                )
            )


def check_internal_release_policy(module: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="adiob-internal-release-check-") as tmp:
        private_root = Path(tmp)
        catalog_path = private_root / "internal-books.json"
        book = {
            "id": "test-book",
            "text": "texts/test-book.txt",
            "rightsConfirmed": True,
            "release": {"tag": "internal-audio-v1"},
        }
        args = type("Args", (), {"release_tag": None, "force": False})()
        with (
            patch.object(module, "require_catalog_text", return_value=Path("text")),
            patch.object(module, "existing_release_assets", return_value={}),
            patch.object(module, "reconcile_existing_manifest", return_value=True),
        ):
            module.process_book(
                ModuleType("builder"),
                args,
                private_root,
                catalog_path,
                {"books": [book]},
                "SichangHe/adiob",
                book,
                {},
            )
        for invalid in (
            {**book, "rightsConfirmed": False},
            {**book, "release": {"tag": ""}},
        ):
            expect_exit(
                lambda invalid=invalid: module.process_book(
                    ModuleType("builder"),
                    args,
                    private_root,
                    catalog_path,
                    {"books": [invalid]},
                    "SichangHe/adiob",
                    invalid,
                    {},
                )
            )


def check_publisher_boundaries(module: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="adiob-publish-path-check-") as tmp:
        root = Path(tmp)
        (root / "books.json").write_text(
            '{"books":[{"id":"test-book"}]}', encoding="utf-8"
        )
        with patch.object(
            module,
            "git_status",
            return_value=[" M books.json", "?? generated/test-book/manifest.json"],
        ):
            if module.changed_private_paths(root) != [
                "books.json",
                "generated/test-book/manifest.json",
            ]:
                raise AssertionError("catalog-derived private paths were not accepted")
        for unsafe in (
            ["?? generated/scratch"],
            ["R  unrelated.txt -> generated/test-book/manifest.json"],
        ):
            with patch.object(module, "git_status", return_value=unsafe):
                expect_exit(lambda: module.changed_private_paths(root))
    result = type("Result", (), {"stdout": " M books.json\n"})()
    with patch.object(module.subprocess, "run", return_value=result):
        if module.command_output(["git", "status"], Path(".")) != " M books.json":
            raise AssertionError("Git status lost its leading worktree column")


def check_catalog_command_modes(module: ModuleType) -> None:
    args = type(
        "Args",
        (),
        {
            "repo": "SichangHe/adiob",
            "confirm_rights": True,
            "clobber": False,
            "dry_run": False,
        },
    )()
    with patch.object(module, "run") as run:
        module.process_catalog(args, Path("/private"), "books.json")
    command = run.call_args.args[0]
    if "--confirm-rights" not in command or "--dry-run" in command:
        raise AssertionError("non-dry catalog processing was not publication-capable")
    args.confirm_rights = False
    args.dry_run = True
    with patch.object(module, "run") as run:
        module.process_catalog(args, Path("/private"), "books.json")
    command = run.call_args.args[0]
    if "--dry-run" not in command or "--confirm-rights" in command:
        raise AssertionError("catalog audit was not read-only")


def check_resume_requires_review(module: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="adiob-resume-review-check-") as tmp:
        public_root = Path(tmp) / "public"
        private_root = Path(tmp) / "private"
        public_root.mkdir()
        private_root.mkdir()
        args = type(
            "Args",
            (),
            {
                "publish": True,
                "dry_run": False,
                "resume": True,
                "accept_resume_changes": False,
                "confirm_rights": True,
                "repo": "SichangHe/adiob",
                "private_root": private_root,
            },
        )()
        with (
            patch.object(module, "parse_args", return_value=args),
            patch.object(module, "repo_root", return_value=public_root),
            patch.object(module, "resolved_private_root", return_value=private_root),
            patch.object(module, "require_origins"),
            patch.object(module, "require_clean_main"),
            patch.object(module, "require_main"),
            patch.object(module, "changed_private_paths", return_value=["books.json"]),
        ):
            expect_exit(module.main)


def check_publication_is_private_repo_only(module: ModuleType) -> None:
    workflows = module.repo_root() / ".github/workflows"
    if workflows.is_dir() and any(workflows.iterdir()):
        raise AssertionError("GitHub Actions workflow remains enabled")
    with tempfile.TemporaryDirectory(prefix="adiob-private-publish-check-") as tmp:
        public_root = Path(tmp) / "public"
        private_root = Path(tmp) / "private"
        public_root.mkdir()
        private_root.mkdir()
        args = type(
            "Args",
            (),
            {
                "publish": True,
                "dry_run": False,
                "resume": False,
                "accept_resume_changes": False,
                "confirm_rights": True,
                "repo": "SichangHe/adiob",
                "private_root": private_root,
            },
        )()
        with (
            patch.object(module, "parse_args", return_value=args),
            patch.object(module, "repo_root", return_value=public_root),
            patch.object(module, "resolved_private_root", return_value=private_root),
            patch.object(module, "require_origins"),
            patch.object(module, "require_clean_main"),
            patch.object(
                module,
                "scan_catalog_texts",
                return_value=["books.json", "internal-books.json"],
            ),
            patch.object(module, "process_catalog") as process_catalog,
            patch.object(module, "set_public_index"),
            patch.object(module, "verify_staged_index"),
            patch.object(module, "changed_private_paths", return_value=["books.json"]),
            patch.object(module, "run") as run,
        ):
            module.main()
        if not run.call_args_list:
            raise AssertionError("private publication did not commit its index")
        processed = [call.args[2] for call in process_catalog.call_args_list]
        if processed != ["books.json", "internal-books.json"]:
            raise AssertionError("publication did not process both private catalogs")
        if any(call.args[1] != private_root for call in run.call_args_list):
            raise AssertionError("publication invoked Git in the public repository")
        pushes = [
            call
            for call in run.call_args_list
            if call.args[0] == ["git", "push", "origin", "main"]
        ]
        if len(pushes) != 1:
            raise AssertionError("publication did not make exactly one private push")


def main() -> None:
    module = load_extractor()
    check_output_boundary(module)
    check_epub_uri_paths(module)
    check_pdf_ocr_fallback(module)
    check_exporter_boundaries(load_exporter())
    release = load_release_processor()
    check_release_voice(release)
    check_release_integrity_relink(release)
    check_release_conflict(release)
    check_internal_release_policy(release)
    publisher = load_publisher()
    check_publisher_boundaries(publisher)
    check_catalog_command_modes(publisher)
    check_resume_requires_review(publisher)
    check_publication_is_private_repo_only(publisher)


if __name__ == "__main__":
    main()
