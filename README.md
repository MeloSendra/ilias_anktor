# ilias_anktor

Small deterministic companion app for turning downloaded text material into an
Anki package. It does not modify `ilias-sync` or Anki's database.

## Usage

```powershell
python -m unittest discover -s tests -p "test_*.py"
python -m anki_companion .\course -o .\course.apkg --deck "My Course"
```

The input folder is scanned recursively for UTF-8 `.txt`, `.md`, and
`.markdown` files, plus text-based `.pdf` files. Each Markdown heading with
following text becomes one card; PDF pages become `Page N` cards:
the heading is the front and the section text is the back. Cards include their
relative source path and stable source-plus-heading identities, so editing a
section updates its identity instead of creating a duplicate.

HTML, Office documents, OCR, AI generation, incremental sync state, AnkiConnect,
and direct Anki database writes are intentionally outside this MVP. PDF
extraction requires the dependency listed in `requirements.txt`.
