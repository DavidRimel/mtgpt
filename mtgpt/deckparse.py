"""Parse decklist text into a ParsedDeck.

Handles the shapes Moxfield's plain-text export actually emits:

    1 Sol Ring
    2x Brainstorm
    1 Arcane Signet (ELD) 331
    1 Sol Ring (LTR) 264 *F*
    1 Cultivate (M21) 177 #Ramp
    1 Atraxa, Praetors' Voice (C16) 28 *CMDR*

Decorations are removed in passes, right to left, so the card name is
whatever survives. This matters because names may themselves contain
parentheses and slashes.
"""

from __future__ import annotations

import re

from .errors import DeckStructureError
from .models import DeckEntry, ParsedDeck

#: Sections whose contents are not part of the 100-card deck.
EXCLUDED_SECTIONS = frozenset({"sideboard", "maybeboard", "considering", "tokens"})

#: Section header lines, e.g. "Commander", "Deck:", "Sideboard".
_SECTION_RE = re.compile(
    r"^(commander|commanders|deck|mainboard|sideboard|maybeboard|considering|tokens|companion)\s*:?\s*$",
    re.IGNORECASE,
)

#: Trailing "#Category" tags. Moxfield emits at most one, but tolerate several.
_TAG_RE = re.compile(r"\s+#(?P<tag>\S+)")

#: Trailing "*F*" / "*CMDR*" / "*E*" style flags.
_FLAG_RE = re.compile(r"\s+\*(?P<flag>[A-Za-z0-9]+)\*")

#: Trailing "(SET) 123". Set codes are 2-6 alphanumerics, which is what keeps a
#: parenthetical that belongs to the card name from being eaten.
_SETCN_RE = re.compile(
    r"\s+\((?P<set>[A-Za-z0-9]{2,6})\)(?:\s+(?P<cn>[A-Za-z0-9★\-]+))?\s*$"
)

#: Leading quantity, with optional "x" suffix.
_QTY_RE = re.compile(r"^(?P<qty>\d+)\s*[xX]?\s+(?P<rest>.+)$")


def _strip_comment(line: str) -> str:
    return line.split("//", 1)[0] if line.lstrip().startswith("//") else line


def parse(text: str) -> ParsedDeck:
    """Parse decklist text. Raises DeckStructureError when nothing parses."""
    entries: list[DeckEntry] = []
    commanders: list[DeckEntry] = []
    section = "deck"

    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue

        header = _SECTION_RE.match(line)
        if header:
            section = header.group(1).lower()
            continue

        if section in EXCLUDED_SECTIONS:
            continue

        entry = _parse_entry(line, section=section)
        if entry is None:
            continue
        (commanders if entry.is_commander else entries).append(entry)

    if not entries and not commanders:
        raise DeckStructureError(
            "No decklist entries found. Expected lines like '1 Sol Ring' or "
            "'1 Arcane Signet (ELD) 331'."
        )

    return ParsedDeck(
        entries=_merge(entries),
        commanders=_merge(commanders),
    )


def _parse_entry(line: str, *, section: str) -> DeckEntry | None:
    """Parse one non-empty, non-header line. Returns None if it is not an entry."""
    category: str | None = None
    flags: list[str] = []

    def take_tag(match: re.Match[str]) -> str:
        nonlocal category
        if category is None:
            category = match.group("tag")
        return ""

    def take_flag(match: re.Match[str]) -> str:
        flags.append(match.group("flag").upper())
        return ""

    line = _TAG_RE.sub(take_tag, line)
    line = _FLAG_RE.sub(take_flag, line).strip()

    set_code = collector_number = None
    setcn = _SETCN_RE.search(line)
    if setcn:
        set_code = setcn.group("set").upper()
        collector_number = setcn.group("cn")
        line = line[: setcn.start()].strip()

    qty_match = _QTY_RE.match(line)
    if not qty_match:
        return None

    name = qty_match.group("rest").strip()
    if not name:
        return None

    is_commander = section.startswith("commander") or "CMDR" in flags

    return DeckEntry(
        qty=int(qty_match.group("qty")),
        name=name,
        set_code=set_code,
        collector_number=collector_number,
        category=category,
        is_commander=is_commander,
    )


def _merge(entries: list[DeckEntry]) -> tuple[DeckEntry, ...]:
    """Collapse repeated lines for the same card, summing quantities.

    Order of first appearance is preserved so a diff of the rendered list stays
    stable across runs.
    """
    merged: dict[str, DeckEntry] = {}
    for entry in entries:
        key = entry.name.casefold()
        existing = merged.get(key)
        if existing is None:
            merged[key] = entry
        else:
            merged[key] = DeckEntry(
                qty=existing.qty + entry.qty,
                name=existing.name,
                set_code=existing.set_code or entry.set_code,
                collector_number=existing.collector_number or entry.collector_number,
                category=existing.category or entry.category,
                is_commander=existing.is_commander or entry.is_commander,
            )
    return tuple(merged.values())
