#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import textwrap
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
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


def load_cleaner() -> ModuleType:
    path = Path(__file__).with_name("clean-private-book-texts.py")
    spec = importlib.util.spec_from_file_location("private_book_cleaner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_script(name: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
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


def check_listenable_layout(module: ModuleType) -> None:
    source = (
        "Commentary on Chapter 4 109\n\n"
        "FIGURE 4-1 The Wide World of Bonds 2002 1.2 2.7\n\n"
        "Type Maturity Yield 1 year 1.2 5 years 2.7\n\n"
        "From the end of 1999 through 2002, a $3,000 investment plus $100 "
        "monthly made a $6,600 total outlay and lost 30.2%, less than 41.3%.\n\n"
        "Keep this complete narrative sentence because it explains the figure in context. "
        "• first choice • second choice\n"
        "\nFor 2003, a single person earning $28,400 paid the bottom tax rate.\n"
        "\nSee http://example.com/a/b/c/d/e/f/g for a prose explanation.\n"
        "\n1. Keep the prose. 2. Spell out the list.\n"
    )
    text = module.listenable_text(
        "the-intelligent-investor-benjamin-graham", source
    )
    expected = (
        "Below is document The Intelligent Investor.\n\n"
        "Figure 4-1, titled The Wide World of Bonds, presents the comparison "
        "discussed in the surrounding text. Its visual row-and-column or "
        "graphical layout has been replaced with this description for listening.\n\n"
        "From the end of 1999 through 2002, a $3,000 investment plus $100 "
        "monthly made a $6,600 total outlay and lost 30.2%, less than 41.3%.\n\n"
        "Keep this complete narrative sentence because it explains the figure in context.\n\n"
        "Bullet point: first choice\n\n"
        "Bullet point: second choice\n\n"
        "For 2003, a single person earning $28,400 paid the bottom tax rate.\n\n"
        "See http://example.com/a/b/c/d/e/f/g for a prose explanation.\n\n"
        "First, Keep the prose.\n\n"
        "Second, Spell out the list.\n\n"
        "Above was document The Intelligent Investor.\n"
    )
    if text != expected:
        raise AssertionError("listening cleanup did not neutralize page layout")
    with tempfile.TemporaryDirectory(prefix="adiob-cleaner-check-") as tmp:
        source = Path(tmp) / "source.txt"
        source.write_text("narrative\n", encoding="utf-8")
        module.LISTENABLE_LINE_RANGES["guard-test"] = ((1, 1, None),)
        module.LISTENABLE_SOURCE_SHA256["guard-test"] = "0" * 64
        try:
            expect_exit(
                lambda: module.body_text(
                    source, "guard-test", module.BookCut(1, 1, "test")
                )
            )
        finally:
            del module.LISTENABLE_LINE_RANGES["guard-test"]
            del module.LISTENABLE_SOURCE_SHA256["guard-test"]


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

    with tempfile.TemporaryDirectory(prefix="adiob-pages-ref-check-") as tmp:
        root = Path(tmp)
        workflow = root / ".github/workflows/pages.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "PRIVATE_BOOK_ARTIFACT_REF: " + "a" * 40 + "\n", encoding="utf-8"
        )
        with patch.object(module, "repo_root", return_value=root):
            if not module.update_pages_ref("b" * 40):
                raise AssertionError("changed Pages private ref was not reported")
            if workflow.read_text(encoding="utf-8") != (
                "PRIVATE_BOOK_ARTIFACT_REF: " + "b" * 40 + "\n"
            ):
                raise AssertionError("Pages private ref was not updated exactly")
            if module.update_pages_ref("b" * 40):
                raise AssertionError("unchanged Pages private ref was reported changed")
            expect_exit(lambda: module.update_pages_ref("short"))
            workflow.write_text("name: pages\n", encoding="utf-8")
            expect_exit(lambda: module.update_pages_ref("c" * 40))


def check_pages_workflow(module: ModuleType) -> None:
    workflow = (module.repo_root() / ".github/workflows/pages.yml").read_text(
        encoding="utf-8"
    )
    refs = module.PRIVATE_ARTIFACT_REF.findall(workflow)
    if len(refs) != 1:
        raise AssertionError("Pages workflow lacks one full private commit pin")
    required = (
        "scripts/fetch-private-book-artifacts.sh _private-books",
        "test -f _private-books/books.json",
        "test -f _private-books/internal-books.json",
        "scripts/stage-private-book-artifacts.py --private-root _private-books",
        "uses: actions/deploy-pages@v4",
    )
    if any(value not in workflow for value in required):
        raise AssertionError(
            "Pages workflow does not stage and deploy private catalogs"
        )


def check_public_index_commit(module: ModuleType) -> None:
    public_root = Path("/public")
    with (
        patch.object(module, "repo_root", return_value=public_root),
        patch.object(module, "update_pages_ref", return_value=True),
        patch.object(module, "git_status", return_value=[]),
        patch.object(module, "run") as run,
    ):
        module.commit_public_index("b" * 40)
    if [call.args for call in run.call_args_list] != [
        (["git", "add", "--", ".github/workflows/pages.yml"], public_root),
        (["git", "commit", "-m", "chore: publish audiobook index"], public_root),
        (["git", "push", "origin", "main"], public_root),
    ]:
        raise AssertionError("Pages ref publication did not use the exact public path")

    with (
        patch.object(module, "repo_root", return_value=public_root),
        patch.object(module, "update_pages_ref", return_value=False),
        patch.object(module, "git_status", return_value=[" M README.md"]),
        patch.object(module, "run") as run,
    ):
        expect_exit(lambda: module.commit_public_index("b" * 40))
    if run.called:
        raise AssertionError("public publication pushed unrelated changes")


def check_private_commit_is_remote(module: ModuleType) -> None:
    private_root = Path("/private")
    with (
        patch.object(module, "changed_private_paths", return_value=[]),
        patch.object(module, "run") as run,
        patch.object(module, "command_output", side_effect=["b" * 40, "b" * 40]),
    ):
        commit = module.commit_private(private_root)
    if commit != "b" * 40:
        raise AssertionError("private publication returned the wrong commit")
    run.assert_called_once_with(["git", "push", "origin", "main"], private_root)

    with (
        patch.object(module, "changed_private_paths", return_value=[]),
        patch.object(module, "run", side_effect=RuntimeError("push failed")),
        patch.object(module, "command_output") as command_output,
    ):
        try:
            module.commit_private(private_root)
        except RuntimeError:
            pass
        else:
            raise AssertionError("failed private push returned a deployment commit")
    command_output.assert_not_called()

    with (
        patch.object(module, "changed_private_paths", return_value=[]),
        patch.object(module, "run"),
        patch.object(module, "command_output", side_effect=["b" * 40, "c" * 40]),
    ):
        expect_exit(lambda: module.commit_private(private_root))


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
    if (
        "--confirm-rights" not in command
        or "--dry-run" in command
        or "--exclude-publish-opt-out" not in command
    ):
        raise AssertionError("non-dry catalog processing was not publication-capable")
    with patch.object(module, "run") as run:
        module.process_catalog(args, Path("/private"), "internal-books.json")
    if "--exclude-publish-opt-out" not in run.call_args.args[0]:
        raise AssertionError("internal catalog processing ignored publish opt-outs")
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


def check_publication_updates_pages(module: ModuleType) -> None:
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
            patch.object(
                module, "commit_private", return_value="b" * 40
            ) as commit_private,
            patch.object(module, "commit_public_index") as commit_public_index,
        ):
            module.main()
        processed = [call.args[2] for call in process_catalog.call_args_list]
        if processed != ["books.json", "internal-books.json"]:
            raise AssertionError("publication did not process both private catalogs")
        commit_private.assert_called_once_with(private_root)
        commit_public_index.assert_called_once_with("b" * 40)


def write_release_book(
    private_root: Path, catalog_name: str, book_id: str, publish: bool | None
) -> None:
    generated_dir = private_root / "generated" / book_id
    generated_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "id": book_id,
        "title": book_id,
        "author": "Author",
        "generation": {"fullBook": True},
        "segments": [{"id": "s001", "startSec": 0, "endSec": 1, "text": "Text"}],
        "audioChunks": [
            {
                "id": "chunk-001",
                "path": (
                    "https://github.com/SichangHe/adiob/releases/download/"
                    f"audiobooks-v1/{book_id}-chunk-001.m4a"
                ),
                "startSec": 0,
                "endSec": 1,
                "segmentStart": 0,
                "segmentCount": 1,
                "sha256": "a" * 64,
                "sizeBytes": 1,
            }
        ],
    }
    (generated_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    path = private_root / catalog_name
    catalog: dict[str, object] = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.is_file()
        else {"books": []}
    )
    book: dict[str, object] = {
        "id": book_id,
        "title": book_id,
        "author": "Author",
        "text": f"cleaned-texts/{book_id}.txt",
        "generated": {
            "manifest": f"generated/{book_id}/manifest.json",
            "cover": f"generated/{book_id}/cover.svg",
        },
    }
    if publish is not None:
        book["publish"] = publish
    books = catalog.get("books")
    if not isinstance(books, list):
        raise AssertionError("test catalog is invalid")
    books.append(book)
    path.write_text(json.dumps(catalog), encoding="utf-8")


def check_private_catalog_union() -> None:
    stager = load_script("stage-private-book-artifacts.py", "private_book_stager_union")
    setter = load_script("set-private-books-publish.py", "private_book_publish_setter")
    with tempfile.TemporaryDirectory(prefix="adiob-private-catalog-union-") as tmp:
        root = Path(tmp)
        private_root = root / "private"
        site_root = root / "site"
        (site_root / "data").mkdir(parents=True)
        private_root.mkdir()
        (site_root / "data/books.json").write_text(
            json.dumps(
                {
                    "defaultBook": "public-book",
                    "books": [
                        {
                            "id": "public-book",
                            "title": "Public Book",
                            "author": "Author",
                            "manifest": "manifest.json",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        write_release_book(private_root, "books.json", "primary-book", None)
        write_release_book(private_root, "books.json", "opt-out-book", False)
        write_release_book(private_root, "internal-books.json", "internal-book", None)
        setter_args = SimpleNamespace(private_root=private_root, dry_run=False)
        with patch.object(setter, "parse_args", return_value=setter_args):
            setter.main()
        for name in ("books.json", "internal-books.json"):
            catalog = json.loads((private_root / name).read_text(encoding="utf-8"))
            if catalog["books"][0].get("publish") is not True:
                raise AssertionError(f"{name} entry was not made publishable")
        primary = json.loads((private_root / "books.json").read_text(encoding="utf-8"))
        if primary["books"][1].get("publish") is not False:
            raise AssertionError("explicit publish opt-out was not preserved")
        stage_args = SimpleNamespace(
            private_root=private_root,
            site_root=site_root,
            reader_path="field-notes-819a",
            artifact_subdir="artifacts",
            segment_page_size=48,
        )
        with patch.object(stager, "parse_args", return_value=stage_args):
            stager.main()
        staged = json.loads(
            (site_root / "field-notes-819a/catalog.json").read_text(encoding="utf-8")
        )
        staged_ids = {book["id"] for book in staged["books"]}
        if staged_ids != {
            "public-book",
            "primary-book",
            "opt-out-book",
            "internal-book",
        }:
            raise AssertionError(
                "staged catalog did not contain the source catalog union"
            )
        internal_manifest = json.loads(
            (site_root / "artifacts/internal-book/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        if internal_manifest["audioChunks"][0]["path"] != (
            "https://github.com/SichangHe/adiob/releases/download/"
            "audiobooks-v1/internal-book-chunk-001.m4a"
        ):
            raise AssertionError("staging changed the public release URL")
        publisher = load_publisher()
        publisher.require_staged_ids(staged, staged_ids)
        expect_exit(
            lambda: publisher.require_staged_ids(staged, staged_ids | {"missing"})
        )
        expect_exit(
            lambda: publisher.require_staged_ids(staged, staged_ids - {"internal-book"})
        )
        duplicated_staged = {"books": [*staged["books"], staged["books"][0]]}
        expect_exit(lambda: publisher.require_staged_ids(duplicated_staged, staged_ids))

        duplicate = json.loads(
            (private_root / "internal-books.json").read_text(encoding="utf-8")
        )
        duplicate["books"][0]["id"] = "primary-book"
        (private_root / "internal-books.json").write_text(
            json.dumps(duplicate), encoding="utf-8"
        )
        expect_exit(lambda: stager.private_catalog_books(private_root))
        original = (private_root / "books.json").read_text(encoding="utf-8")
        with patch.object(setter, "parse_args", return_value=setter_args):
            expect_exit(setter.main)
        if (private_root / "books.json").read_text(encoding="utf-8") != original:
            raise AssertionError("duplicate catalog ids mutated a source catalog")


def main() -> None:
    module = load_extractor()
    check_output_boundary(module)
    check_epub_uri_paths(module)
    check_pdf_ocr_fallback(module)
    check_listenable_layout(load_cleaner())
    check_exporter_boundaries(load_exporter())
    release = load_release_processor()
    check_release_voice(release)
    check_release_integrity_relink(release)
    check_release_conflict(release)
    check_internal_release_policy(release)
    publisher = load_publisher()
    check_publisher_boundaries(publisher)
    check_pages_workflow(publisher)
    check_public_index_commit(publisher)
    check_private_commit_is_remote(publisher)
    check_catalog_command_modes(publisher)
    check_resume_requires_review(publisher)
    check_publication_updates_pages(publisher)
    check_private_catalog_union()


if __name__ == "__main__":
    main()
