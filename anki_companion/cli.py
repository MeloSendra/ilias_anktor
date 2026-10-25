from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from getpass import getpass
from pathlib import Path

from .apkg import write_apkg
from .core import GeminiAdapter, cache_text_source, generate_course_cards, iter_sources
from .state import changed_files


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an Anki package from selected course files with Gemini.")
    parser.add_argument("input", nargs="?", type=Path, help="course folder; defaults to %%USERPROFILE%%\\ILIAS")
    parser.add_argument("--root", type=Path, dest="input_root", help="override the default ILIAS course folder")
    parser.add_argument("-o", "--output", type=Path, required=True, help="output .apkg path")
    parser.add_argument("--deck", default="Ilias Companion", help="Anki deck name")
    parser.add_argument("--mode", choices=("efficient", "deep"), default="efficient")
    parser.add_argument(
        "--file", action="append", dest="selected_files",
        help="relative course file to include (repeatable; defaults to all supported files)",
    )
    args = parser.parse_args()

    try:
        if args.input is not None and args.input_root is not None:
            parser.error("provide either the input folder or --root, not both")
        input_root = args.input or args.input_root or (Path.home() / "ILIAS")
        sources = iter_sources(input_root)
        selected = {Path(value).as_posix() for value in args.selected_files or []}
        sources_by_course: dict[str, list[tuple[Path, str]]] = defaultdict(list)
        source_paths = []
        found = set()
        for path, text in sources:
            relative = path.relative_to(input_root).as_posix()
            if selected and relative not in selected:
                continue
            found.add(relative)
            source_paths.append(path)
            course = path.relative_to(input_root).parts[0]
            _, normalized = cache_text_source(path, text, input_root)
            sources_by_course[course].append((path, normalized))
        if selected:
            missing = selected - found
            if missing:
                raise ValueError(f"selected file not found or unsupported: {', '.join(sorted(missing))}")
        api_key = os.environ.get("GEMINI_API_KEY") or getpass("Gemini API key (input hidden): ")
        adapter = GeminiAdapter(api_key)
        cards_by_course = {}
        for course, course_sources in sources_by_course.items():
            cards_by_course[course] = generate_course_cards(
                course, course_sources, input_root, adapter, args.mode
            )
        write_apkg(cards_by_course, args.output, args.deck)
        changed = changed_files(input_root, source_paths)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    total = sum(len(cards) for cards in cards_by_course.values())
    print(f"wrote {total} cards to {args.output} ({changed} new or changed files)")
