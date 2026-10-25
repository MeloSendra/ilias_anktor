from __future__ import annotations

import hashlib
import json
from pathlib import Path


STATE_NAME = ".ilias-anktor-state.json"


def changed_files(root: Path, paths: list[Path]) -> int:
    state_path = root / STATE_NAME
    if not state_path.exists():
        previous: dict[str, str] = {}
    else:
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read scan state: {state_path}") from exc
        if not isinstance(previous, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in previous.items()):
            raise ValueError(f"invalid scan state: {state_path}")

    current = {}
    changed = 0
    for path in paths:
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        current[relative] = digest
        if previous.get(relative) != digest:
            changed += 1

    state_path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return changed
