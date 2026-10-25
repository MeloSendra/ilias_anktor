from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping, Sequence
from tempfile import NamedTemporaryFile

import genanki

from .core import Card


def write_apkg(
    cards: Sequence[Card] | Mapping[str, Sequence[Card]],
    output: Path,
    deck_name: str = "Ilias Companion",
) -> None:
    decks = (
        {deck_name: cards}
        if not isinstance(cards, Mapping)
        else cards
    )
    if not any(decks.values()):
        raise ValueError("no cards could be generated from the input")

    model = genanki.Model(
        1_700_000_001_000,
        "Ilias Companion Basic",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[{
            "name": "Card 1",
            "qfmt": "{{Front}}",
            "afmt": "{{FrontSide}}<hr id=answer>{{Back}}",
        }],
    )
    anki_decks = []
    for index, (name, deck_cards) in enumerate(sorted(decks.items())):
        deck = genanki.Deck(1_700_000_000_000 + index, name)
        for card in deck_cards:
            note = genanki.Note(model=model, fields=[card.front, card.back])
            note.guid = card.identifier[:10]
            deck.add_note(note)
        if deck.notes:
            anki_decks.append(deck)
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        genanki.Package(anki_decks).write_to_file(str(temporary_path))
        temporary_path.replace(output)
    finally:
        temporary_path.unlink(missing_ok=True)
