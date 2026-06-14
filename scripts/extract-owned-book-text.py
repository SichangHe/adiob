#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html.parser
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit


class TextHtmlParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag in {"script", "style"}:
            self.skip_depth += 1
        if tag in {"br", "p", "div", "section", "article", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
        if tag in {"p", "div", "section", "article", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract local owned-access book text into ignored owned-text/."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--confirm-local-owned-use",
        action="store_true",
        help="Confirm output stays local-only and is not publishable repo content.",
    )
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def root_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return repo_root() / path


def lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(root_path(path)))


def resolved_under(path: Path, parent: Path) -> bool:
    return (
        root_path(path)
        .resolve(strict=False)
        .is_relative_to(parent.resolve(strict=False))
    )


def lexical_under(path: Path, parent: Path) -> bool:
    return lexical_path(path).is_relative_to(lexical_path(parent))


def strictly_under(path: Path, parent: Path) -> bool:
    return lexical_under(path, parent) and resolved_under(path, parent)


def require_boundary(out: Path) -> Path:
    root = repo_root()
    private_root = root / "owned-text"
    out_path = root_path(out)
    if not strictly_under(out_path, private_root):
        raise SystemExit("output text must be under ignored `owned-text/`")
    if out_path.is_symlink():
        raise SystemExit(f"refusing to overwrite symlink output: {out_path}")
    reject_symlink_ancestors(out_path, private_root)
    return out_path


def reject_symlink_ancestors(path: Path, parent: Path) -> None:
    parent = root_path(parent)
    path = root_path(path)
    if parent.is_symlink():
        raise SystemExit(f"refusing symlink private root: {parent}")
    rel_parent = lexical_path(path).parent.relative_to(lexical_path(parent))
    current = parent
    for part in rel_parent.parts:
        current /= part
        if current.is_symlink():
            raise SystemExit(f"refusing symlink output parent: {current}")


def write_owned_file(path: Path, text: str) -> None:
    out = require_boundary(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink_ancestors(out, repo_root() / "owned-text")
    if out.is_symlink():
        raise SystemExit(f"refusing to overwrite symlink output: {out}")
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=out.parent,
        prefix=f".{out.name}.",
        delete=False,
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, out)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\f", "\n\n")
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def extract_pdf(source: Path) -> str:
    if shutil.which("pdftotext") is None:
        raise SystemExit("pdftotext is required for PDF extraction")
    result = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", "-layout", str(source), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def opf_path(epub: zipfile.ZipFile) -> str:
    container = ET.fromstring(epub.read("META-INF/container.xml"))
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfile = container.find(".//c:rootfile", ns)
    if rootfile is None:
        raise SystemExit("EPUB container has no rootfile")
    path = rootfile.attrib.get("full-path")
    if not path:
        raise SystemExit("EPUB rootfile is missing full-path")
    return epub_member("", path)


def epub_member(base: str, href: str) -> str:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        raise SystemExit(f"EPUB path must be relative: {href}")
    path = unquote(parsed.path)
    if path.startswith("/") or "\\" in path:
        raise SystemExit(f"unsafe EPUB path: {href}")
    member = posixpath.normpath(posixpath.join(base, path))
    if member in {"", ".", ".."} or member.startswith("../"):
        raise SystemExit(f"unsafe EPUB path: {href}")
    return member


def html_text(raw: bytes) -> str:
    parser = TextHtmlParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return parser.text()


def extract_epub(source: Path) -> str:
    with zipfile.ZipFile(source) as epub:
        opf = opf_path(epub)
        base = posixpath.dirname(opf)
        package = ET.fromstring(epub.read(opf))
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        items = {
            item.attrib["id"]: item.attrib["href"]
            for item in package.findall(".//opf:manifest/opf:item", ns)
            if item.attrib.get("media-type") in {"application/xhtml+xml", "text/html"}
        }
        parts = []
        for itemref in package.findall(".//opf:spine/opf:itemref", ns):
            href = items.get(itemref.attrib.get("idref", ""))
            if href:
                parts.append(html_text(epub.read(epub_member(base, href))))
        return "\n\n".join(parts)


def extract_docx(source: Path) -> str:
    with zipfile.ZipFile(source) as docx:
        xml = ET.fromstring(docx.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for para in xml.findall(".//w:p", ns):
        text = "".join(node.text or "" for node in para.findall(".//w:t", ns))
        if text.strip():
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def extract_mobi(source: Path) -> str:
    if shutil.which("ebook-convert") is None:
        raise SystemExit("MOBI extraction requires Calibre `ebook-convert`")
    with tempfile.TemporaryDirectory(prefix="adiob-mobi-") as tmp:
        out = Path(tmp) / "book.txt"
        subprocess.run(
            ["ebook-convert", str(source), str(out)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        return out.read_text(encoding="utf-8", errors="replace")


def extract_text(source: Path) -> str:
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(source)
    if suffix == ".epub":
        return extract_epub(source)
    if suffix == ".docx":
        return extract_docx(source)
    if suffix == ".mobi":
        return extract_mobi(source)
    if suffix in {".txt", ".md"}:
        return source.read_text(encoding="utf-8", errors="replace")
    raise SystemExit(f"unsupported book format: {suffix}")


def main() -> None:
    args = parse_args()
    if not args.confirm_local_owned_use:
        raise SystemExit("pass --confirm-local-owned-use for local owned-book text")
    source = root_path(args.source)
    if not source.is_file():
        raise SystemExit(f"source file does not exist: {source}")
    text = normalize_text(extract_text(source))
    if len(text.strip()) < 100:
        raise SystemExit("extracted text is unexpectedly short")
    out = require_boundary(args.out)
    write_owned_file(out, text)
    print(f"wrote {out.relative_to(repo_root())} ({len(text)} chars)")


if __name__ == "__main__":
    main()
