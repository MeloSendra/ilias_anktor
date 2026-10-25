from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_SUFFIXES = {".md", ".markdown", ".pdf", ".txt"}


@dataclass(frozen=True)
class Card:
    front: str
    back: str
    source: str
    identifier: str


def read_sources(root: Path) -> list[tuple[Path, str]]:
    if not root.is_dir():
        raise ValueError(f"input directory does not exist: {root}")

    sources: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            try:
                if path.suffix.lower() == ".pdf":
                    text = _read_pdf(path)
                else:
                    text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError(f"file is not valid UTF-8: {path}") from exc
            except OSError as exc:
                raise ValueError(f"cannot read file: {path}") from exc
            sources.append((path, text))
    return sources


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError, PdfStreamError
        pages = PdfReader(path).pages
        sections = []
        for index, page in enumerate(pages, 1):
            text = (page.extract_text() or "").strip()
            if text:
                sections.append(f"# Page {index}\n{text}")
        return "\n\n".join(sections)
    except ImportError as exc:
        raise ValueError("PDF support requires the pypdf package") from exc
    except (OSError, PdfReadError, PdfStreamError) as exc:
        raise ValueError(f"cannot extract text from PDF: {path}") from exc


def generate_cards(sources: list[tuple[Path, str]], root: Path) -> list[Card]:
    cards: list[Card] = []
    for path, text in sources:
        relative = path.relative_to(root).as_posix()
        lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        headings = [
            (index, re.sub(r"^#+\s*", "", line).strip())
            for index, line in enumerate(lines)
            if re.match(r"^#{1,6}\s+\S", line)
        ]
        for heading_index, (line_number, heading) in enumerate(headings):
            end = headings[heading_index + 1][0] if heading_index + 1 < len(headings) else len(lines)
            body = "\n".join(lines[line_number + 1 : end]).strip()
            body = re.sub(r"^\s*[-*]\s+", "", body, flags=re.MULTILINE)
            if not body:
                continue
            identifier = hashlib.sha256(f"{relative}\0{heading}".encode()).hexdigest()
            cards.append(Card(heading, f"{body}\n\nSource: {relative}", relative, identifier))

    return cards
