from __future__ import annotations

import argparse
from pathlib import Path

from .apkg import write_apkg
from .core import generate_cards, read_sources


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a deterministic Anki package from text and Markdown.")
    parser.add_argument("input", type=Path, help="course folder containing .txt, .md, or .markdown files")
    parser.add_argument("-o", "--output", type=Path, required=True, help="output .apkg path")
    parser.add_argument("--deck", default="Ilias Companion", help="Anki deck name")
    args = parser.parse_args()

    try:
        sources = read_sources(args.input)
        cards = generate_cards(sources, args.input)
        write_apkg(cards, args.output, args.deck)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"wrote {len(cards)} cards to {args.output}")
