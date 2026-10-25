from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from getpass import getpass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable


SUPPORTED_SUFFIXES = {".html", ".htm", ".md", ".markdown", ".pdf", ".txt"}
MAX_CARDS_PER_SOURCE = 150
MAX_CARDS_PER_COURSE = 300
MAX_GEMINI_CHARS = 12_000
GEMINI_TIMEOUT_SECONDS = 180
GEMINI_RETRIES = 5
SUMMARY_DIR = ".ilias-anktor-summaries"
TEXT_DIR = ".ilias-anktor-text"


@dataclass(frozen=True)
class Card:
    front: str
    back: str
    source: str
    identifier: str


def read_sources(root: Path) -> list[tuple[Path, str]]:
    return list(iter_sources(root))


def iter_sources(root: Path):
    if not root.is_dir():
        raise ValueError(f"input directory does not exist: {root}")

    for path in sorted(root.rglob("*")):
        if any(part in {SUMMARY_DIR, TEXT_DIR} for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            try:
                if path.suffix.lower() == ".pdf":
                    text = _read_pdf(path)
                elif path.suffix.lower() in {".html", ".htm"}:
                    text = _read_html(path)
                else:
                    text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError(f"file is not valid UTF-8: {path}") from exc
            except OSError as exc:
                raise ValueError(f"cannot read file: {path}") from exc
            yield path, text


def group_sources_by_course(
    sources: list[tuple[Path, str]], root: Path
) -> dict[str, list[tuple[Path, str]]]:
    """Group sources by their first path segment for the upcoming course picker."""
    grouped: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for path, text in sources:
        relative = path.relative_to(root)
        grouped[relative.parts[0] if relative.parts else root.name].append((path, text))
    return dict(sorted(grouped.items()))


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


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.heading: list[str] | None = None
        self.block: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._finish_block()
            self.heading = []
        elif tag == "li":
            self._finish_block()
            self.block = ["- "]

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            if self.heading is not None:
                self.heading.append(value)
            else:
                self.block.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self.heading is not None:
            heading = " ".join(self.heading).strip()
            if heading:
                self.lines.append(f"# {heading}")
            self.heading = None
        elif tag in {"p", "div", "li", "br"}:
            self._finish_block()

    def _finish_block(self) -> None:
        value = " ".join(self.block).strip()
        if value:
            self.lines.append(value)
        self.block = []

    def result(self) -> str:
        self._finish_block()
        return "\n".join(self.lines)


def _read_html(path: Path) -> str:
    try:
        parser = _HtmlText()
        parser.feed(path.read_text(encoding="utf-8-sig"))
        parser.close()
        return parser.result()
    except UnicodeDecodeError as exc:
        raise ValueError(f"file is not valid UTF-8: {path}") from exc
    except OSError as exc:
        raise ValueError(f"cannot read file: {path}") from exc


class GeminiAdapter:
    """Small, dependency-free adapter for Gemini's generateContent endpoint."""

    def __init__(self, api_key: str, model: str = "gemini-3.8-flash") -> None:
        if not api_key.strip():
            raise ValueError("Gemini API key cannot be empty")
        self.api_key = api_key
        self.model = model

    def generate(self, source: str, text: str) -> list[dict[str, str]]:
        prompt = (
            "Create the smallest useful set of high-value study cards from this course file. "
            "Return ONLY a JSON array of objects with exactly these string keys: "
            '"question" and "answer". Do not include markdown fences, explanations, or source text. '
            "Avoid trivial, duplicate, or ambiguous cards.\n\n"
            f"COURSE FILE: {source}\nCONTENT:\n{text}"
        )
        result = self._request(prompt)
        try:
            content = result["candidates"][0]["content"]["parts"][0]["text"]
            cards = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Gemini returned invalid card JSON") from exc
        if not isinstance(cards, list):
            raise ValueError("Gemini returned invalid card JSON")
        validated: list[dict[str, str]] = []
        for card in cards:
            if (
                not isinstance(card, dict)
                or set(card) != {"question", "answer"}
                or not isinstance(card["question"], str)
                or not isinstance(card["answer"], str)
                or not card["question"].strip()
                or not card["answer"].strip()
            ):
                raise ValueError("Gemini returned invalid card JSON")
            validated.append({"question": card["question"].strip(), "answer": card["answer"].strip()})
        return validated

    def summarize(self, source: str, text: str) -> str:
        prompt = (
            "Summarize this course file for later study-card generation. Keep definitions, "
            "rules, formulas, examples, and relationships. Remove repetition and navigation "
            "noise. Preserve important details. Return plain text only.\n\n"
            f"COURSE FILE: {source}\nCONTENT:\n{text}"
        )
        result = self._request(prompt)
        try:
            return result["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Gemini returned an invalid summary") from exc

    def _request(self, prompt: str) -> dict:
        payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        request = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        for attempt in range(GEMINI_RETRIES):
            try:
                with urllib.request.urlopen(request, timeout=GEMINI_TIMEOUT_SECONDS) as response:
                    result = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                try:
                    detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message")
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    detail = None
                if exc.code in {429, 503} and attempt + 1 < GEMINI_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                message = f"Gemini request failed (HTTP {exc.code})"
                raise ValueError(f"{message}: {detail}" if detail else message) from exc
            except (socket.timeout, TimeoutError) as exc:
                raise ValueError(
                    f"Gemini request timed out after {GEMINI_TIMEOUT_SECONDS} seconds"
                ) from exc
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                raise ValueError(f"Gemini request failed: {exc}") from exc
        return result


def _source_footer(relative: str) -> str:
    return f"Source: {' › '.join(Path(relative).parts)}"


def normalize_extracted_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    cleaned: list[str] = []
    previous = None
    for line in lines:
        if not line.strip():
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        if line.strip() == previous:
            continue
        cleaned.append(line)
        previous = line.strip()
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned)


def cache_text_source(path: Path, text: str, root: Path) -> tuple[Path, str]:
    relative = path.relative_to(root)
    cached_path = root / TEXT_DIR / relative.with_suffix(".txt")
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_extracted_text(text)
    cached_path.write_text(normalized, encoding="utf-8")
    return cached_path, normalized


def build_subject_summary(
    course: str,
    sources: list[tuple[Path, str]],
    root: Path,
    adapter: GeminiAdapter,
) -> tuple[Path, str]:
    cache_dir = root / SUMMARY_DIR
    cache_dir.mkdir(exist_ok=True)
    summary_path = cache_dir / f"{hashlib.sha256(course.encode()).hexdigest()[:16]}.md"
    manifest_path = summary_path.with_suffix(".json")
    fingerprints = {
        path.relative_to(root).as_posix(): hashlib.sha256(text.encode()).hexdigest()
        for path, text in sources
    }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("files") == fingerprints:
            return summary_path, summary_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        pass

    sections = []
    for batch in batch_sources(sources, root):
        source_names = ", ".join(path.relative_to(root).as_posix() for path, _ in batch)
        summary = adapter.summarize(course, format_batch(batch, root))
        sections.append(f"## {source_names}\n\n{summary}")
    summary = f"# {course}\n\n" + "\n\n".join(sections)
    summary_path.write_text(summary, encoding="utf-8")
    manifest_path.write_text(json.dumps({"files": fingerprints}, indent=2), encoding="utf-8")
    return summary_path, summary


def _text_chunks(text: str, limit: int = MAX_GEMINI_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for paragraph in re.split(r"\n{2,}", text):
        if len(paragraph) > limit:
            if current:
                chunks.append("\n\n".join(current))
                current, length = [], 0
            chunks.extend(paragraph[index:index + limit] for index in range(0, len(paragraph), limit))
            continue
        if current and length + len(paragraph) + 2 > limit:
            chunks.append("\n\n".join(current))
            current, length = [], 0
        current.append(paragraph)
        length += len(paragraph) + (2 if current else 0)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def format_batch(sources: list[tuple[Path, str]], root: Path) -> str:
    return "\n\n".join(
        f"===== SOURCE: {path.relative_to(root).as_posix()} =====\n{text}"
        for path, text in sources
    )


def batch_sources(
    sources: list[tuple[Path, str]], root: Path, limit: int = MAX_GEMINI_CHARS
) -> list[list[tuple[Path, str]]]:
    batches: list[list[tuple[Path, str]]] = []
    current: list[tuple[Path, str]] = []
    length = 0
    for path, text in sources:
        for index, chunk in enumerate(_text_chunks(text, max(1, limit - 100))):
            name = path.with_name(f"{path.name} (part {index + 1})") if len(_text_chunks(text, max(1, limit - 100))) > 1 else path
            entry = (name, chunk)
            entry_length = len(chunk) + len(path.name) + 32
            if current and length + entry_length > limit:
                batches.append(current)
                current, length = [], 0
            current.append(entry)
            length += entry_length
    if current:
        batches.append(current)
    return batches


def _cards_from_items(
    items: list[dict[str, str]],
    source: str,
    root: Path,
    seen: set[tuple[str, str]],
    question_counts: dict[str, int],
    cards: list[Card],
    limit: int,
) -> None:
    for item in items:
        key = (item["question"].casefold(), item["answer"].casefold())
        if key in seen:
            continue
        seen.add(key)
        question_key = item["question"].casefold()
        occurrence = question_counts[question_key]
        question_counts[question_key] += 1
        identifier = hashlib.sha256(f"{source}\0{item['question']}\0{occurrence}".encode()).hexdigest()
        cards.append(Card(item["question"], f"{item['answer']}\n\n{_source_footer(source)}", source, identifier))
        if len(cards) >= limit:
            return


def generate_course_cards(
    course: str,
    sources: list[tuple[Path, str]],
    root: Path,
    adapter: GeminiAdapter,
    mode: str = "efficient",
) -> list[Card]:
    if mode not in {"efficient", "deep"}:
        raise ValueError(f"unknown generation mode: {mode}")
    if mode == "deep":
        summary_path, summary = build_subject_summary(course, sources, root, adapter)
        sources = [(root / course / "_ilias-anktor-summary.md", summary)]
    cards: list[Card] = []
    seen: set[tuple[str, str]] = set()
    question_counts: dict[str, int] = defaultdict(int)
    for batch in batch_sources(sources, root):
        items = adapter.generate(course, format_batch(batch, root))
        _cards_from_items(items, course, root, seen, question_counts, cards, MAX_CARDS_PER_COURSE)
        if len(cards) >= MAX_CARDS_PER_COURSE:
            break
    return cards


def generate_cards(
    sources: list[tuple[Path, str]],
    root: Path,
    *,
    api_key: str | None = None,
    adapter: GeminiAdapter | None = None,
    prompt_api_key: Callable[[str], str] = getpass,
) -> list[Card]:
    """Generate validated, deduplicated cards for the selected source files."""
    if adapter is None:
        api_key = api_key or os.environ.get("GEMINI_API_KEY") or prompt_api_key(
            "Gemini API key (input hidden): "
        )
        adapter = GeminiAdapter(api_key)

    cards: list[Card] = []
    for path, text in sources:
        relative = path.relative_to(root).as_posix()
        seen: set[tuple[str, str]] = set()
        question_counts: dict[str, int] = defaultdict(int)
        for chunk in _text_chunks(text):
            for item in adapter.generate(relative, chunk):
                key = (item["question"].casefold(), item["answer"].casefold())
                if key in seen:
                    continue
                seen.add(key)
                question_key = item["question"].casefold()
                occurrence = question_counts[question_key]
                question_counts[question_key] += 1
                identifier = hashlib.sha256(
                    f"{relative}\0{item['question']}\0{occurrence}".encode()
                ).hexdigest()
                cards.append(
                    Card(
                        item["question"],
                        f"{item['answer']}\n\n{_source_footer(relative)}",
                        relative,
                        identifier,
                    )
                )
                if len(seen) >= MAX_CARDS_PER_SOURCE:
                    break
            if len(seen) >= MAX_CARDS_PER_SOURCE:
                break

    return cards
