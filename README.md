# ilias_anktor

**Anktor** stands for **Anki Generator**.

Small companion app for turning downloaded text material into an Anki package
with Gemini-generated Q/A cards. It does not modify `ilias-sync` or Anki's
database.

## Usage

```powershell
python -m unittest discover -s tests -p "test_*.py"
python -m anki_companion .\course -o .\course.apkg --deck "My Course"
python -m anki_companion --root C:\Users\tinse\ILIAS -o .\course.apkg
# Optionally select files (repeat --file):
python -m anki_companion --root C:\Users\tinse\ILIAS --file lesson.md -o .\course.apkg
```

The desktop UI lives in `desktop/` and mirrors the Electron packaging style of
`ilias_windows`:

```powershell
cd desktop
npm install
npm start
```

It lets you select top-level ILIAS courses and launches the tested generation
backend without requiring the user to operate the CLI. The app first uses
`GEMINI_API_KEY` when it is set. Otherwise, save a key in Settings; it is stored
in Windows Credential Manager under `ILIAS Anktor` and used locally by the app.
The generator uses `gemini-3.8-flash`, sends bounded 12,000-character batches
to keep requests responsive, allows up to three minutes per request, and
limits each course to 300 cards while letting Gemini choose a smaller useful
set. Efficient mode is the default; Deep mode is available when richer
summaries are worth the extra requests.

For a standalone Windows build, install Python and PyInstaller once, then run:

```powershell
cd desktop
.\build-backend.ps1
npm install
.\package.ps1
```

`package.ps1` also works when the repository is under a WSL-backed UNC path.
The desktop app provides Browse buttons for the ILIAS folder and output file.

The resulting portable executable can be double-clicked directly; it does not
need a scheduled task or an internet connection except when calling Gemini.
It does not synchronize files or update itself.

The input folder is scanned recursively for UTF-8 `.txt`, `.md`, `.markdown`,
`.html`, and `.htm` files, plus text-based `.pdf` files. The input can be any local
folder, including an `ILIAS` export; it does not need to be produced by
`ilias-sync`. Efficient mode combines normalized files into bounded course
batches and generates cards directly. Deep mode summarizes those same compact
batches first and caches the subject document under
`.ilias-anktor-summaries`, then generates cards from it. The
`.ilias-anktor-text` cache contains one normalized UTF-8 file per source; both
caches are ignored by Git. Duplicate cards are removed and each course is
limited to 300 cards. Every answer includes a readable relative source footer.
Set `GEMINI_API_KEY` or save the key in the desktop Settings page; the key is
never printed.

With no input folder, the default source is `%USERPROFILE%\ILIAS`. Use `--root`
to select another source. Each successful run exports all current cards and
records file hashes in `.ilias-anktor-state.json`, reporting how many files are
new or changed since the previous successful scan.

Office documents, OCR, AnkiConnect, and direct Anki database writes are
intentionally outside this MVP. PDF
extraction requires the dependency listed in `requirements.txt`. Generated
packages and local course material are excluded by [.gitignore](.gitignore).
