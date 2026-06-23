#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PRIVATE_ROOT = Path("../adiob-private-artifacts")
CONTRARIAN_ID = "the-contrarian"


@dataclass(frozen=True)
class BookCut:
    start_line: int
    end_line: int
    note: str


CUTS = {
    "business-notes-writing-personal-notes-that-build-professional-relationships": BookCut(
        250,
        5265,
        "Removed publisher/catalog/front matter and rear jacket/ad copy; retained body chapters and reference library.",
    ),
    "clean-code-a-handbook-of-agile-software-craftsmanship-robert-c-martin": BookCut(
        843,
        19259,
        "Removed front matter, table of contents, cover copy, and index; retained chapters, appendices, and epilogue.",
    ),
    "design-patterns-elements-of-reusable-object-oriented-software-erich-gamma-richard-helm-john-vlissides-ralph-johnson": BookCut(
        469,
        13915,
        "Removed front matter, table of contents, bibliography, and index; retained chapters and appendices.",
    ),
    "designing-interfaces-second-edition-jenifer-tidwell": BookCut(
        810,
        14926,
        "Removed introduction/preface/table of contents and index/about/colophon; retained chapters and further reading.",
    ),
    "domain-driven-design-tackling-complexity-in-the-heart-of-software-eric-evans": BookCut(
        898,
        15005,
        "Removed title/front matter/table of contents and appendix; retained chapters and epilogues.",
    ),
    "man-s-search-for-meaning": BookCut(
        283,
        3560,
        "Removed publisher/catalog/front matter and about-author back matter; retained the two main parts and postscript.",
    ),
    "never-split-the-difference-negotiating-as-if-your-life-depended-on-it-chris-voss-and-tahl-raz-harpercollins": BookCut(
        58,
        9491,
        "Removed table of contents and notes/index/publisher back matter; retained chapters, acknowledgments, and appendix.",
    ),
    "outliers": BookCut(
        203,
        10094,
        "Removed title/copyright/front matter and notes/acknowledgments/index; retained introduction, chapters, and epilogue.",
    ),
    "the-elements-of-style": BookCut(
        59,
        1765,
        "Removed title page and table of contents; retained all chapter body text through the end marker.",
    ),
    "understanding-power": BookCut(
        348,
        17637,
        "Removed title/front matter/table of contents and index; retained all available body text. Source lacks actual Chapter One text.",
    ),
    "vdoc-pub-moral-mazes": BookCut(
        129,
        1816,
        "Removed title/front matter/table of contents and notes/index/back matter; retained introduction, chapters, afterword, and author's note.",
    ),
    "vdoc-pub-moral-mazes-pdf": BookCut(
        266,
        18158,
        "Removed title/front matter/table of contents and notes/index/back matter; retained introduction, chapters, afterword, and author's note.",
    ),
    "walden": BookCut(
        32,
        9263,
        "Removed biographical front matter and QCEnglish disclaimer back matter; retained all chapters.",
    ),
}

PAGE_NUMBER = re.compile(r"^(?:\d+|[ivxlcdm]+)$", re.IGNORECASE)
SPLIT_INITIAL_CAP = re.compile(r"\b([A-Z])\s+([A-Z]{4,})\b")
INLINE_ARTIFACTS = (
    re.compile(r"\s*Download from Wow! eBook <www\.wowebook\.com>\s*"),
    re.compile(r"\s*\[ Team LiB \]\s*"),
    re.compile(r"\bThis page intentionally left blank\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+Understanding Power\b"),
    re.compile(r"\b\d+\s+FOUNDATION CLASSES APPENDIX C\b"),
    re.compile(r"\b\d{1,4}\s+Chapter\s+\d+:\s+[^.\n]{1,120}"),
    re.compile(r"\b\d{1,4}\s+References\b"),
    re.compile(r"\b(?:\d+|[ilno]+\d+|[ilno]?\d+o)\s+Man's Search for Meaning\b"),
    re.compile(r"\bLogotherapy in a Nutshell\s+(?:\d+|[ilno]\d+|[mn])\b"),
    re.compile(r"\bExperiences in a Concentration Camp\s+\d+\b"),
    re.compile(r"\bT HE E ND\b"),
)
ARTIFACT_PARAGRAPHS = {
    "Understanding Power",
    "Man's Search for Meaning",
    "Moral Mazes",
    "This page intentionally left blank",
    "[ Team LiB ]",
}

LINE_FIXES: dict[str, dict[int, str | None]] = {
    "man-s-search-for-meaning": {
        3536: None,
        3539: "You may be prone to blame me for invoking examples",
        3540: 'that are the exceptions to the rule. "Sed omnia praeclara tam',
        3541: 'difficilia quam rara sunt" (but everything great is just as',
        3542: "difficult to realize as it is rare to find) reads the last",
        3543: "sentence of the Ethics of Spinoza. You may of course ask",
        3544: 'whether we really need to refer to "saints." Wouldn\'t it',
        3545: "suffice just to refer to decent people? It is true that they",
        3546: "form a minority. More than that, they always will remain a",
        3547: "minority. And yet I see therein the very challenge to join",
        3548: "the minority. For the world is in a bad state, but everything",
        3549: "will become still worse unless each of us does his best.",
        3550: None,
        3551: "So, let us be alert\u2014alert in a twofold sense: Since",
        3552: None,
        3553: "Auschwitz we know what man is capable of. And",
        3554: None,
        3555: "since Hiroshima we know what is at stake.",
        3556: None,
        3557: None,
        3558: None,
        3559: None,
        3560: None,
    }
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write cleaned private-book body text and update the private catalog."
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


def temp_file(path: Path, text: str) -> Path:
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
    return tmp_path


def write_file(path: Path, text: str) -> None:
    os.replace(temp_file(path, text), path)


def write_json(path: Path, value: dict[str, Any]) -> None:
    write_file(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def clean_line(text: str) -> str:
    text = text.replace("\xad", "")
    text = re.sub(r"\s+", " ", text).strip()
    return SPLIT_INITIAL_CAP.sub(r"\1\2", text)


def remove_artifacts(text: str) -> str:
    for pattern in INLINE_ARTIFACTS:
        text = pattern.sub(" ", text)
    return clean_line(text)


def paragraph_text(lines: list[str]) -> str:
    parts: list[str] = []
    for line in lines:
        if parts and parts[-1].endswith("-") and line[:1].islower():
            parts[-1] = parts[-1][:-1] + line
        else:
            parts.append(line)
    return remove_artifacts(" ".join(parts))


def body_text(source: Path, book_id: str, cut: BookCut) -> str:
    paragraphs: list[str] = []
    lines: list[str] = []
    line_fixes = LINE_FIXES.get(book_id, {})

    def flush() -> None:
        nonlocal lines
        if lines:
            paragraphs.append(paragraph_text(lines))
            lines = []

    with source.open(encoding="utf-8") as source_file:
        for line_number, line in enumerate(source_file, start=1):
            if line_number < cut.start_line:
                continue
            if line_number > cut.end_line:
                break
            fixed = line_fixes.get(line_number, line)
            if fixed is None:
                continue
            text = clean_line(fixed)
            if not text or PAGE_NUMBER.fullmatch(text):
                flush()
                continue
            lines.append(text)
    flush()
    text = "\n\n".join(
        paragraph
        for paragraph in paragraphs
        if paragraph and paragraph not in ARTIFACT_PARAGRAPHS
    ).strip()
    for pattern in INLINE_ARTIFACTS:
        text = pattern.sub(" ", text)
    text = "\n\n".join(
        clean_line(paragraph)
        for paragraph in text.split("\n\n")
        if clean_line(paragraph) and clean_line(paragraph) not in ARTIFACT_PARAGRAPHS
    ).strip()
    if not text:
        raise SystemExit(f"cleaned text is empty: {source}")
    return text + "\n"


def raw_text_path(private_root: Path, book_id: str) -> Path:
    path = private_root / "texts" / f"{book_id}.txt"
    if not path.is_file():
        raise SystemExit(f"missing raw source text: {path}")
    return path


def main() -> None:
    args = parse_args()
    private_root = require_private_root(args.private_root)
    catalog_path = private_root / "books.json"
    catalog = read_json(catalog_path)
    books = catalog.get("books")
    if not isinstance(books, list):
        raise SystemExit("private catalog must contain a books list")
    outputs: list[tuple[dict[str, Any], str, Path, str]] = []
    for book in books:
        if not isinstance(book, dict):
            continue
        book_id = book.get("id")
        if not isinstance(book_id, str) or book_id == CONTRARIAN_ID:
            continue
        cut = CUTS.get(book_id)
        if cut is None:
            raise SystemExit(f"missing cleanup cut for {book_id}")
        source = raw_text_path(private_root, book_id)
        target_rel = Path("cleaned-texts") / f"{book_id}.txt"
        text = body_text(source, book_id, cut)
        outputs.append((book, book_id, target_rel, text))
    pending: list[tuple[Path, Path]] = []
    catalog_tmp: Path | None = None
    if not args.dry_run:
        try:
            for book, _, target_rel, text in outputs:
                book["text"] = target_rel.as_posix()
                target = private_root / target_rel
                pending.append((target, temp_file(target, text)))
            catalog_tmp = temp_file(
                catalog_path,
                json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
            )
        except Exception:
            for _, tmp_path in pending:
                tmp_path.unlink(missing_ok=True)
            if catalog_tmp is not None:
                catalog_tmp.unlink(missing_ok=True)
            raise
        for target, tmp_path in pending:
            os.replace(tmp_path, target)
        os.replace(catalog_tmp, catalog_path)
    for book, book_id, target_rel, text in outputs:
        print(f"cleaned {book_id}: {CUTS[book_id].note}")
    print(f"cleaned {len(outputs)} books")


if __name__ == "__main__":
    main()
