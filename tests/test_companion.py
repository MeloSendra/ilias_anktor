import sqlite3
import subprocess
import sys
import zipfile
import re
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from anki_companion.apkg import write_apkg
from anki_companion.core import Card, GeminiAdapter, build_subject_summary, cache_text_source, generate_cards, generate_course_cards, group_sources_by_course, normalize_extracted_text, read_sources
from anki_companion.state import changed_files


class FakeAdapter:
    def generate(self, source, text):
        lines = text.splitlines()
        headings = [(i, re.sub(r"^#+\s*", "", line).strip()) for i, line in enumerate(lines)
                    if re.match(r"^#{1,6}\s+\S", line)]
        cards = []
        for index, (line, heading) in enumerate(headings):
            end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
            body = "\n".join(re.sub(r"^\s*[-*]\s+", "", value) for value in lines[line + 1:end]).strip()
            if body:
                cards.append({"question": heading, "answer": body})
        return cards


class CompanionTests(unittest.TestCase):
    def cards(self, sources, root):
        return generate_cards(sources, root, adapter=FakeAdapter())

    def test_extracts_html_headings_and_lists(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lesson.html").write_text(
                "<html><body><h1>Topic</h1><p>Definition.</p><h2>Steps</h2><ul><li>One</li><li>Two</li></ul></body></html>",
                encoding="utf-8",
            )
            cards = self.cards(read_sources(root), root)
            self.assertEqual([card.front for card in cards], ["Topic", "Steps"])
            self.assertEqual(cards[1].back, "One\nTwo\n\nSource: lesson.html")

    def test_tracks_new_and_changed_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            source.write_text("# Topic\nDefinition.", encoding="utf-8")
            self.assertEqual(changed_files(root, [source]), 1)
            self.assertEqual(changed_files(root, [source]), 0)
            source.write_text("# Topic\nUpdated.", encoding="utf-8")
            self.assertEqual(changed_files(root, [source]), 1)

    def test_extracts_text_pdf_pages(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            writer = PdfWriter()
            page = writer.add_blank_page(width=612, height=792)
            font = writer._add_object(DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }))
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
            })
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 18 Tf 72 720 Td (PDF definition) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
            with (root / "lesson.pdf").open("wb") as output:
                writer.write(output)
            cards = self.cards(read_sources(root), root)
            self.assertEqual(cards[0].front, "Page 1")
            self.assertIn("PDF definition", cards[0].back)

    def test_discovers_supported_files_and_normalizes_text(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "nested" / "lesson.md").write_bytes(b"\xef\xbb\xbf# Topic\r\nDefinition.")
            (root / "ignored.docx").write_text("not supported", encoding="utf-8")
            cards = self.cards(read_sources(root), root)
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].front, "Topic")
            self.assertEqual(cards[0].back, "Definition.\n\nSource: nested › lesson.md")

    def test_generates_nested_sections_and_skips_empty_sections(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            source.write_text("# Empty\n## Detail\n- One\n- Two\n### Nested\nExplanation.", encoding="utf-8")
            cards = self.cards(read_sources(root), root)
            self.assertEqual([card.front for card in cards], ["Detail", "Nested"])
            self.assertEqual(cards[0].back, "One\nTwo\n\nSource: lesson.md")

    def test_identity_survives_body_edits(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            source.write_text("# Topic\nFirst version.", encoding="utf-8")
            first = self.cards(read_sources(root), root)[0]
            source.write_text("# Topic\nUpdated version.", encoding="utf-8")
            second = self.cards(read_sources(root), root)[0]
            self.assertEqual(first.identifier, second.identifier)
            self.assertNotEqual(first.back, second.back)

    def test_gemini_cards_are_deduplicated_capped_and_sourced(self) -> None:
        class Response:
            def generate(self, source, text):
                return [{"question": "Same", "answer": "Answer"}] * 2 + [
                    {"question": f"Q{i}", "answer": "A"} for i in range(200)
                ]

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            source.write_text("content", encoding="utf-8")
            cards = generate_cards([(source, "content")], root, adapter=Response())
            self.assertEqual(len(cards), 150)
            self.assertEqual(cards[0].back, "Answer\n\nSource: lesson.md")
            self.assertEqual(len({card.identifier for card in cards}), 150)

    def test_gemini_response_requires_exact_card_json(self) -> None:
        class Response:
            def generate(self, source, text):
                raise ValueError("Gemini returned invalid card JSON")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            source.write_text("content", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid card JSON"):
                generate_cards([(source, "content")], root, adapter=Response())

    def test_prompts_for_key_only_when_environment_is_empty(self) -> None:
        class Response:
            def generate(self, source, text):
                return [{"question": "Q", "answer": "A"}]

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            source.write_text("content", encoding="utf-8")
            cards = generate_cards(
                [(source, "content")], root, adapter=Response(),
                prompt_api_key=lambda _: self.fail("key prompt should not be used"),
            )
            self.assertEqual(cards[0].source, "lesson.md")

    def test_groups_sources_by_top_level_course_folder(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "Course A" / "lesson.md"
            second = root / "Course A" / "notes.txt"
            third = root / "Course B" / "lesson.md"
            for path in (first, second, third):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("content", encoding="utf-8")
            grouped = group_sources_by_course(read_sources(root), root)
            self.assertEqual(list(grouped), ["Course A", "Course B"])
            self.assertEqual([path.name for path, _ in grouped["Course A"]], ["lesson.md", "notes.txt"])

    def test_gemini_default_model(self) -> None:
        self.assertEqual(GeminiAdapter("key").model, "gemini-3.8-flash")

    def test_course_generation_uses_one_shared_batch_in_efficient_mode(self) -> None:
        class Adapter:
            def __init__(self):
                self.calls = []

            def generate(self, source, text):
                self.calls.append((source, text))
                return [{"question": "Q", "answer": "A"}]

        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "Course A" / "one.txt"
            second = root / "Course A" / "two.txt"
            first.parent.mkdir(parents=True)
            first.write_text("one", encoding="utf-8")
            second.write_text("two", encoding="utf-8")
            adapter = Adapter()
            cards = generate_course_cards("Course A", [(first, "one"), (second, "two")], root, adapter)
            self.assertEqual(len(adapter.calls), 1)
            self.assertIn("SOURCE: Course A/one.txt", adapter.calls[0][1])
            self.assertEqual(len(cards), 1)

    def test_deep_mode_summarizes_compact_batches_and_caches(self) -> None:
        class Adapter:
            def __init__(self):
                self.summaries = 0
                self.generations = 0

            def summarize(self, source, text):
                self.summaries += 1
                return "compact summary"

            def generate(self, source, text):
                self.generations += 1
                return [{"question": "Q", "answer": "A"}]

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Course A" / "one.txt"
            source.parent.mkdir(parents=True)
            source.write_text("one", encoding="utf-8")
            adapter = Adapter()
            generate_course_cards("Course A", [(source, "one")], root, adapter, "deep")
            generate_course_cards("Course A", [(source, "one")], root, adapter, "deep")
            self.assertEqual(adapter.summaries, 1)
            self.assertEqual(adapter.generations, 2)

    def test_subject_summary_is_cached_by_source_content(self) -> None:
        class SummaryAdapter:
            def __init__(self):
                self.calls = 0

            def summarize(self, source, text):
                self.calls += 1
                return f"Summary of {source}: {text}"

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Course A" / "lesson.md"
            source.parent.mkdir()
            source.write_text("content", encoding="utf-8")
            adapter = SummaryAdapter()
            first_path, first = build_subject_summary("Course A", [(source, "content")], root, adapter)
            second_path, second = build_subject_summary("Course A", [(source, "content")], root, adapter)
            self.assertEqual(first_path, second_path)
            self.assertEqual(first, second)
            self.assertEqual(adapter.calls, 1)

    def test_extracted_text_is_normalized_and_cached(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Course A" / "lesson.pdf"
            source.parent.mkdir()
            cached, text = cache_text_source(
                source, "Header\nHeader\n\n\n# Topic\r\n\r\nDefinition\n", root
            )
            self.assertEqual(text, "Header\n\n# Topic\n\nDefinition")
            self.assertEqual(cached, root / ".ilias-anktor-text" / "Course A" / "lesson.txt")
            self.assertEqual(cached.read_text(encoding="utf-8"), text)

    def test_cached_text_and_summaries_are_not_scanned(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            source.write_text("# Topic\nDefinition", encoding="utf-8")
            (root / ".ilias-anktor-text").mkdir()
            (root / ".ilias-anktor-text" / "old.txt").write_text("old", encoding="utf-8")
            self.assertEqual([path for path, _ in read_sources(root)], [source])

    def test_large_sources_are_chunked_before_gemini(self) -> None:
        class Response:
            def __init__(self):
                self.lengths = []

            def generate(self, source, text):
                self.lengths.append(len(text))
                return [{"question": f"Q{len(self.lengths)}", "answer": "A"}]

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            response = Response()
            cards = generate_cards([(source, "paragraph\n\n" * 40_000)], root, adapter=response)
            self.assertGreater(len(response.lengths), 1)
            self.assertLessEqual(max(response.lengths), 12_000)
            self.assertEqual(len(cards), len(response.lengths))

    def test_gemini_http_errors_include_status_and_message(self) -> None:
        original = __import__("urllib.request", fromlist=["urlopen"]).urlopen
        try:
            import urllib.request
            urllib.request.urlopen = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                urllib.error.HTTPError(
                    "https://example.invalid", 400, "Bad Request",
                    {}, __import__("io").BytesIO(b'{"error":{"message":"bad API key"}}'),
                )
            )
            with self.assertRaisesRegex(ValueError, "HTTP 400\\): bad API key"):
                GeminiAdapter("key").generate("lesson.md", "content")
        finally:
            urllib.request.urlopen = original

    def test_gemini_timeout_has_actionable_message(self) -> None:
        import urllib.request
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                TimeoutError()
            )
            with self.assertRaisesRegex(ValueError, "timed out after 180 seconds"):
                GeminiAdapter("key").generate("lesson.md", "content")
        finally:
            urllib.request.urlopen = original

    def test_gemini_retries_temporary_overload(self) -> None:
        import io
        import urllib.request
        original = urllib.request.urlopen
        calls = 0

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"candidates":[{"content":{"parts":[{"text":"[{\\\"question\\\":\\\"Q\\\",\\\"answer\\\":\\\"A\\\"}]" } ]}}]}'

        def request(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise urllib.error.HTTPError(
                    "https://example.invalid", 503, "Busy", {}, io.BytesIO(b'{"error":{"message":"busy"}}')
                )
            return Response()

        try:
            urllib.request.urlopen = request
            self.assertEqual(GeminiAdapter("key").generate("lesson.md", "content"), [
                {"question": "Q", "answer": "A"}
            ])
            self.assertEqual(calls, 3)
        finally:
            urllib.request.urlopen = original

    def test_card_ids_differ_when_same_question_has_different_answers(self) -> None:
        class Response:
            def generate(self, source, text):
                return [
                    {"question": "Same question", "answer": "First answer"},
                    {"question": "Same question", "answer": "Second answer"},
                ]

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            source.write_text("content", encoding="utf-8")
            cards = generate_cards([(source, "content")], root, adapter=Response())
            self.assertEqual(len({card.identifier for card in cards}), 2)

    def test_rejects_missing_and_invalid_input(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "does not exist"):
                read_sources(root / "missing")
            (root / "broken.md").write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "not valid UTF-8"):
                read_sources(root)

    def test_rejects_empty_export(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "no cards"):
                write_apkg([], Path(directory) / "cards.apkg", "Test")

    def test_writes_readable_apkg(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            source.write_text("# Topic\nDefinition.", encoding="utf-8")
            output = root / "cards.apkg"
            write_apkg(self.cards(read_sources(root), root), output, "Test")
            with zipfile.ZipFile(output) as package:
                self.assertEqual(package.namelist(), ["collection.anki2", "media"])
                database = root / "collection.anki2"
                database.write_bytes(package.read("collection.anki2"))
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
            deck = connection.execute("SELECT decks FROM col").fetchone()[0]
            self.assertIn('"Test"', deck)
            connection.close()
            self.assertFalse(output.with_suffix(".anki2").exists())

    def test_writes_multiple_course_decks(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "courses.apkg"
            cards = {
                "Course A": [Card("Question", "Answer", "a.pdf", "a" * 64)],
                "Course B": [Card("Question 2", "Answer 2", "b.pdf", "b" * 64)],
            }
            write_apkg(cards, output)
            with zipfile.ZipFile(output) as package:
                database = Path(directory) / "collection.anki2"
                database.write_bytes(package.read("collection.anki2"))
            connection = sqlite3.connect(database)
            decks = connection.execute("SELECT decks FROM col").fetchone()[0]
            self.assertIn("Course A", decks)
            self.assertIn("Course B", decks)
            connection.close()

    def test_cli_reports_invalid_input(self) -> None:
        with TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", "anki_companion", str(Path(directory) / "missing"), "-o", str(Path(directory) / "cards.apkg")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("input directory does not exist", result.stderr)


if __name__ == "__main__":
    unittest.main()
