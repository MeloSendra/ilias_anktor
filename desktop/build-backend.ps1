$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m pip install -r ..\requirements.txt pyinstaller
New-Item -ItemType Directory -Force backend | Out-Null
python -m PyInstaller --noconfirm --clean --onefile --name anki_companion --distpath backend --paths .. `
  --exclude-module numpy --exclude-module pandas --exclude-module scipy `
  --exclude-module matplotlib --exclude-module IPython backend_entry.py
