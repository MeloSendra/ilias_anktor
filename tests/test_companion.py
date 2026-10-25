import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from anki_companion.apkg import write_apkg
from anki_companion.core import generate_cards, read_sources


class CompanionTests(unittest.TestCase):
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
            cards = generate_cards(read_sources(root), root)
            self.assertEqual(cards[0].front, "Page 1")
            self.assertIn("PDF definition", cards[0].back)

    def test_discovers_supported_files_and_normalizes_text(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "nested" / "lesson.md").write_bytes(b"\xef\xbb\xbf# Topic\r\nDefinition.")
            (root / "ignored.docx").write_text("not supported", encoding="utf-8")
            cards = generate_cards(read_sources(root), root)
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].front, "Topic")
            self.assertEqual(cards[0].back, "Definition.\n\nSource: nested/lesson.md")

    def test_generates_nested_sections_and_skips_empty_sections(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            source.write_text("# Empty\n## Detail\n- One\n- Two\n### Nested\nExplanation.", encoding="utf-8")
            cards = generate_cards(read_sources(root), root)
            self.assertEqual([card.front for card in cards], ["Detail", "Nested"])
            self.assertEqual(cards[0].back, "One\nTwo\n\nSource: lesson.md")

    def test_identity_survives_body_edits(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lesson.md"
            source.write_text("# Topic\nFirst version.", encoding="utf-8")
            first = generate_cards(read_sources(root), root)[0]
            source.write_text("# Topic\nUpdated version.", encoding="utf-8")
            second = generate_cards(read_sources(root), root)[0]
            self.assertEqual(first.identifier, second.identifier)
            self.assertNotEqual(first.back, second.back)

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
            write_apkg(generate_cards(read_sources(root), root), output, "Test")
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
