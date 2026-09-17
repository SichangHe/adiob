#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
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
    "book": BookCut(
        151,
        1081,
        "Removed publication data, publisher foreword, contents, and third-party promotional back matter; retained the autobiography and author's postscript.",
    ),
    "book-docx": BookCut(
        59,
        2763,
        "Removed title/front matter; retained all twenty-eight biography chapters through the subject's death.",
    ),
    "business-notes-writing-personal-notes-that-build-professional-relationships": BookCut(
        250,
        5265,
        "Removed publisher/catalog/front matter and rear jacket/ad copy; retained body chapters and reference library.",
    ),
    "california-driver-handbook-10-01-24-passed-accessible-dl-600-rev-1-2024": BookCut(
        142,
        3060,
        "Removed cover, letter, copyright notice, contents, and rear advertisements; retained all fourteen handbook sections through the glossary.",
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
    "dokumen-pub-the-man-who-solved-the-market-how-jim-simons-launched-the-quant-revolution-hardcovernbsped-073521798x-9780735217980": BookCut(
        275,
        4367,
        "Removed publication/front matter, contents, photograph captions, acknowledgments, appendices, notes, and index; retained the introduction through epilogue.",
    ),
    "elon-musk-pdf-room": BookCut(
        11,
        16934,
        "Removed acknowledgments, notes, illustration credits, and index; retained the prologue and complete narrative.",
    ),
    "man-s-search-for-meaning": BookCut(
        283,
        3560,
        "Removed publisher/catalog/front matter and about-author back matter; retained the two main parts and postscript.",
    ),
    # 🧑 "OCR my new book the mythical man month and put the text in the usual place"
    "mythical-man-month": BookCut(
        66,
        5308,
        "Removed publication data, contents, notes, index, and back-cover copy; retained the preface, fifteen essays, and epilogue.",
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
    "the-intelligent-investor-benjamin-graham": BookCut(
        334,
        22092,
        "Removed publication/front matter, contents, preface, appendixes, endnotes, acknowledgments, and index; retained the introduction and twenty chapters.",
    ),
    "the-art-of-computer-programming": BookCut(
        165,
        33826,
        "Removed cover, damaged contents pages, appendixes dominated by formulas/tables, and bilingual name/index table; retained all three chapters.",
    ),
    "understanding-power": BookCut(
        348,
        17637,
        "Removed title/front matter/table of contents and index; retained all available body text. Source lacks actual Chapter One text.",
    ),
    "road-sign-chart-dl-37-r11-2009-english-secured": BookCut(
        8,
        48,
        "Removed document headers and revision footers; retained all warning, regulatory, construction, and guide sign labels.",
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
    "words-and-phrases-for-class-c-driving-tests-dl-80-en-r3-2022-access-secured": BookCut(
        1,
        65,
        "Retained all seven driving-test sections and removed the duplicate revision footer.",
    ),
}

PAGE_NUMBER = re.compile(r"^(?:\d+|[ivxlcdm]+)$", re.IGNORECASE)
SPLIT_INITIAL_CAP = re.compile(r"\b([A-Z])\s+([A-Z]{4,})\b")
INLINE_ARTIFACTS = (
    re.compile(r"\s*www\.fx1618\.com\s*", re.IGNORECASE),
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
    "B The Whole and the Parts",
    "n PlantoThrow One Away",
    "Passing the Ward",
    "PlantoThrow One Away",
    "TheTarPit",
    "Understanding Power",
    "Man's Search for Meaning",
    "Moral Mazes",
    "This page intentionally left blank",
    "[ Team LiB ]",
}
LISTENABLE_BOOK_TITLES = {
    "mythical-man-month": "The Mythical Man-Month",
    "the-intelligent-investor-benjamin-graham": "The Intelligent Investor",
}
LISTENABLE_LINE_RANGES: dict[str, tuple[tuple[int, int, str | None], ...]] = {
    "mythical-man-month": (
        (160, 185, None),
        (
            239,
            269,
            "The diagram labeled Figure 1.1 shows how a program becomes a tested, documented "
            "programming product and an integrated programming-system "
            "component, with the complete programming-systems product "
            "combining both transformations.",
        ),
        (
            434,
            499,
            "A reproduced restaurant menu illustrates the chapter's point "
            "that good work takes time. Its itemized visual layout has been "
            "replaced with this description for listening.",
        ),
        (608, 612, "The chart labeled Figure 2.1 plots less completion time as independent workers are added to a perfectly partitionable task."),
        (623, 630, "The chart labeled Figure 2.2 shows that adding workers does not shorten a task constrained to sequential steps."),
        (640, 643, "The chart labeled Figure 2.3 shows diminishing time savings when a partitionable task also requires communication."),
        (661, 667, "The chart labeled Figure 2.4 shows that complex interrelationships can erase the time saved by adding workers."),
        (792, 825, "The charts labeled Figures 2.5 through 2.7 compare the planned schedule with two consequences of missing the first milestone."),
        (870, 882, "The chart labeled Figure 2.8 shows that adding and training programmers after a delay can leave the project just as late."),
        (1173, 1176, "The diagram labeled Figure 3.1 compares the many communication paths in an ordinary ten-person team with the simpler paths in a surgical team."),
        (1567, 1591, "A chapter-opening illustration appears here in the source and has been omitted for listening."),
        (1788, 1800, "A chapter-opening illustration appears here in the source and has been omitted for listening."),
        (2559, 2566, "A chapter-opening illustration appears here in the source and has been omitted for listening."),
        (2623, 2659, "The chart labeled Figure 8.1 graphs programming effort rising faster than program size, approximately with size to the power of one point five."),
        (2720, 2741, "The table labeled Figure 8.2 summarizes four programming jobs, contrasting lower productivity for control programs with higher productivity for translators."),
        (2762, 2768, "The charts labeled Figures 8.3 and 8.4 compare predicted and actual programming and debugging rates over time."),
        (3401, 3413, "The diagram labeled Figure 11.1 shows parallel managerial and technical career ladders, with corresponding levels intended to have equal standing."),
        (3484, 3489, "The chart labeled Figure 11.2 graphs bugs found per month across the months after a software release."),
        (3671, 3676, "The chart labeled Figure 12.1 shows target-machine use staying low at first and then rising sharply as components become ready for debugging."),
        (3909, 3921, "The table labeled Figure 12.2 compares four projects and indicates substantially higher productivity with conversational development than with batch development."),
        (4322, 4325, "A chapter-opening illustration appears here in the source and has been omitted for listening."),
        (4534, 4716, "The table labeled Figure 14.1 is a milestone report comparing scheduled and estimated dates. It exposes late approvals and tests so managers can act before small delays accumulate."),
        (
            4896,
            4925,
            "The diagram labeled Figure 15.1 is a program-structure graph showing linked routines, "
            "modes, messages, and external variables. Its visual layout has "
            "been replaced with this description for listening.",
        ),
        (5007, 5029, "A flow-chart fragment appears here in the source. Its unreadable visual layout has been omitted for listening."),
        (
            5073,
            5214,
            "The listing labeled Figure 15.3 is a self-documenting program whose labels, "
            "names, indentation, and comments illustrate the techniques "
            "described in the surrounding text. The code layout has been "
            "replaced with this description for listening.",
        ),
    ),
}
LISTENABLE_SOURCE_SHA256 = {
    "mythical-man-month": "2c496b5a7e97cbd97ddd5eeb11544a0fd20da223be58ad090d6ac7c971ee5a8d",
}
ORDINALS = (
    "",
    "First",
    "Second",
    "Third",
    "Fourth",
    "Fifth",
    "Sixth",
    "Seventh",
    "Eighth",
    "Ninth",
    "Tenth",
    "Eleventh",
    "Twelfth",
    "Thirteenth",
    "Fourteenth",
    "Fifteenth",
    "Sixteenth",
    "Seventeenth",
    "Eighteenth",
    "Nineteenth",
    "Twentieth",
)
LAYOUT_BLOCK = re.compile(
    r"^(TABLE|FIGURE|FIG\.)\s+([A-Za-z0-9.-]+)(?:\s+(.*))?$", re.IGNORECASE
)
PAGE_FURNITURE = re.compile(r"^(?:\d{1,3}\s+)?[A-Za-z0-9][A-Za-z0-9’'&, -]{2,80}\s+\d{1,3}$")
LEADING_PAGE_FURNITURE = re.compile(
    r"^\d{1,3}\s+[A-Za-z][A-Za-z0-9’'&, ?-]{2,80}$"
)
ROMAN_PAGE_FURNITURE = re.compile(
    r"^(?:[ivxlcdm]+\s+[A-Za-z][A-Za-z ]+|[A-Za-z][A-Za-z ]+\s+[ivxlcdm]+)$",
    re.IGNORECASE,
)
DATA_HEADER = re.compile(r"^(?:Standard & Poor’s )?DJIA\s+500-Stock Composite$")

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
    parser.add_argument(
        "--book-id",
        action="append",
        default=[],
        help="Clean only this catalog book ID; may be repeated.",
    )
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
    text = re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", text)
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


def layout_description(paragraph: str) -> str | None:
    match = LAYOUT_BLOCK.fullmatch(paragraph)
    if match is None:
        return None
    kind, identifier, content = match.groups()
    caption = ""
    if content:
        caption = re.split(r"\s(?=[+–−-]?\$?\d)", content, maxsplit=1)[0]
        caption = " ".join(caption.split()[:12]).rstrip(" ,.;:")
        words = re.findall(r"[A-Za-z][A-Za-z’'-]*", caption)
        uppercase_words = [word for word in words if len(word) >= 4 and word.isupper()]
        incomplete_endings = {"and", "at", "between", "for", "from", "in", "of", "the", "to"}
        if (
            not 3 <= len(words) <= 12
            or len(uppercase_words) >= 2
            or words[-1].casefold() in incomplete_endings
        ):
            caption = ""
    label = "Figure" if kind.casefold().startswith("fig") else "Table"
    name = f"{label} {identifier}"
    if caption:
        name += f", titled {caption}"
    return (
        f"{name}, presents the comparison discussed in the surrounding text. "
        "Its visual row-and-column or graphical layout has been replaced with "
        "this description for listening."
    )


def narrative_paragraph(paragraph: str) -> bool:
    words = re.findall(r"\b[\w’'-]+\b", paragraph)
    letters = sum(character.isalpha() for character in paragraph)
    return (
        len(words) >= 10
        and re.search(r"[.!?](?:\s|$)", paragraph) is not None
        and letters >= len(paragraph) // 2
    )


def numeric_layout(paragraph: str) -> bool:
    numbers = re.findall(r"(?<!\w)[+–−-]?\$?\d+(?:[.,/]\d+)*(?:%|\b)", paragraph)
    words = re.findall(r"\b[\w’'-]+\b", paragraph)
    return (
        len(numbers) >= 2
        and len(numbers) * 2 >= len(words)
        and re.search(r"[.!?](?:\s|$)", paragraph) is None
    )


def ordered_list(paragraph: str) -> list[str] | None:
    matches = list(
        re.finditer(r"(?<!\w)(\d{1,2})\.\s+(?=[A-Z“\"(])", paragraph)
    )
    numbers = [int(match.group(1)) for match in matches]
    if (
        len(matches) < 2
        or max(numbers) >= len(ORDINALS)
        or numbers != list(range(numbers[0], numbers[0] + len(numbers)))
    ):
        return None
    parts: list[str] = []
    lead = paragraph[: matches[0].start()].strip()
    if lead:
        parts.append(lead)
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else None
        item = paragraph[match.end() : end].strip()
        parts.append(f"{ORDINALS[numbers[index]]}, {item}")
    return parts


# 🧑 "re-OCR and re-audiolize the intelligent investor because it contains too many tables etc. that screw up the listening"
def listenable_text(book_id: str, text: str) -> str:
    title = LISTENABLE_BOOK_TITLES.get(book_id)
    if title is None:
        return text
    paragraphs: list[str] = []
    layout_pending = False
    layout_notice = (
        "A visual data layout appears here in the source. Its rows and columns "
        "have been omitted for listening; the surrounding text states the "
        "comparison and conclusion."
    )
    for paragraph in text.split("\n\n"):
        if (
            (
                PAGE_FURNITURE.fullmatch(paragraph)
                or LEADING_PAGE_FURNITURE.fullmatch(paragraph)
                or ROMAN_PAGE_FURNITURE.fullmatch(paragraph)
            )
            and not paragraph.startswith("CHAPTER ")
        ):
            continue
        if DATA_HEADER.fullmatch(paragraph):
            continue
        if (
            paragraphs
            and paragraphs[-1].replace(" ", "").casefold()
            == paragraph.replace(" ", "").casefold()
        ):
            continue
        description = layout_description(paragraph)
        if description is not None:
            paragraphs.append(description)
            layout_pending = True
            continue
        if layout_pending:
            if paragraph.startswith(("Note:", "Notes:", "Source:")):
                paragraphs.append(paragraph)
                continue
            if not narrative_paragraph(paragraph):
                continue
            layout_pending = False
        if numeric_layout(paragraph):
            if not paragraphs or paragraphs[-1] != layout_notice:
                paragraphs.append(layout_notice)
            continue
        ordered_parts = ordered_list(paragraph)
        if ordered_parts is not None:
            paragraphs.extend(ordered_parts)
            continue
        parts = paragraph.split("•")
        if parts[0].strip():
            paragraphs.append(parts[0].strip())
        paragraphs.extend(
            f"Bullet point: {part.strip()}" for part in parts[1:] if part.strip()
        )
    body = "\n\n".join(paragraphs).strip()
    return (
        f"Below is document {title}.\n\n{body}\n\n"
        f"Above was document {title}.\n"
    )


def body_text(source: Path, book_id: str, cut: BookCut) -> str:
    paragraphs: list[str] = []
    lines: list[str] = []
    line_fixes = LINE_FIXES.get(book_id, {})
    listenable_ranges = LISTENABLE_LINE_RANGES.get(book_id, ())
    if listenable_ranges:
        expected_sha256 = LISTENABLE_SOURCE_SHA256[book_id]
        actual_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise SystemExit(
                f"listening cleanup ranges require source {expected_sha256}, got "
                f"{actual_sha256}: {source}"
            )

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
            range_match = next(
                (
                    (start_line, replacement)
                    for start_line, end_line, replacement in listenable_ranges
                    if start_line <= line_number <= end_line
                ),
                None,
            )
            if range_match is not None:
                start_line, replacement = range_match
                if line_number == start_line:
                    flush()
                    if replacement is not None:
                        paragraphs.append(replacement)
                continue
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
    return listenable_text(book_id, text + "\n")


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
    selected = set(args.book_id)
    available = {
        book.get("id")
        for book in books
        if isinstance(book, dict)
        and isinstance(book.get("id"), str)
        and book.get("id") != CONTRARIAN_ID
    }
    missing = selected - available
    if missing:
        raise SystemExit(f"unknown catalog book ID: {sorted(missing)[0]}")
    outputs: list[tuple[dict[str, Any], str, Path, str]] = []
    for book in books:
        if not isinstance(book, dict):
            continue
        book_id = book.get("id")
        if not isinstance(book_id, str) or book_id == CONTRARIAN_ID:
            continue
        if selected and book_id not in selected:
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
