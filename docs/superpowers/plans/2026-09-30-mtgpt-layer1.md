# mtgpt Layer 1 (Deterministic Spine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable Commander deck auditor that ingests a decklist, resolves every card against Scryfall, validates legality, tags each card by function, and reports ratios/curve/pips plus a bracket verdict.

**Architecture:** A pure-Python package at `mtgpt/` with one module per pipeline stage and no cross-stage coupling: each stage takes a typed value and returns a typed value. Only `scryfall.py` touches the network, so every other module is tested offline against recorded fixtures. A thin `cli.py` composes the stages and renders the report that `SKILL.md` tells Claude to run.

**Tech Stack:** Python 3.12, stdlib only for runtime (`urllib.request`, `dataclasses`, `re`, `json`), `pytest` for tests. No third-party runtime dependencies in Layer 1 — Playwright arrives in Layer 2 with the Moxfield fetcher.

**Spec:** `docs/superpowers/specs/2026-09-30-mtgpt-design.md`

## Execution order

Tasks 1-8 then 11, 12, 13, then 10, and **Task 9 LAST**. Task 9 writes `SKILL.md`, which is the
agent's playbook over the whole toolkit — it cannot be written before the operations it
documents exist. Task 10 (Moxfield browser fetch) is verify-first and may end in a documented
deferral, so it is placed after the operations that do not depend on it.

## Global Constraints

- Python 3.12+ (`requires-python = ">=3.12"`).
- Zero third-party runtime dependencies. `pytest` is a dev dependency only.
- Package lives at `mtgpt/` in the repo root; invoked as `python3 -m mtgpt.cli` from the repo root. Never require `PYTHONPATH` to be set by hand.
- Every Scryfall request sends `User-Agent: mtgpt/0.1` and `Accept: application/json`, and sleeps 100ms between requests (Scryfall asks for 50-100ms; we use the polite end).
- All dataclasses are `frozen=True`. Collections on frozen dataclasses use `tuple` or `frozenset`, never `list`/`set`/`dict`, so instances stay hashable.
- A card name that Scryfall cannot resolve is a **hard stop**, never a guess or a silent drop.
- No network access in any test. Tests read fixtures from `tests/fixtures/`.
- Format is Commander only. Legality is always checked against `legalities.commander`.
- Basic lands are the only cards exempt from the singleton rule. Detection: `type_line` starts with `"Basic Land"`.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Packaging, `requires-python`, pytest config (`pythonpath = ["."]`) |
| `mtgpt/__init__.py` | Package marker, `__version__` |
| `mtgpt/models.py` | All frozen dataclasses + enums shared across stages. No logic. |
| `mtgpt/errors.py` | Exception types that signal hard stops |
| `mtgpt/deckparse.py` | Decklist text → `ParsedDeck`. No network. |
| `mtgpt/scryfall.py` | `ParsedDeck` → `ResolvedDeck`. The only networked module. |
| `mtgpt/validate.py` | `ResolvedDeck` → `tuple[Violation, ...]` |
| `mtgpt/classify.py` | `Card` → `frozenset[Function]` |
| `mtgpt/targets.py` | Ratio targets, curve bands, pip thresholds, bracket rules. Data only. |
| `mtgpt/audit.py` | `ResolvedDeck` + tags → `AuditReport` |
| `mtgpt/brackets.py` | `ResolvedDeck` + tags + target bracket → `BracketReport` |
| `mtgpt/cli.py` | Argument parsing, stage composition, text rendering |
| `skills/mtgpt/SKILL.md` | The skill Claude loads: routing and workflow |
| `skills/mtgpt/references/deckbuilding-hygiene.md` | Prose rationale for the numbers in `targets.py` |
| `skills/mtgpt/references/brackets.md` | Bracket 1-5 rules and enforcement checklist |
| `.claude-plugin/plugin.json` | Plugin manifest |
| `.claude-plugin/marketplace.json` | Marketplace manifest for `/plugin marketplace add` |

Why `targets.py` holds the numbers and `references/` holds the prose: the audit must compute from a single source of truth, and duplicating thresholds into markdown guarantees they drift apart. The reference file explains *why* each number is what it is and points at `targets.py` for the value.

---

## Task 1: Scaffold and shared models

**Files:**
- Create: `pyproject.toml`
- Create: `mtgpt/__init__.py`
- Create: `mtgpt/errors.py`
- Create: `mtgpt/models.py`
- Create: `.claude-plugin/plugin.json`
- Create: `.claude-plugin/marketplace.json`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Function` (enum), `DeckEntry`, `ParsedDeck`, `Card`, `ResolvedDeck`, `Violation`, `Severity`, and exceptions `MtgptError`, `UnresolvedCards`, `DeckStructureError`, `SourceUnavailable`. Every later task imports from these two modules.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
import pytest

from mtgpt.models import (
    Card,
    DeckEntry,
    Function,
    ParsedDeck,
    ResolvedDeck,
    Severity,
    Violation,
)


def sample_card(name="Sol Ring", **kw):
    defaults = dict(
        name=name,
        mana_value=1.0,
        type_line="Artifact",
        oracle_text="{T}: Add {C}{C}.",
        mana_cost="{1}",
        color_identity=frozenset(),
        colors=frozenset(),
        legal_commander="legal",
        produced_mana=frozenset("C"),
        layout="normal",
        is_game_changer=False,
        usd=1.54,
        keywords=(),
    )
    defaults.update(kw)
    return Card(**defaults)


def test_card_is_frozen_and_hashable():
    card = sample_card()
    assert hash(card) is not None
    with pytest.raises(AttributeError):
        card.name = "Mana Crypt"


def test_card_is_basic_land_only_for_basics():
    assert sample_card("Forest", type_line="Basic Land — Forest").is_basic_land
    assert not sample_card("Command Tower", type_line="Land").is_basic_land


def test_card_is_land_front_face():
    assert sample_card("Command Tower", type_line="Land").is_land
    assert not sample_card("Cultivate", type_line="Sorcery").is_land


def test_card_mdfc_land_back_is_not_a_land():
    agadeem = sample_card(
        "Agadeem's Awakening // Agadeem, the Undercrypt",
        type_line="Sorcery // Land",
        layout="modal_dfc",
    )
    assert not agadeem.is_land
    assert agadeem.is_mdfc_land


def test_parsed_deck_counts_exclude_commanders():
    entries = (DeckEntry(qty=1, name="Sol Ring"), DeckEntry(qty=30, name="Forest"))
    cmd = (DeckEntry(qty=1, name="Atraxa, Praetors' Voice", is_commander=True),)
    deck = ParsedDeck(entries=entries, commanders=cmd)
    assert deck.total_cards == 31
    assert deck.total_with_commanders == 32


def test_resolved_deck_total_counts_quantities():
    deck = ResolvedDeck(
        commanders=(sample_card("Atraxa, Praetors' Voice"),),
        cards=((1, sample_card()), (30, sample_card("Forest"))),
    )
    assert deck.total_cards == 31
    assert deck.total_with_commanders == 32


def test_violation_orders_by_severity():
    err = Violation(severity=Severity.ERROR, code="deck_size", message="too small")
    warn = Violation(severity=Severity.WARNING, code="curve", message="high")
    assert sorted([warn, err])[0] is err


def test_function_enum_covers_expected_tags():
    expected = {
        "LAND", "RAMP", "DRAW", "SPOT_REMOVAL", "SWEEPER", "TUTOR",
        "COUNTERSPELL", "PROTECTION", "WINCON", "RECURSION",
        "MASS_LAND_DENIAL", "EXTRA_TURNS", "SYNERGY",
    }
    assert {f.name for f in Function} == expected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtgpt'`

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "mtgpt"
version = "0.1.0"
description = "Build and tune Magic: The Gathering Commander decks"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.setuptools]
packages = ["mtgpt"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 4: Write `mtgpt/__init__.py` and `mtgpt/errors.py`**

```python
# mtgpt/__init__.py
"""Build and tune Magic: The Gathering Commander decks."""

__version__ = "0.1.0"
```

```python
# mtgpt/errors.py
"""Exceptions that signal a hard stop.

The pipeline never guesses. When an input cannot be trusted, it raises.
"""


class MtgptError(Exception):
    """Base class for every mtgpt failure."""


class UnresolvedCards(MtgptError):
    """One or more card names could not be resolved against Scryfall.

    This is the guard against hallucinated and misspelled cards. It carries the
    offending names so the caller can echo them back verbatim.
    """

    def __init__(self, names):
        self.names = tuple(names)
        joined = ", ".join(self.names)
        super().__init__(
            f"{len(self.names)} card name(s) could not be found on Scryfall: {joined}. "
            "Fix the spelling or remove the entries; mtgpt will not guess."
        )


class DeckStructureError(MtgptError):
    """The decklist text could not be parsed into a deck at all."""


class SourceUnavailable(MtgptError):
    """An upstream data source failed. Callers may degrade rather than stop."""

    def __init__(self, source, detail):
        self.source = source
        self.detail = detail
        super().__init__(f"{source} unavailable: {detail}")
```

- [ ] **Step 5: Write `mtgpt/models.py`**

```python
# mtgpt/models.py
"""Typed values passed between pipeline stages.

Every dataclass is frozen so a stage cannot mutate its input. Collection
fields use tuple/frozenset to keep instances hashable.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Function(enum.Enum):
    """What role a card plays in the deck.

    A card may carry several functions; Cultivate is both RAMP and TUTOR.
    """

    LAND = "land"
    RAMP = "ramp"
    DRAW = "draw"
    SPOT_REMOVAL = "spot_removal"
    SWEEPER = "sweeper"
    TUTOR = "tutor"
    COUNTERSPELL = "counterspell"
    PROTECTION = "protection"
    WINCON = "wincon"
    RECURSION = "recursion"
    MASS_LAND_DENIAL = "mass_land_denial"
    EXTRA_TURNS = "extra_turns"
    SYNERGY = "synergy"


class Severity(enum.IntEnum):
    """Lower value sorts first, so ERROR leads a sorted report."""

    ERROR = 0
    WARNING = 1
    INFO = 2


@dataclass(frozen=True, order=True)
class Violation:
    """A rule finding. `code` is stable for tests; `message` is for humans."""

    severity: Severity
    code: str
    message: str


@dataclass(frozen=True)
class DeckEntry:
    """One parsed line of a decklist, before any Scryfall lookup."""

    qty: int
    name: str
    set_code: str | None = None
    collector_number: str | None = None
    category: str | None = None
    is_commander: bool = False


@dataclass(frozen=True)
class ParsedDeck:
    """Output of deckparse. Names are unverified strings at this point."""

    entries: tuple[DeckEntry, ...] = ()
    commanders: tuple[DeckEntry, ...] = ()

    @property
    def total_cards(self) -> int:
        return sum(e.qty for e in self.entries)

    @property
    def total_with_commanders(self) -> int:
        return self.total_cards + sum(e.qty for e in self.commanders)


@dataclass(frozen=True)
class Card:
    """A Scryfall-verified card. If you hold one of these, the card is real."""

    name: str
    mana_value: float
    type_line: str
    oracle_text: str
    mana_cost: str
    color_identity: frozenset[str]
    colors: frozenset[str]
    legal_commander: str
    produced_mana: frozenset[str]
    layout: str
    is_game_changer: bool
    usd: float | None
    keywords: tuple[str, ...] = ()

    @property
    def front_type_line(self) -> str:
        """Type line of the front face only."""
        return self.type_line.split("//")[0].strip()

    @property
    def is_land(self) -> bool:
        """True only when the front face is a land.

        An MDFC whose back face is a land is not a land: you cannot play it as
        one on the turn you need the front half.
        """
        return "Land" in self.front_type_line

    @property
    def is_basic_land(self) -> bool:
        return self.front_type_line.startswith("Basic Land")

    @property
    def is_mdfc_land(self) -> bool:
        """A spell on the front, a land on the back. Counts as a flex source."""
        if "//" not in self.type_line:
            return False
        front, _, back = self.type_line.partition("//")
        return "Land" not in front and "Land" in back

    @property
    def is_banned(self) -> bool:
        return self.legal_commander == "banned"

    @property
    def is_legal(self) -> bool:
        return self.legal_commander == "legal"


@dataclass(frozen=True)
class ResolvedDeck:
    """Output of scryfall.resolve. Every name is now a verified Card."""

    commanders: tuple[Card, ...] = ()
    cards: tuple[tuple[int, Card], ...] = ()

    @property
    def total_cards(self) -> int:
        return sum(qty for qty, _ in self.cards)

    @property
    def total_with_commanders(self) -> int:
        return self.total_cards + len(self.commanders)

    @property
    def command_zone_identity(self) -> frozenset[str]:
        """Union of the commanders' color identities."""
        out: set[str] = set()
        for card in self.commanders:
            out |= card.color_identity
        return frozenset(out)

    def iter_cards(self):
        """Yield each card once per copy, so quantities are respected."""
        for qty, card in self.cards:
            for _ in range(qty):
                yield card
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 7: Write the plugin manifests**

```json
// .claude-plugin/plugin.json
{
  "name": "mtgpt",
  "version": "0.1.0",
  "description": "Build and tune Magic: The Gathering Commander decks with Scryfall-verified data",
  "author": "DavidRimel"
}
```

```json
// .claude-plugin/marketplace.json
{
  "name": "mtgpt",
  "owner": {
    "name": "DavidRimel"
  },
  "plugins": [
    {
      "name": "mtgpt",
      "source": "./",
      "description": "Build and tune Magic: The Gathering Commander decks with Scryfall-verified data"
    }
  ]
}
```

Note: JSON does not permit comments. The `//` lines above name the file; do not copy them into the files.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml mtgpt/ tests/test_models.py .claude-plugin/
git commit -m "feat: scaffold mtgpt package with shared models

Frozen dataclasses for every pipeline stage, hard-stop exception types,
and the plugin manifests. Card exposes is_land/is_mdfc_land so a modal
DFC land-back is never miscounted as a land.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Decklist parser

Moxfield's plain-text export is the input format. Real exports carry set codes, collector numbers, foil markers, category tags, and section headers, in varying combinations depending on the user's export settings. The parser strips the optional decorations in passes rather than attempting one monolithic regex, because each pass is independently testable.

**Files:**
- Create: `mtgpt/deckparse.py`
- Test: `tests/test_deckparse.py`

**Interfaces:**
- Consumes: `DeckEntry`, `ParsedDeck`, `DeckStructureError` from Task 1.
- Produces: `parse(text: str) -> ParsedDeck`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_deckparse.py
import pytest

from mtgpt.deckparse import parse
from mtgpt.errors import DeckStructureError


def test_parses_bare_quantity_and_name():
    deck = parse("1 Sol Ring\n30 Forest\n")
    assert [(e.qty, e.name) for e in deck.entries] == [(1, "Sol Ring"), (30, "Forest")]


def test_parses_set_code_and_collector_number():
    deck = parse("1 Arcane Signet (ELD) 331\n")
    entry = deck.entries[0]
    assert entry.name == "Arcane Signet"
    assert entry.set_code == "ELD"
    assert entry.collector_number == "331"


def test_keeps_parenthetical_that_is_part_of_the_name():
    # Set codes are 2-6 alphanumerics; a word in parens is part of the name.
    deck = parse("1 Erase (Not the Urza's Legacy One)\n")
    assert deck.entries[0].name == "Erase (Not the Urza's Legacy One)"
    assert deck.entries[0].set_code is None


def test_parses_x_suffix_quantity():
    deck = parse("2x Brainstorm\n")
    assert (deck.entries[0].qty, deck.entries[0].name) == (2, "Brainstorm")


def test_strips_foil_and_other_star_flags():
    deck = parse("1 Sol Ring (LTR) 264 *F*\n")
    assert deck.entries[0].name == "Sol Ring"
    assert deck.entries[0].collector_number == "264"


def test_captures_hash_category_tag():
    deck = parse("1 Cultivate (M21) 177 #Ramp\n")
    assert deck.entries[0].category == "Ramp"
    assert deck.entries[0].name == "Cultivate"


def test_commander_section_marks_commander():
    text = "Commander\n1 Atraxa, Praetors' Voice (C16) 28\n\nDeck\n1 Sol Ring\n"
    deck = parse(text)
    assert [c.name for c in deck.commanders] == ["Atraxa, Praetors' Voice"]
    assert [e.name for e in deck.entries] == ["Sol Ring"]


def test_cmdr_flag_marks_commander_without_section():
    deck = parse("1 Atraxa, Praetors' Voice (C16) 28 *CMDR*\n1 Sol Ring\n")
    assert [c.name for c in deck.commanders] == ["Atraxa, Praetors' Voice"]
    assert [e.name for e in deck.entries] == ["Sol Ring"]


def test_excludes_sideboard_and_maybeboard():
    text = (
        "Deck\n1 Sol Ring\n\n"
        "Sideboard\n1 Mana Crypt\n\n"
        "Maybeboard\n1 Mana Vault\n\n"
        "Considering\n1 Chrome Mox\n"
    )
    deck = parse(text)
    assert [e.name for e in deck.entries] == ["Sol Ring"]


def test_ignores_blank_lines_and_comments():
    deck = parse("\n// my notes\n1 Sol Ring\n\n")
    assert len(deck.entries) == 1


def test_handles_crlf_and_trailing_whitespace():
    deck = parse("1 Sol Ring   \r\n2x Forest\r\n")
    assert [(e.qty, e.name) for e in deck.entries] == [(1, "Sol Ring"), (2, "Forest")]


def test_double_faced_name_with_slashes_survives():
    deck = parse("1 Agadeem's Awakening // Agadeem, the Undercrypt (ZNR) 90\n")
    assert deck.entries[0].name == "Agadeem's Awakening // Agadeem, the Undercrypt"
    assert deck.entries[0].set_code == "ZNR"


def test_merges_duplicate_lines_of_the_same_card():
    deck = parse("1 Forest\n3 Forest\n")
    assert [(e.qty, e.name) for e in deck.entries] == [(4, "Forest")]


def test_raises_when_no_entries_found():
    with pytest.raises(DeckStructureError):
        parse("this is not a decklist at all\n")


def test_raises_on_empty_input():
    with pytest.raises(DeckStructureError):
        parse("   \n\n")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_deckparse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtgpt.deckparse'`

- [ ] **Step 3: Write `mtgpt/deckparse.py`**

```python
# mtgpt/deckparse.py
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_deckparse.py -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/deckparse.py tests/test_deckparse.py
git commit -m "feat: parse Moxfield-style decklist text

Strips category tags, star flags, and set/collector numbers in passes so
the surviving text is the card name. A 2-6 alphanumeric set-code rule
keeps parentheses that belong to a card name intact.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Scryfall resolution

The only networked module. All I/O goes through an injectable `transport` callable, so every test runs offline against hand-written fixtures that mirror shapes verified against the live API on 2026-09-30.

Three subtleties this task must get right:

1. **`not_found` is the hallucination guard.** Scryfall's `/cards/collection` returns unmatched identifiers in a `not_found` array. That array becoming a raised `UnresolvedCards` is the single most important behavior in Layer 1.
2. **Response order is not request order,** and a modal DFC requested as `Agadeem's Awakening` comes back named `Agadeem's Awakening // Agadeem, the Undercrypt`. Matching therefore indexes returned cards by both full name and front-face name.
3. **MDFC top-level fields are incomplete.** `mana_cost` is empty and `colors` may be absent; both live on `card_faces[0]`. `oracle_text` is absent entirely. We take the front face, because the front face is what you cast — and combining both faces' text would make a land back-face register as ramp.

**Files:**
- Create: `mtgpt/scryfall.py`
- Create: `tests/fixtures/collection_basic.json`
- Create: `tests/fixtures/collection_mdfc.json`
- Create: `tests/fixtures/game_changers.json`
- Test: `tests/test_scryfall.py`

**Interfaces:**
- Consumes: `ParsedDeck`, `Card`, `ResolvedDeck` from Task 1; `UnresolvedCards`, `SourceUnavailable` from Task 1.
- Produces:
  - `COLLECTION_BATCH_SIZE: int` (75)
  - `ScryfallClient(transport=None, sleep=time.sleep)` with methods `collection(names: Sequence[str]) -> tuple[tuple[dict, ...], tuple[str, ...]]` and `game_changers() -> frozenset[str]`
  - `card_from_json(payload: dict, *, game_changers: frozenset[str] = frozenset()) -> Card`
  - `resolve(deck: ParsedDeck, *, client: ScryfallClient | None = None) -> ResolvedDeck`

- [ ] **Step 1: Write the fixtures**

Write `tests/fixtures/collection_basic.json`:

```json
{
  "object": "list",
  "not_found": [],
  "data": [
    {
      "object": "card",
      "name": "Sol Ring",
      "cmc": 1.0,
      "type_line": "Artifact",
      "oracle_text": "{T}: Add {C}{C}.",
      "mana_cost": "{1}",
      "color_identity": [],
      "colors": [],
      "layout": "normal",
      "produced_mana": ["C"],
      "keywords": [],
      "legalities": {"commander": "legal"},
      "prices": {"usd": "1.54"}
    },
    {
      "object": "card",
      "name": "Atraxa, Praetors' Voice",
      "cmc": 4.0,
      "type_line": "Legendary Creature — Phyrexian Angel Horror",
      "oracle_text": "Flying, vigilance, deathtouch, lifelink\nAt the beginning of your end step, proliferate.",
      "mana_cost": "{3}{G}{W}{U}{B}",
      "color_identity": ["B", "G", "U", "W"],
      "colors": ["B", "G", "U", "W"],
      "layout": "normal",
      "keywords": ["Flying", "Vigilance", "Deathtouch", "Lifelink", "Proliferate"],
      "legalities": {"commander": "legal"},
      "prices": {"usd": "3.21"}
    },
    {
      "object": "card",
      "name": "Dockside Extortionist",
      "cmc": 2.0,
      "type_line": "Creature — Goblin Pirate",
      "oracle_text": "When this creature enters, create X Treasure tokens, where X is the number of artifacts and enchantments your opponents control.",
      "mana_cost": "{1}{R}",
      "color_identity": ["R"],
      "colors": ["R"],
      "layout": "normal",
      "produced_mana": ["B", "G", "R", "U", "W"],
      "keywords": [],
      "legalities": {"commander": "banned"},
      "prices": {"usd": null}
    }
  ]
}
```

Write `tests/fixtures/collection_mdfc.json`:

```json
{
  "object": "list",
  "not_found": [],
  "data": [
    {
      "object": "card",
      "name": "Agadeem's Awakening // Agadeem, the Undercrypt",
      "cmc": 6.0,
      "type_line": "Sorcery // Land",
      "mana_cost": "",
      "color_identity": ["B"],
      "layout": "modal_dfc",
      "produced_mana": ["B"],
      "keywords": [],
      "legalities": {"commander": "legal"},
      "prices": {"usd": "12.40"},
      "card_faces": [
        {
          "object": "card_face",
          "name": "Agadeem's Awakening",
          "type_line": "Sorcery",
          "oracle_text": "Return from your graveyard to the battlefield any number of target creature cards that each have a different mana value X or less.",
          "mana_cost": "{X}{B}{B}{B}",
          "colors": ["B"]
        },
        {
          "object": "card_face",
          "name": "Agadeem, the Undercrypt",
          "type_line": "Land",
          "oracle_text": "As this land enters, you may pay 3 life. If you don't, it enters tapped.\n{T}: Add {B}.",
          "mana_cost": "",
          "colors": []
        }
      ]
    }
  ]
}
```

Write `tests/fixtures/game_changers.json`:

```json
{
  "object": "list",
  "has_more": false,
  "total_cards": 3,
  "data": [
    {"object": "card", "name": "Rhystic Study"},
    {"object": "card", "name": "Cyclonic Rift"},
    {"object": "card", "name": "Smothering Tithe"}
  ]
}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_scryfall.py
import json
import pathlib

import pytest

from mtgpt.errors import SourceUnavailable, UnresolvedCards
from mtgpt.models import DeckEntry, ParsedDeck
from mtgpt.scryfall import (
    COLLECTION_BATCH_SIZE,
    ScryfallClient,
    card_from_json,
    resolve,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class FakeTransport:
    """Records calls and returns queued responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload=None):
        self.calls.append((url, payload))
        if not self.responses:
            raise AssertionError(f"unexpected extra request to {url}")
        return self.responses.pop(0)


def test_batch_size_is_scryfall_limit():
    assert COLLECTION_BATCH_SIZE == 75


def test_card_from_json_maps_basic_fields():
    payload = load("collection_basic.json")["data"][0]
    card = card_from_json(payload)
    assert card.name == "Sol Ring"
    assert card.mana_value == 1.0
    assert card.type_line == "Artifact"
    assert card.mana_cost == "{1}"
    assert card.produced_mana == frozenset({"C"})
    assert card.legal_commander == "legal"
    assert card.usd == 1.54
    assert card.is_game_changer is False


def test_card_from_json_handles_null_price():
    payload = load("collection_basic.json")["data"][2]
    card = card_from_json(payload)
    assert card.usd is None
    assert card.is_banned is True


def test_card_from_json_flags_game_changers():
    payload = load("collection_basic.json")["data"][0]
    card = card_from_json(payload, game_changers=frozenset({"sol ring"}))
    assert card.is_game_changer is True


def test_card_from_json_uses_front_face_for_mdfc():
    payload = load("collection_mdfc.json")["data"][0]
    card = card_from_json(payload)
    # Front face supplies cost, colors, and text; top level supplies type_line.
    assert card.mana_cost == "{X}{B}{B}{B}"
    assert card.colors == frozenset({"B"})
    assert card.type_line == "Sorcery // Land"
    assert "Return from your graveyard" in card.oracle_text
    # The land back face must not leak into the text we classify on.
    assert "Add {B}" not in card.oracle_text
    assert card.is_mdfc_land is True
    assert card.is_land is False


def test_collection_raises_unresolved_with_offending_names():
    transport = FakeTransport({"data": [], "not_found": [{"name": "Nonexistent Xyz"}]})
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    with pytest.raises(UnresolvedCards) as excinfo:
        client.collection(["Nonexistent Xyz"])
    assert excinfo.value.names == ("Nonexistent Xyz",)
    assert "will not guess" in str(excinfo.value)


def test_collection_batches_requests_at_the_limit():
    names = [f"Card {i}" for i in range(76)]
    first = {"data": [{"name": n} for n in names[:75]], "not_found": []}
    second = {"data": [{"name": names[75]}], "not_found": []}
    transport = FakeTransport(first, second)
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    cards, missing = client.collection(names)
    assert len(transport.calls) == 2
    assert len(transport.calls[0][1]["identifiers"]) == 75
    assert len(transport.calls[1][1]["identifiers"]) == 1
    assert len(cards) == 76
    assert missing == ()


def test_collection_sleeps_between_requests():
    names = [f"Card {i}" for i in range(76)]
    transport = FakeTransport(
        {"data": [{"name": n} for n in names[:75]], "not_found": []},
        {"data": [{"name": names[75]}], "not_found": []},
    )
    slept = []
    client = ScryfallClient(transport=transport, sleep=slept.append)
    client.collection(names)
    assert slept and all(s >= 0.1 for s in slept)


def test_game_changers_returns_casefolded_names():
    transport = FakeTransport(load("game_changers.json"))
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    assert client.game_changers() == frozenset(
        {"rhystic study", "cyclonic rift", "smothering tithe"}
    )


def test_game_changers_raises_source_unavailable_on_transport_error():
    def boom(url, payload=None):
        raise OSError("connection reset")

    client = ScryfallClient(transport=boom, sleep=lambda _: None)
    with pytest.raises(SourceUnavailable):
        client.game_changers()


def test_resolve_matches_mdfc_requested_by_front_face_name():
    parsed = ParsedDeck(entries=(DeckEntry(qty=1, name="Agadeem's Awakening"),))
    transport = FakeTransport(load("collection_mdfc.json"), {"data": [], "has_more": False})
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    deck = resolve(parsed, client=client)
    assert deck.cards[0][1].name == "Agadeem's Awakening // Agadeem, the Undercrypt"


def test_resolve_separates_commanders_and_preserves_quantities():
    parsed = ParsedDeck(
        entries=(DeckEntry(qty=3, name="Sol Ring"),),
        commanders=(DeckEntry(qty=1, name="Atraxa, Praetors' Voice", is_commander=True),),
    )
    basic = load("collection_basic.json")
    transport = FakeTransport(basic, {"data": [], "has_more": False})
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    deck = resolve(parsed, client=client)
    assert [c.name for c in deck.commanders] == ["Atraxa, Praetors' Voice"]
    assert deck.cards == ((3, deck.cards[0][1]),)
    assert deck.cards[0][1].name == "Sol Ring"
    assert deck.total_with_commanders == 4


def test_resolve_degrades_when_game_changers_unavailable():
    """A Game Changers outage must not block the audit."""
    calls = {"n": 0}

    def transport(url, payload=None):
        calls["n"] += 1
        if "search" in url:
            raise OSError("scryfall search down")
        return load("collection_basic.json")

    parsed = ParsedDeck(entries=(DeckEntry(qty=1, name="Sol Ring"),))
    client = ScryfallClient(transport=transport, sleep=lambda _: None)
    deck = resolve(parsed, client=client)
    assert deck.cards[0][1].is_game_changer is False
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_scryfall.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtgpt.scryfall'`

- [ ] **Step 4: Write `mtgpt/scryfall.py`**

```python
# mtgpt/scryfall.py
"""Resolve card names against Scryfall.

The only module in mtgpt that performs network I/O. All requests pass through
an injectable `transport`, so tests exercise the mapping and batching logic
offline.

Scryfall asks clients to identify themselves and to leave 50-100ms between
requests. We send a real User-Agent and use the polite end of that range.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence

from .errors import SourceUnavailable, UnresolvedCards
from .models import Card, ParsedDeck, ResolvedDeck

API = "https://api.scryfall.com"

#: Scryfall accepts at most 75 identifiers per /cards/collection request.
COLLECTION_BATCH_SIZE = 75

#: Scryfall's requested courtesy delay between requests, in seconds.
REQUEST_DELAY = 0.1

USER_AGENT = "mtgpt/0.1"

Transport = Callable[..., dict]


def _http_transport(url: str, payload: dict | None = None) -> dict:
    """Default transport: POST when given a payload, GET otherwise."""
    data = None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _front_face(payload: dict) -> dict:
    """The face whose cost you pay. Falls back to the card itself."""
    faces = payload.get("card_faces")
    return faces[0] if faces else payload


def card_from_json(payload: dict, *, game_changers: frozenset[str] = frozenset()) -> Card:
    """Map a Scryfall card payload onto a Card.

    For multi-face cards, cost/colors/text come from the front face while
    `type_line` stays at the top level (so it reads "Sorcery // Land" and
    `is_mdfc_land` can do its job). Using only the front face's text is
    deliberate: a land back face would otherwise register as ramp.
    """
    front = _front_face(payload)
    name = payload.get("name", "")
    price = (payload.get("prices") or {}).get("usd")

    return Card(
        name=name,
        mana_value=float(payload.get("cmc") or 0.0),
        type_line=payload.get("type_line") or front.get("type_line", ""),
        oracle_text=front.get("oracle_text") or payload.get("oracle_text") or "",
        mana_cost=front.get("mana_cost") or payload.get("mana_cost") or "",
        color_identity=frozenset(payload.get("color_identity") or ()),
        colors=frozenset(front.get("colors") or payload.get("colors") or ()),
        legal_commander=(payload.get("legalities") or {}).get("commander", "unknown"),
        produced_mana=frozenset(payload.get("produced_mana") or ()),
        layout=payload.get("layout", "normal"),
        is_game_changer=name.casefold() in game_changers,
        usd=float(price) if price is not None else None,
        keywords=tuple(payload.get("keywords") or ()),
    )


class ScryfallClient:
    """Thin Scryfall wrapper with batching and courtesy delays."""

    def __init__(self, transport: Transport | None = None, sleep=time.sleep):
        self._transport = transport or _http_transport
        self._sleep = sleep

    def collection(
        self, names: Sequence[str]
    ) -> tuple[tuple[dict, ...], tuple[str, ...]]:
        """Resolve names in batches of 75.

        Raises UnresolvedCards if Scryfall reports any name as not found. This
        is the guard that stops invented or misspelled cards from proceeding.
        """
        found: list[dict] = []
        missing: list[str] = []

        for index in range(0, len(names), COLLECTION_BATCH_SIZE):
            batch = names[index : index + COLLECTION_BATCH_SIZE]
            payload = {"identifiers": [{"name": n} for n in batch]}
            if index:
                self._sleep(REQUEST_DELAY)
            try:
                body = self._transport(f"{API}/cards/collection", payload)
            except (urllib.error.URLError, OSError) as exc:
                raise SourceUnavailable("Scryfall", str(exc)) from exc
            found.extend(body.get("data") or ())
            for entry in body.get("not_found") or ():
                missing.append(entry.get("name", "<unknown>"))

        if missing:
            raise UnresolvedCards(missing)

        return tuple(found), tuple(missing)

    def game_changers(self) -> frozenset[str]:
        """Casefolded names on the current Game Changers list.

        WotC revises this list, so it is fetched rather than hardcoded.
        """
        names: set[str] = set()
        url = f"{API}/cards/search?q=is%3Agamechanger&unique=cards"
        while url:
            try:
                body = self._transport(url)
            except (urllib.error.URLError, OSError) as exc:
                raise SourceUnavailable("Scryfall Game Changers", str(exc)) from exc
            for card in body.get("data") or ():
                names.add(card["name"].casefold())
            url = body.get("next_page") if body.get("has_more") else None
            if url:
                self._sleep(REQUEST_DELAY)
        return frozenset(names)


def _index_by_name(payloads: Sequence[dict]) -> dict[str, dict]:
    """Index payloads by full name and by front-face name.

    Scryfall returns a modal DFC under its full "A // B" name even when it was
    requested as "A", and response order does not track request order.
    """
    index: dict[str, dict] = {}
    for payload in payloads:
        name = payload.get("name", "")
        index.setdefault(name.casefold(), payload)
        front, _, _ = name.partition("//")
        index.setdefault(front.strip().casefold(), payload)
        faces = payload.get("card_faces") or ()
        if faces:
            index.setdefault(faces[0].get("name", "").strip().casefold(), payload)
    return index


def resolve(deck: ParsedDeck, *, client: ScryfallClient | None = None) -> ResolvedDeck:
    """Turn a ParsedDeck of unverified names into a ResolvedDeck of real cards.

    Raises UnresolvedCards when any name fails to resolve. A Game Changers
    outage degrades instead: the audit proceeds with every card unflagged, and
    the caller is expected to say so in its report.
    """
    client = client or ScryfallClient()
    all_entries = list(deck.commanders) + list(deck.entries)
    names = [entry.name for entry in all_entries]

    payloads, _ = client.collection(names)

    try:
        game_changers = client.game_changers()
    except SourceUnavailable:
        game_changers = frozenset()

    index = _index_by_name(payloads)
    cards: dict[str, Card] = {}
    unmatched: list[str] = []

    for entry in all_entries:
        payload = index.get(entry.name.casefold())
        if payload is None:
            unmatched.append(entry.name)
            continue
        cards[entry.name] = card_from_json(payload, game_changers=game_changers)

    if unmatched:
        raise UnresolvedCards(unmatched)

    return ResolvedDeck(
        commanders=tuple(cards[e.name] for e in deck.commanders),
        cards=tuple((e.qty, cards[e.name]) for e in deck.entries),
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_scryfall.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 6: Commit**

```bash
git add mtgpt/scryfall.py tests/test_scryfall.py tests/fixtures/
git commit -m "feat: resolve card names against Scryfall

Batches at 75 identifiers, honors the courtesy delay, and turns the
not_found array into a raised UnresolvedCards so invented card names stop
the pipeline. Front-face mapping keeps a modal DFC's land back from
registering as ramp. A Game Changers outage degrades instead of failing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Legality validation

**Files:**
- Create: `mtgpt/validate.py`
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: `Card`, `ResolvedDeck`, `Violation`, `Severity` from Task 1.
- Produces: `validate(deck: ResolvedDeck) -> tuple[Violation, ...]`, sorted with errors first. Violation codes: `deck_size`, `commander_missing`, `commander_count`, `commander_not_legendary`, `singleton`, `color_identity`, `banned`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validate.py
from mtgpt.models import Card, ResolvedDeck, Severity
from mtgpt.validate import validate


def card(name, **kw):
    defaults = dict(
        name=name,
        mana_value=1.0,
        type_line="Artifact",
        oracle_text="",
        mana_cost="{1}",
        color_identity=frozenset(),
        colors=frozenset(),
        legal_commander="legal",
        produced_mana=frozenset(),
        layout="normal",
        is_game_changer=False,
        usd=None,
        keywords=(),
    )
    defaults.update(kw)
    return Card(**defaults)


ATRAXA = card(
    "Atraxa, Praetors' Voice",
    type_line="Legendary Creature — Phyrexian Angel Horror",
    color_identity=frozenset("WUBG"),
    mana_value=4.0,
)


def legal_deck(extra=()):
    """A structurally valid 100-card deck: commander + 63 spells + 36 basics."""
    spells = tuple((1, card(f"Spell {i}")) for i in range(63))
    lands = ((36, card("Forest", type_line="Basic Land — Forest",
                       color_identity=frozenset("G"))),)
    return ResolvedDeck(commanders=(ATRAXA,), cards=spells + lands + tuple(extra))


def codes(violations):
    return [v.code for v in violations]


def test_valid_deck_has_no_violations():
    assert validate(legal_deck()) == ()


def test_flags_wrong_deck_size():
    deck = ResolvedDeck(commanders=(ATRAXA,), cards=((10, card("Forest")),))
    assert "deck_size" in codes(validate(deck))


def test_flags_missing_commander():
    deck = ResolvedDeck(commanders=(), cards=((99, card("Forest")),))
    assert "commander_missing" in codes(validate(deck))


def test_allows_two_commanders_for_partner():
    partner_a = card("Commander A", type_line="Legendary Creature — Human")
    partner_b = card("Commander B", type_line="Legendary Creature — Human")
    spells = tuple((1, card(f"Spell {i}")) for i in range(62))
    lands = ((36, card("Forest", type_line="Basic Land — Forest")),)
    deck = ResolvedDeck(commanders=(partner_a, partner_b), cards=spells + lands)
    assert "commander_count" not in codes(validate(deck))


def test_flags_three_commanders():
    trio = tuple(card(f"C{i}", type_line="Legendary Creature — Human") for i in range(3))
    deck = ResolvedDeck(commanders=trio, cards=((97, card("Forest")),))
    assert "commander_count" in codes(validate(deck))


def test_flags_non_legendary_commander():
    deck = ResolvedDeck(
        commanders=(card("Grizzly Bears", type_line="Creature — Bear"),),
        cards=((99, card("Forest", type_line="Basic Land — Forest")),),
    )
    assert "commander_not_legendary" in codes(validate(deck))


def test_flags_duplicate_nonbasic():
    deck = legal_deck(extra=((2, card("Sol Ring")),))
    violations = [v for v in validate(deck) if v.code == "singleton"]
    assert violations and "Sol Ring" in violations[0].message


def test_allows_duplicate_basic_lands():
    deck = legal_deck()
    assert "singleton" not in codes(validate(deck))


def test_flags_color_identity_violation():
    deck = legal_deck(extra=((1, card("Lightning Bolt", color_identity=frozenset("R"))),))
    violations = [v for v in validate(deck) if v.code == "color_identity"]
    assert violations
    assert "Lightning Bolt" in violations[0].message
    assert violations[0].severity is Severity.ERROR


def test_colorless_card_never_violates_identity():
    deck = legal_deck()
    assert "color_identity" not in codes(validate(deck))


def test_flags_banned_card():
    deck = legal_deck(extra=((1, card("Dockside Extortionist", legal_commander="banned")),))
    violations = [v for v in validate(deck) if v.code == "banned"]
    assert violations and violations[0].severity is Severity.ERROR


def test_flags_commander_color_identity_against_itself():
    """A commander's own identity defines the deck, so it can never violate."""
    deck = legal_deck()
    assert not [v for v in validate(deck) if "Atraxa" in v.message]


def test_errors_sort_before_warnings():
    deck = ResolvedDeck(commanders=(), cards=((5, card("Forest")),))
    violations = validate(deck)
    severities = [v.severity for v in violations]
    assert severities == sorted(severities)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_validate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtgpt.validate'`

- [ ] **Step 3: Write `mtgpt/validate.py`**

```python
# mtgpt/validate.py
"""Check a resolved deck against Commander's construction rules.

Returns findings rather than raising, so a report can show every problem at
once instead of surfacing them one run at a time.
"""

from __future__ import annotations

from .models import ResolvedDeck, Severity, Violation

#: A Commander deck is 100 cards including the command zone.
DECK_SIZE = 100


def validate(deck: ResolvedDeck) -> tuple[Violation, ...]:
    """Return every rules violation found, errors first."""
    findings: list[Violation] = []
    findings.extend(_check_size(deck))
    findings.extend(_check_commanders(deck))
    findings.extend(_check_singleton(deck))
    findings.extend(_check_color_identity(deck))
    findings.extend(_check_banned(deck))
    return tuple(sorted(findings))


def _check_size(deck: ResolvedDeck) -> list[Violation]:
    total = deck.total_with_commanders
    if total == DECK_SIZE:
        return []
    direction = "too few" if total < DECK_SIZE else "too many"
    delta = abs(DECK_SIZE - total)
    return [
        Violation(
            severity=Severity.ERROR,
            code="deck_size",
            message=(
                f"Deck has {total} cards including the command zone; "
                f"Commander requires {DECK_SIZE} ({direction} by {delta})."
            ),
        )
    ]


def _check_commanders(deck: ResolvedDeck) -> list[Violation]:
    findings: list[Violation] = []
    if not deck.commanders:
        findings.append(
            Violation(
                severity=Severity.ERROR,
                code="commander_missing",
                message=(
                    "No commander found. Mark it with a 'Commander' section header "
                    "or a '*CMDR*' tag in the decklist."
                ),
            )
        )
        return findings

    if len(deck.commanders) > 2:
        findings.append(
            Violation(
                severity=Severity.ERROR,
                code="commander_count",
                message=(
                    f"{len(deck.commanders)} commanders declared. Only Partner, "
                    "Friends Forever, or Background pairs may exceed one."
                ),
            )
        )

    for commander in deck.commanders:
        if "Legendary" not in commander.type_line and "Background" not in commander.type_line:
            findings.append(
                Violation(
                    severity=Severity.ERROR,
                    code="commander_not_legendary",
                    message=(
                        f"{commander.name} is not legendary, so it cannot be a "
                        f"commander (type: {commander.type_line})."
                    ),
                )
            )
    return findings


def _check_singleton(deck: ResolvedDeck) -> list[Violation]:
    findings: list[Violation] = []
    for qty, card in deck.cards:
        if qty > 1 and not card.is_basic_land:
            findings.append(
                Violation(
                    severity=Severity.ERROR,
                    code="singleton",
                    message=(
                        f"{card.name} appears {qty} times. Commander is singleton; "
                        "only basic lands may repeat."
                    ),
                )
            )
    return findings


def _check_color_identity(deck: ResolvedDeck) -> list[Violation]:
    if not deck.commanders:
        return []
    allowed = deck.command_zone_identity
    findings: list[Violation] = []
    for _, card in deck.cards:
        outside = card.color_identity - allowed
        if outside:
            symbols = "".join(sorted(outside))
            findings.append(
                Violation(
                    severity=Severity.ERROR,
                    code="color_identity",
                    message=(
                        f"{card.name} has color identity outside the commander's: "
                        f"{{{symbols}}} not in {{{''.join(sorted(allowed)) or 'C'}}}."
                    ),
                )
            )
    return findings


def _check_banned(deck: ResolvedDeck) -> list[Violation]:
    findings: list[Violation] = []
    for card in list(deck.commanders) + [c for _, c in deck.cards]:
        if card.is_banned:
            findings.append(
                Violation(
                    severity=Severity.ERROR,
                    code="banned",
                    message=f"{card.name} is banned in Commander.",
                )
            )
        elif not card.is_legal:
            findings.append(
                Violation(
                    severity=Severity.WARNING,
                    code="banned",
                    message=(
                        f"{card.name} is not legal in Commander "
                        f"(status: {card.legal_commander})."
                    ),
                )
            )
    return findings
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_validate.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/validate.py tests/test_validate.py
git commit -m "feat: validate Commander construction rules

Deck size, commander count and legendary status, singleton with the basic
land exemption, color identity against the command zone, and banned list.
Returns sorted findings so one run shows every problem.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Function classification

Tags each card with the roles it plays. Two decisions here carry weight beyond this module:

1. **`produced_mana` cannot drive ramp detection alone.** Verified against the live API: `Dockside Extortionist` reports `produced_mana: ["B","G","R","U","W"]` because it makes Treasures. Ramp detection therefore requires a mana-adding pattern in the oracle text, not merely the presence of `produced_mana`.
2. **A land tutor is not a `TUTOR`.** Cultivate searches the library, but the bracket rules care about tutoring that assembles combos and finds answers. Tagging Cultivate as a tutor would inflate tutor density and misreport the bracket. `TUTOR` therefore excludes searches restricted to lands, while those still count as `RAMP`.

Likewise `MASS_LAND_DENIAL` suppresses `SWEEPER`: Armageddon reads "Destroy all lands", which matches a sweeper pattern, but counting it toward the creature-removal package would be wrong.

**Files:**
- Create: `mtgpt/classify.py`
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes: `Card`, `Function` from Task 1.
- Produces: `classify(card: Card) -> frozenset[Function]` and `classify_deck(deck: ResolvedDeck) -> dict[str, frozenset[Function]]` keyed by card name.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classify.py
import pytest

from mtgpt.classify import classify, classify_deck
from mtgpt.models import Card, Function, ResolvedDeck

F = Function


def card(name, type_line, oracle_text, **kw):
    defaults = dict(
        name=name,
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        mana_cost="{1}{G}",
        color_identity=frozenset("G"),
        colors=frozenset("G"),
        legal_commander="legal",
        produced_mana=frozenset(),
        layout="normal",
        is_game_changer=False,
        usd=None,
        keywords=(),
    )
    defaults.update(kw)
    return Card(**defaults)


def test_basic_land_is_land_only():
    forest = card("Forest", "Basic Land — Forest", "({T}: Add {G}.)",
                  produced_mana=frozenset("G"))
    assert classify(forest) == frozenset({F.LAND})


def test_utility_land_is_land():
    tower = card("Command Tower", "Land",
                 "{T}: Add one mana of any color in your commander's color identity.",
                 produced_mana=frozenset("WUBRG"))
    assert F.LAND in classify(tower)


def test_mana_rock_is_ramp():
    sol = card("Sol Ring", "Artifact", "{T}: Add {C}{C}.",
               produced_mana=frozenset("C"))
    assert F.RAMP in classify(sol)
    assert F.LAND not in classify(sol)


def test_mana_dork_is_ramp():
    birds = card("Birds of Paradise", "Creature — Bird",
                 "Flying\n{T}: Add one mana of any color.",
                 produced_mana=frozenset("WUBRG"))
    assert F.RAMP in classify(birds)


def test_treasure_maker_is_ramp():
    dockside = card(
        "Dockside Extortionist", "Creature — Goblin Pirate",
        "When this creature enters, create X Treasure tokens, where X is the "
        "number of artifacts and enchantments your opponents control.",
        produced_mana=frozenset("WUBRG"),
    )
    assert F.RAMP in classify(dockside)


def test_produced_mana_alone_does_not_make_ramp():
    """A creature that makes no mana must not be ramp just because Scryfall
    lists produced_mana for an unrelated reason."""
    decoy = card("Decoy", "Creature — Human", "Flying",
                 produced_mana=frozenset("WUBRG"))
    assert F.RAMP not in classify(decoy)


def test_land_ramp_spell_is_ramp_but_not_tutor():
    cultivate = card(
        "Cultivate", "Sorcery",
        "Search your library for up to two basic land cards, reveal those cards, "
        "put one onto the battlefield tapped and the other into your hand, then shuffle.",
    )
    tags = classify(cultivate)
    assert F.RAMP in tags
    assert F.TUTOR not in tags


def test_unrestricted_search_is_a_tutor():
    demonic = card("Demonic Tutor", "Sorcery",
                   "Search your library for a card, put that card into your hand, "
                   "then shuffle.")
    assert F.TUTOR in classify(demonic)


def test_spot_removal_requires_a_target():
    stp = card("Swords to Plowshares", "Instant",
               "Exile target creature. Its controller gains life equal to its power.")
    tags = classify(stp)
    assert F.SPOT_REMOVAL in tags
    assert F.SWEEPER not in tags


def test_sweeper_hits_all_creatures():
    wrath = card("Wrath of God", "Sorcery",
                 "Destroy all creatures. They can't be regenerated.")
    tags = classify(wrath)
    assert F.SWEEPER in tags
    assert F.SPOT_REMOVAL not in tags


def test_mass_land_denial_is_not_counted_as_a_sweeper():
    armageddon = card("Armageddon", "Sorcery", "Destroy all lands.")
    tags = classify(armageddon)
    assert F.MASS_LAND_DENIAL in tags
    assert F.SWEEPER not in tags


@pytest.mark.parametrize(
    "name,oracle,is_mld,is_sweeper",
    [
        ("Armageddon", "Destroy all lands.", True, False),
        ("Ravages of War", "Destroy all lands.", True, False),
        # "lands" is not adjacent to "all" on the classic MLD cards.
        ("Jokulhaups",
         "Destroy all artifacts, creatures, and lands. They can't be regenerated.",
         True, True),
        ("Devastation", "Destroy all creatures and lands.", True, True),
        ("Wrath of God", "Destroy all creatures. They can't be regenerated.", False, True),
        # "nonland" must not register as a land.
        ("Nonland wipe", "Destroy all nonland permanents.", False, True),
        ("Cyclonic Rift overload",
         "Return all nonland permanents you don't control to their owners' hands.",
         False, False),
    ],
    ids=["armageddon", "ravages", "jokulhaups", "devastation", "wrath", "nonland", "rift"],
)
def test_mass_land_denial_detection(name, oracle, is_mld, is_sweeper):
    tags = classify(card(name, "Sorcery", oracle))
    assert (F.MASS_LAND_DENIAL in tags) is is_mld
    assert (F.SWEEPER in tags) is is_sweeper


@pytest.mark.parametrize(
    "name,oracle",
    [
        ("Time Warp", "Target player takes an extra turn after this one."),
        ("Nexus of Fate", "Take an extra turn after this one."),
        # Plural: "takes two extra turns".
        ("Time Stretch", "Target player takes two extra turns after this one."),
    ],
    ids=["time-warp", "nexus", "time-stretch"],
)
def test_extra_turn_spells_detected(name, oracle):
    assert F.EXTRA_TURNS in classify(card(name, "Sorcery", oracle))


def test_counterspell():
    cs = card("Counterspell", "Instant", "Counter target spell.")
    assert F.COUNTERSPELL in classify(cs)


def test_card_draw():
    div = card("Divination", "Sorcery", "Draw two cards.")
    assert F.DRAW in classify(div)


def test_cantrip_draw_a_card():
    ancestral = card("Ancestral Recall", "Instant", "Target player draws three cards.")
    assert F.DRAW in classify(ancestral)


def test_protection_effects():
    teferi = card("Teferi's Protection", "Instant",
                  "Until your next turn, your life total can't change and you gain "
                  "protection from everything. Phase out all permanents you control.")
    assert F.PROTECTION in classify(teferi)


def test_extra_turns():
    time_warp = card("Time Warp", "Sorcery", "Target player takes an extra turn after this one.")
    assert F.EXTRA_TURNS in classify(time_warp)


def test_recursion():
    regrowth = card("Regrowth", "Sorcery",
                    "Return target card from your graveyard to your hand.")
    assert F.RECURSION in classify(regrowth)


def test_explicit_wincon():
    lab_man = card("Laboratory Maniac", "Creature — Human Wizard",
                   "If you would draw a card while your library has no cards in it, "
                   "you win the game instead.")
    assert F.WINCON in classify(lab_man)


def test_cards_can_carry_several_functions():
    """Cultivate fetches land and shuffles: ramp, and not a bare SYNERGY tag."""
    cultivate = card(
        "Cultivate", "Sorcery",
        "Search your library for up to two basic land cards, reveal those cards, "
        "put one onto the battlefield tapped and the other into your hand, then shuffle.",
    )
    tags = classify(cultivate)
    assert F.RAMP in tags
    assert F.SYNERGY not in tags

    beast_within = card("Beast Within", "Instant",
                        "Destroy target permanent. Its controller creates a 3/3 green "
                        "Beast creature token.")
    beast_tags = classify(beast_within)
    assert F.SPOT_REMOVAL in beast_tags
    assert F.SYNERGY not in beast_tags


def test_uncategorized_card_falls_back_to_synergy():
    pet = card("Weird Pet Card", "Creature — Bear", "Trample")
    assert classify(pet) == frozenset({F.SYNERGY})


def test_synergy_is_not_added_alongside_real_tags():
    sol = card("Sol Ring", "Artifact", "{T}: Add {C}{C}.", produced_mana=frozenset("C"))
    assert F.SYNERGY not in classify(sol)


def test_mdfc_land_is_not_tagged_land():
    agadeem = card(
        "Agadeem's Awakening // Agadeem, the Undercrypt", "Sorcery // Land",
        "Return from your graveyard to the battlefield any number of target "
        "creature cards that each have a different mana value X or less.",
        layout="modal_dfc", produced_mana=frozenset("B"),
    )
    tags = classify(agadeem)
    assert F.LAND not in tags
    assert F.RECURSION in tags


# Real oracle text from Scryfall, 2026-09-30. Each of these was misclassified by
# an earlier draft of the regexes above; they are the reason those regexes look
# the way they do. Keep them.
REAL_STAPLES = [
    # (name, type_line, oracle_text, must_include, must_exclude)
    ("Nature's Lore", "Sorcery",
     "Search your library for a Forest card, put that card onto the battlefield, then shuffle.",
     {F.RAMP}, {F.TUTOR}),
    ("Three Visits", "Sorcery",
     "Search your library for a Forest card, put it onto the battlefield, then shuffle.",
     {F.RAMP}, {F.TUTOR}),
    ("Farseek", "Sorcery",
     "Search your library for a Plains, Island, Swamp, or Mountain card, put it onto "
     "the battlefield tapped, then shuffle.",
     {F.RAMP}, {F.TUTOR}),
    ("Swan Song", "Instant",
     "Counter target enchantment, instant, or sorcery spell. Its controller creates a "
     "2/2 blue Bird creature token with flying.",
     {F.COUNTERSPELL}, {F.SPOT_REMOVAL}),
    ("Dovin's Veto", "Instant",
     "This spell can't be countered.\nCounter target noncreature spell.",
     {F.COUNTERSPELL}, {F.SPOT_REMOVAL}),
    ("Flusterstorm", "Instant",
     "Counter target instant or sorcery spell unless its controller pays {1}.",
     {F.COUNTERSPELL}, {F.SPOT_REMOVAL}),
    ("Blasphemous Act", "Sorcery",
     "This spell costs {1} less to cast for each creature on the battlefield.\n"
     "Blasphemous Act deals 13 damage to each creature.",
     {F.SWEEPER}, {F.SPOT_REMOVAL}),
    ("Toxic Deluge", "Sorcery",
     "As an additional cost to cast this spell, pay X life.\n"
     "All creatures get -X/-X until end of turn.",
     {F.SWEEPER}, set()),
    ("Cyclonic Rift", "Instant",
     "Return target nonland permanent you don't control to its owner's hand.\n"
     "Overload {6}{U}",
     {F.SPOT_REMOVAL}, set()),
    ("Timetwister", "Sorcery",
     "Each player shuffles their hand and graveyard into their library, then draws "
     "seven cards.",
     {F.DRAW}, set()),
    ("Smothering Tithe", "Enchantment",
     "Whenever an opponent draws a card, that player may pay {2}. If the player "
     "doesn't, you create a Treasure token.",
     {F.RAMP}, {F.DRAW}),
    ("Eternal Witness", "Creature — Human Shaman",
     "When this creature enters, return target card from your graveyard to your hand.",
     {F.RECURSION}, {F.SPOT_REMOVAL}),
]


@pytest.mark.parametrize(
    "name,type_line,oracle,must_include,must_exclude",
    REAL_STAPLES,
    ids=[s[0] for s in REAL_STAPLES],
)
def test_real_staples_classify_correctly(name, type_line, oracle, must_include, must_exclude):
    tags = classify(card(name, type_line, oracle))
    assert must_include <= tags, f"{name}: expected {must_include}, got {tags}"
    assert not (must_exclude & tags), f"{name}: must not be {must_exclude & tags}, got {tags}"


def test_classify_deck_keys_by_name():
    sol = card("Sol Ring", "Artifact", "{T}: Add {C}{C}.", produced_mana=frozenset("C"))
    forest = card("Forest", "Basic Land — Forest", "({T}: Add {G}.)",
                  produced_mana=frozenset("G"))
    deck = ResolvedDeck(commanders=(), cards=((1, sol), (36, forest)))
    tags = classify_deck(deck)
    assert tags["Sol Ring"] == frozenset({F.RAMP})
    assert tags["Forest"] == frozenset({F.LAND})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtgpt.classify'`

- [ ] **Step 3: Write `mtgpt/classify.py`**

```python
# mtgpt/classify.py
"""Tag each card with the functions it performs.

Classification is heuristic and deliberately visible: the report prints tags
per card so a wrong tag can be spotted and corrected, rather than silently
skewing the ratios it feeds.

Two rules deserve their reasoning stated:

* Ramp needs a mana-adding pattern in the oracle text. Scryfall's
  `produced_mana` is populated for cards that merely make Treasure or similar,
  so the field alone produces false positives.
* A search restricted to lands is ramp, not a tutor. Bracket rules count
  tutors as combo assembly and answer-finding; counting Cultivate would
  misreport tutor density.
"""

from __future__ import annotations

import re

from .models import Card, Function, ResolvedDeck

F = Function

_ADDS_MANA = re.compile(
    r"(\{T\}\s*:\s*Add|\badd one mana\b|\badd two mana\b|\badd \{|"
    r"\badds? .{0,20}mana\b|create .{0,20}\bTreasure\b)",
    re.IGNORECASE,
)
_SEARCH_LIBRARY = re.compile(r"search your library", re.IGNORECASE)
#: A land fetch names either the word "land" or a basic land TYPE. Nature's Lore,
#: Three Visits, and Farseek say "Forest card" / "Plains ... card" and never the
#: word "land", so omitting the type names misfiles the format's most-played ramp
#: spells as tutors — understating ramp and inflating tutor density at once.
_LAND_SEARCH = re.compile(
    r"search your library for (?:up to )?(?:a|an|one|two|three|four|X|\d+)?\s*"
    r"(?:basic )?(?:lands?|Plains|Island|Swamp|Mountain|Forest|Wastes)\b",
    re.IGNORECASE,
)
_DRAW = re.compile(
    r"\bdraws?\s+(?:a\s+card|one|two|three|four|five|six|seven|eight|nine|ten|X|\d+)\b",
    re.IGNORECASE,
)
#: "Whenever an opponent draws a card" is not card draw for us. Masked out before
#: _DRAW runs, so Smothering Tithe counts as ramp only.
_OPPONENT_DRAW = re.compile(
    r"\bopponents?\s+draws?\s+(?:a\s+card|\w+\s+cards?)", re.IGNORECASE
)
#: Bounce is removal. "owner's hand" is what separates it from graveyard
#: recursion, which returns to "your hand".
_SPOT_REMOVAL = re.compile(
    r"(destroy|exile)\s+target\b|"
    r"target\s+(creature|permanent|player)\s+(?:gets|sacrifices)|"
    r"return target .{0,60}?to (?:its|their) owner'?s hand",
    re.IGNORECASE,
)
#: Not every wipe says "destroy". Blasphemous Act deals damage to each creature;
#: Toxic Deluge gives all creatures -X/-X.
_SWEEPER = re.compile(
    r"(destroy|exile)\s+(all|each|every)\b|"
    r"each player sacrifices|"
    r"deals \S+ damage to each (?:creature|other creature)|"
    r"all creatures get -",
    re.IGNORECASE,
)
#: Mass land denial. "lands" need not sit immediately after "all": Jokulhaups
#: destroys "all artifacts, creatures, and lands" and Devastation "all creatures
#: and lands". Requiring adjacency misses the format's defining MLD cards, and
#: MLD is the rule brackets 1-3 care most about. [^.] keeps the match inside one
#: sentence; \blands?\b will not fire on "nonland".
_MASS_LAND_DENIAL = re.compile(
    r"(?:destroy|exile)\s+(?:all|each)\b[^.]{0,60}?\blands?\b|"
    r"each player sacrifices\s+(?:a|an|all|X|\d+)?\s*lands?\b",
    re.IGNORECASE,
)
#: Denial that hits ONLY lands. Armageddon is not a creature sweeper, but
#: Jokulhaups genuinely is both, so only the lands-only form suppresses SWEEPER.
_LANDS_ONLY_DENIAL = re.compile(r"(?:destroy|exile)\s+(?:all|each)\s+lands?\b", re.IGNORECASE)
#: Real counterspells rarely read "counter target spell": Swan Song says
#: "Counter target enchantment, instant, or sorcery spell", Dovin's Veto says
#: "noncreature spell". A window between "target" and "spell" catches them.
_COUNTERSPELL = re.compile(r"counter target\b.{0,60}?\b(?:spell|ability)\b", re.IGNORECASE)
_PROTECTION = re.compile(
    r"\bhexproof\b|\bindestructible\b|protection from|\bphases? out\b|"
    r"\bshroud\b|can't be countered|sacrifice .{0,30}\binstead\b",
    re.IGNORECASE,
)
#: "takes an extra turn", but also Time Stretch's "takes two extra turns".
_EXTRA_TURNS = re.compile(r"takes?\s+\w+\s+extra\s+turns?", re.IGNORECASE)
_WINCON = re.compile(r"\bwins? the game\b|\bloses? the game\b", re.IGNORECASE)
_RECURSION = re.compile(
    r"return .{0,60}from (?:your|a|target player's) graveyard", re.IGNORECASE
)


def classify(card: Card) -> frozenset[Function]:
    """Return every function this card performs.

    Lands short-circuit: a land is tagged LAND and nothing else, so a utility
    land's tap-for-mana text does not also register it as ramp.
    """
    if card.is_land:
        return frozenset({F.LAND})

    text = card.oracle_text or ""
    tags: set[Function] = set()

    if _is_ramp(card, text):
        tags.add(F.RAMP)
    if _SEARCH_LIBRARY.search(text) and not _LAND_SEARCH.search(text):
        tags.add(F.TUTOR)
    if _DRAW.search(_OPPONENT_DRAW.sub(" ", text)):
        tags.add(F.DRAW)
    if _COUNTERSPELL.search(text):
        tags.add(F.COUNTERSPELL)
    if _MASS_LAND_DENIAL.search(text):
        tags.add(F.MASS_LAND_DENIAL)
    # Lands-only denial is not a creature sweeper; a list-form wipe like
    # Jokulhaups is both, so only suppress on the lands-only form.
    if _SWEEPER.search(text) and not _LANDS_ONLY_DENIAL.search(text):
        tags.add(F.SWEEPER)
    # A counterspell is its own category, never spot removal.
    if (
        _SPOT_REMOVAL.search(text)
        and F.SWEEPER not in tags
        and F.COUNTERSPELL not in tags
    ):
        tags.add(F.SPOT_REMOVAL)
    if _PROTECTION.search(text) or _has_protection_keyword(card):
        tags.add(F.PROTECTION)
    if _EXTRA_TURNS.search(text):
        tags.add(F.EXTRA_TURNS)
    if _WINCON.search(text):
        tags.add(F.WINCON)
    if _RECURSION.search(text):
        tags.add(F.RECURSION)

    return frozenset(tags) if tags else frozenset({F.SYNERGY})


def _is_ramp(card: Card, text: str) -> bool:
    """Ramp is either an explicit mana-adding effect or a land fetch.

    Requiring the text pattern is what keeps `produced_mana` noise out.
    """
    if _LAND_SEARCH.search(text):
        return True
    if not _ADDS_MANA.search(text):
        return False
    # A creature or artifact that adds mana is ramp; a land was excluded above.
    return True


def _has_protection_keyword(card: Card) -> bool:
    protective = {"Hexproof", "Indestructible", "Shroud", "Ward"}
    return bool(protective & set(card.keywords))


def classify_deck(deck: ResolvedDeck) -> dict[str, frozenset[Function]]:
    """Classify every distinct card in the deck, keyed by card name."""
    tags = {card.name: classify(card) for _, card in deck.cards}
    for commander in deck.commanders:
        tags[commander.name] = classify(commander)
    return tags
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_classify.py -v`
Expected: PASS, 45 tests (23 named + 12 real staples + 7 MLD + 3 extra-turn).

If `test_treasure_maker_is_ramp` and `test_produced_mana_alone_does_not_make_ramp` cannot both pass, the Treasure clause in `_ADDS_MANA` is matching too broadly or too narrowly — adjust that clause only, and do not fall back to keying on `produced_mana`, which is the bug both tests exist to prevent.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/classify.py tests/test_classify.py
git commit -m "feat: tag cards by deck function

Ramp detection requires a mana-adding oracle pattern rather than
produced_mana, which Scryfall populates for Treasure makers. Land searches
count as ramp but not as tutors, and mass land denial suppresses the
sweeper tag, so neither inflates the removal or tutor counts.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Targets and audit

`targets.py` is the single source of truth for every threshold. The reference markdown explains the reasoning and points here for values, so the two cannot drift.

The pip analysis compares, per color, the most demanding single card's pip count against the number of sources that can produce that color. Thresholds are adapted from Frank Karsten's methodology for 100-card singleton decks; they are heuristics encoded as configuration, not settled fact, and are tunable in one place.

**Files:**
- Create: `mtgpt/targets.py`
- Create: `mtgpt/audit.py`
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: `Card`, `Function`, `ResolvedDeck`, `Violation`, `Severity` from Task 1; `classify_deck` from Task 5.
- Produces:
  - `targets.py`: `LAND`, `RAMP`, `DRAW`, `SPOT_REMOVAL`, `SWEEPER`, `PROTECTION`, `MANA_SOURCES` (each a `(min, max)` tuple), `AVERAGE_MV_BAND`, `PIP_SOURCE_MINIMUMS`, `CATEGORY_TARGETS`
  - `audit.py`: `CategoryCount`, `PipReport`, `AuditReport`, and `audit(deck, tags=None) -> AuditReport`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_audit.py
import pytest

from mtgpt import targets
from mtgpt.audit import audit
from mtgpt.models import Card, Function, ResolvedDeck

F = Function


def card(name, type_line="Creature — Bear", oracle_text="", mv=2.0, cost="{1}{G}",
         produced=(), identity="G"):
    return Card(
        name=name,
        mana_value=mv,
        type_line=type_line,
        oracle_text=oracle_text,
        mana_cost=cost,
        color_identity=frozenset(identity),
        colors=frozenset(identity),
        legal_commander="legal",
        produced_mana=frozenset(produced),
        layout="normal",
        is_game_changer=False,
        usd=None,
        keywords=(),
    )


FOREST = card("Forest", "Basic Land — Forest", "({T}: Add {G}.)", mv=0.0, cost="",
              produced="G")
SOL_RING = card("Sol Ring", "Artifact", "{T}: Add {C}{C}.", mv=1.0, cost="{1}",
                produced="C", identity="")


def build(cards, commanders=()):
    return ResolvedDeck(commanders=commanders, cards=tuple(cards))


def test_counts_lands_separately_from_mdfc_lands():
    agadeem = card(
        "Agadeem's Awakening // Agadeem, the Undercrypt",
        "Sorcery // Land", "Return from your graveyard...", mv=6.0,
        cost="{X}{B}{B}{B}", produced="B", identity="B",
    )
    report = audit(build([(30, FOREST), (1, agadeem)]))
    assert report.land_count == 30
    assert report.mdfc_land_count == 1


def test_average_mana_value_excludes_lands():
    deck = build([(36, FOREST), (1, card("Two Drop", mv=2.0)), (1, card("Four Drop", mv=4.0))])
    assert report_mv(deck) == pytest.approx(3.0)


def report_mv(deck):
    return audit(deck).average_mana_value


def test_average_mana_value_is_zero_for_an_all_land_deck():
    assert report_mv(build([(36, FOREST)])) == 0.0


def test_curve_buckets_nonland_cards_with_seven_plus_bucket():
    deck = build([
        (1, card("One", mv=1.0)), (1, card("Two", mv=2.0)),
        (1, card("Big", mv=9.0)), (36, FOREST),
    ])
    curve = dict(audit(deck).curve)
    assert curve[1] == 1
    assert curve[2] == 1
    assert curve[7] == 1  # the 7+ bucket
    assert 9 not in curve


def test_category_count_reports_target_band_and_status():
    deck = build([(36, FOREST), (1, SOL_RING)])
    report = audit(deck)
    ramp = next(c for c in report.categories if c.function is F.RAMP)
    assert ramp.count == 1
    assert (ramp.target_min, ramp.target_max) == targets.RAMP
    assert ramp.status == "low"


def test_category_status_ok_when_inside_band():
    deck = build([(36, FOREST)] + [(1, card(f"Rock {i}", "Artifact", "{T}: Add {C}.",
                                            mv=2.0, cost="{2}", produced="C"))
                                   for i in range(11)])
    report = audit(deck)
    ramp = next(c for c in report.categories if c.function is F.RAMP)
    assert ramp.count == 11
    assert ramp.status == "ok"


def test_category_status_high_above_band():
    deck = build([(36, FOREST)] + [(1, card(f"Rock {i}", "Artifact", "{T}: Add {C}.",
                                            mv=2.0, cost="{2}", produced="C"))
                                   for i in range(20)])
    ramp = next(c for c in audit(deck).categories if c.function is F.RAMP)
    assert ramp.status == "high"


def test_land_count_respects_quantities():
    assert audit(build([(36, FOREST)])).land_count == 36


def test_mana_sources_include_lands_ramp_and_mdfc():
    deck = build([(36, FOREST), (1, SOL_RING)])
    assert audit(deck).mana_sources == 37


def test_pip_report_counts_most_demanding_card():
    triple = card("Triple Green", oracle_text="", mv=3.0, cost="{G}{G}{G}")
    deck = build([(36, FOREST), (1, triple)])
    green = next(p for p in audit(deck).pips if p.color == "G")
    assert green.max_pips == 3
    assert green.sources == 36
    assert green.required == targets.PIP_SOURCE_MINIMUMS[3]
    assert green.ok is True


def test_pip_report_flags_insufficient_sources():
    triple = card("Triple Blue", oracle_text="", mv=3.0, cost="{U}{U}{U}", identity="U")
    deck = build([(36, FOREST), (1, triple)])
    blue = next(p for p in audit(deck).pips if p.color == "U")
    assert blue.sources == 0
    assert blue.ok is False


def test_pip_report_omits_unused_colors():
    deck = build([(36, FOREST), (1, card("Green Bear", cost="{1}{G}"))])
    assert {p.color for p in audit(deck).pips} == {"G"}


def test_generic_and_x_costs_are_not_pips():
    deck = build([(36, FOREST), (1, card("Xy", cost="{X}{5}", identity=""))])
    assert audit(deck).pips == ()


def test_colorless_and_snow_symbols_are_not_pips():
    deck = build([(36, FOREST),
                  (1, card("Rock", "Artifact", mv=2.0, cost="{C}{C}", identity="")),
                  (1, card("Snowy", "Artifact", mv=2.0, cost="{2}{S}", identity=""))])
    assert audit(deck).pips == ()


@pytest.mark.parametrize(
    "cost,color,expected_max_pips",
    [
        ("{2}{G/W}", "G", 1),
        ("{2}{G/W}", "W", 1),
        ("{G/P}", "G", 1),
        # Monocolored hybrid: the left side is generic. Reporting zero pips here
        # would understate the deck's need for white sources.
        ("{2/W}{2/W}{2/W}", "W", 3),
        ("{2/B}{2/B}{2/B}", "B", 3),
    ],
    ids=["hybrid-G", "hybrid-W", "phyrexian", "spectral-procession", "beseech"],
)
def test_hybrid_and_monocolored_hybrid_pips_are_counted(cost, color, expected_max_pips):
    identity = "".join(sorted({c for c in cost if c in "WUBRG"}))
    deck = build([(36, FOREST), (1, card("Hybrid Card", cost=cost, mv=3.0, identity=identity))])
    report = next(p for p in audit(deck).pips if p.color == color)
    assert report.max_pips == expected_max_pips


def test_audit_accepts_precomputed_tags():
    deck = build([(36, FOREST), (1, SOL_RING)])
    tags = {"Forest": frozenset({F.LAND}), "Sol Ring": frozenset({F.DRAW})}
    report = audit(deck, tags=tags)
    draw = next(c for c in report.categories if c.function is F.DRAW)
    assert draw.count == 1


def test_average_mv_band_flagged_when_out_of_range():
    deck = build([(36, FOREST)] + [(1, card(f"Fatty {i}", mv=8.0, cost="{8}"))
                                   for i in range(10)])
    report = audit(deck)
    assert report.average_mana_value > targets.AVERAGE_MV_BAND[1]
    assert report.curve_status == "high"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtgpt.targets'`

- [ ] **Step 3: Write `mtgpt/targets.py`**

```python
# mtgpt/targets.py
"""Every threshold the audit checks, in one place.

The prose rationale lives in skills/mtgpt/references/deckbuilding-hygiene.md.
That file explains the numbers; this file defines them. Keeping values in code
and reasoning in markdown is what stops the two from drifting apart.

All bands are inclusive (min, max) tuples.
"""

from __future__ import annotations

from .models import Function

#: Lands in the 99. Flexes down with a low curve and heavy ramp.
LAND = (36, 38)

#: Mana rocks, dorks, and land-fetch spells.
RAMP = (10, 12)

#: Repeatable and burst card advantage.
DRAW = (8, 12)

#: Targeted answers to a single threat.
SPOT_REMOVAL = (5, 8)

#: Board wipes. Fewer if the deck itself goes wide.
SWEEPER = (2, 3)

#: Ways to protect the commander or the board.
PROTECTION = (3, 5)

#: Lands plus ramp plus MDFC land backs.
MANA_SOURCES = (46, 50)

#: Acceptable average mana value across the nonland cards.
AVERAGE_MV_BAND = (2.8, 3.2)

#: Colored sources needed to cast a card with N pips of a color on curve.
#: Adapted from Frank Karsten's source-count methodology for 100-card
#: singleton decks. Heuristic, and tunable here.
PIP_SOURCE_MINIMUMS = {1: 14, 2: 20, 3: 26}

#: Functions the audit reports a target band for, in report order.
CATEGORY_TARGETS: dict[Function, tuple[int, int]] = {
    Function.LAND: LAND,
    Function.RAMP: RAMP,
    Function.DRAW: DRAW,
    Function.SPOT_REMOVAL: SPOT_REMOVAL,
    Function.SWEEPER: SWEEPER,
    Function.PROTECTION: PROTECTION,
}
```

- [ ] **Step 4: Write `mtgpt/audit.py`**

```python
# mtgpt/audit.py
"""Measure a deck against the targets in targets.py.

Reports actual-versus-target for each category so drift is visible rather
than arguable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import targets
from .classify import classify_deck
from .models import Function, ResolvedDeck

F = Function

#: Colored mana symbols in a mana cost. Generic, X, and colorless are excluded
#: because they place no demand on the mana base's colors.
_PIP_RE = re.compile(r"\{([WUBRG])\}")

#: Hybrid, phyrexian, and monocolored-hybrid symbols: {G/W}, {G/P}, {2/W}.
#: The left side may be generic, as on Spectral Procession's {2/W} — requiring a
#: color there reports zero pips for such a card and silently understates the
#: deck's color needs. Every listed color gets the pip, which overstates demand
#: slightly for hybrids; that is the safe direction to be wrong in.
_HYBRID_RE = re.compile(r"\{(?:([WUBRG])|\d+)/([WUBRGP])\}")

#: Mana values above this are grouped into one bucket.
CURVE_TOP_BUCKET = 7


@dataclass(frozen=True)
class CategoryCount:
    """How many cards fill a role, against the target band."""

    function: Function
    count: int
    target_min: int
    target_max: int

    @property
    def status(self) -> str:
        if self.count < self.target_min:
            return "low"
        if self.count > self.target_max:
            return "high"
        return "ok"

    @property
    def delta(self) -> int:
        """Cards to add (positive) or cut (negative) to reach the band."""
        if self.count < self.target_min:
            return self.target_min - self.count
        if self.count > self.target_max:
            return self.target_max - self.count
        return 0


@dataclass(frozen=True)
class PipReport:
    """Whether the mana base can support a color's heaviest demand."""

    color: str
    total_pips: int
    max_pips: int
    sources: int
    required: int

    @property
    def ok(self) -> bool:
        return self.sources >= self.required


@dataclass(frozen=True)
class AuditReport:
    """The full measurement of a deck."""

    total_cards: int
    land_count: int
    mdfc_land_count: int
    mana_sources: int
    average_mana_value: float
    curve: tuple[tuple[int, int], ...]
    categories: tuple[CategoryCount, ...]
    pips: tuple[PipReport, ...]

    @property
    def curve_status(self) -> str:
        low, high = targets.AVERAGE_MV_BAND
        if self.average_mana_value < low:
            return "low"
        if self.average_mana_value > high:
            return "high"
        return "ok"


def audit(deck: ResolvedDeck, tags: dict[str, frozenset[Function]] | None = None) -> AuditReport:
    """Measure the deck. Pass `tags` to reuse an existing classification."""
    tags = tags if tags is not None else classify_deck(deck)

    land_count = sum(qty for qty, c in deck.cards if c.is_land)
    mdfc_land_count = sum(qty for qty, c in deck.cards if c.is_mdfc_land)

    counts: dict[Function, int] = {}
    for qty, card in deck.cards:
        for function in tags.get(card.name, frozenset()):
            counts[function] = counts.get(function, 0) + qty

    ramp_count = counts.get(F.RAMP, 0)
    mana_sources = land_count + ramp_count + mdfc_land_count

    categories = tuple(
        CategoryCount(
            function=function,
            count=counts.get(function, 0),
            target_min=band[0],
            target_max=band[1],
        )
        for function, band in targets.CATEGORY_TARGETS.items()
    )

    return AuditReport(
        total_cards=deck.total_cards,
        land_count=land_count,
        mdfc_land_count=mdfc_land_count,
        mana_sources=mana_sources,
        average_mana_value=_average_mana_value(deck),
        curve=_curve(deck),
        categories=categories,
        pips=_pips(deck, tags),
    )


def _nonlands(deck: ResolvedDeck):
    for qty, card in deck.cards:
        if not card.is_land:
            yield qty, card


def _average_mana_value(deck: ResolvedDeck) -> float:
    total = 0.0
    count = 0
    for qty, card in _nonlands(deck):
        total += card.mana_value * qty
        count += qty
    return round(total / count, 2) if count else 0.0


def _curve(deck: ResolvedDeck) -> tuple[tuple[int, int], ...]:
    buckets: dict[int, int] = {}
    for qty, card in _nonlands(deck):
        bucket = min(int(card.mana_value), CURVE_TOP_BUCKET)
        buckets[bucket] = buckets.get(bucket, 0) + qty
    return tuple(sorted(buckets.items()))


def _count_pips(mana_cost: str) -> dict[str, int]:
    """Colored pip demand for one card."""
    pips: dict[str, int] = {}
    for color in _PIP_RE.findall(mana_cost):
        pips[color] = pips.get(color, 0) + 1
    for left, right in _HYBRID_RE.findall(mana_cost):
        for color in (left, right):
            # `left` is empty when the symbol is a monocolored hybrid like {2/W}.
            if color and color in "WUBRG":
                pips[color] = pips.get(color, 0) + 1
    return pips


def _pips(deck: ResolvedDeck, tags: dict[str, frozenset[Function]]) -> tuple[PipReport, ...]:
    totals: dict[str, int] = {}
    maxima: dict[str, int] = {}

    for qty, card in _nonlands(deck):
        for color, count in _count_pips(card.mana_cost).items():
            totals[color] = totals.get(color, 0) + count * qty
            maxima[color] = max(maxima.get(color, 0), count)

    reports = []
    for color in sorted(totals):
        sources = _sources_for(deck, tags, color)
        demand = min(maxima[color], max(targets.PIP_SOURCE_MINIMUMS))
        reports.append(
            PipReport(
                color=color,
                total_pips=totals[color],
                max_pips=maxima[color],
                sources=sources,
                required=targets.PIP_SOURCE_MINIMUMS[demand],
            )
        )
    return tuple(reports)


def _sources_for(deck: ResolvedDeck, tags: dict[str, frozenset[Function]], color: str) -> int:
    """Cards that can produce `color`: lands, MDFC land backs, and ramp."""
    total = 0
    for qty, card in deck.cards:
        if color not in card.produced_mana:
            continue
        is_ramp = F.RAMP in tags.get(card.name, frozenset())
        if card.is_land or card.is_mdfc_land or is_ramp:
            total += qty
    return total
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_audit.py -v`
Expected: PASS, 21 tests (16 named + 5 parametrized hybrid cases).

- [ ] **Step 6: Commit**

```bash
git add mtgpt/targets.py mtgpt/audit.py tests/test_audit.py
git commit -m "feat: audit ratios, curve, and colored sources

targets.py is the single source of truth for every threshold; the
reference markdown explains them and points here for values. Pip analysis
sizes each color against its most demanding card using Karsten-derived
minimums.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Bracket compliance

Layer 1 checks the four bracket constraints that are decidable from card data alone: Game Changer count, mass land denial, extra-turn density, and tutor density. **Two-card infinite combo detection is deliberately out of scope** — it requires Commander Spellbook, which arrives in Layer 2. The report names that gap explicitly in `deferred_checks` rather than implying a clean bill of health.

**Files:**
- Create: `mtgpt/brackets.py`
- Test: `tests/test_brackets.py`

**Interfaces:**
- Consumes: `Function`, `ResolvedDeck`, `Violation`, `Severity` from Task 1; `classify_deck` from Task 5.
- Produces: `BracketRule`, `BracketReport`, `RULES: dict[int, BracketRule]`, `check(deck, tags=None, target=3) -> BracketReport`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brackets.py
import pytest

from mtgpt.brackets import RULES, check
from mtgpt.models import Card, Function, ResolvedDeck, Severity

F = Function


def card(name, type_line="Creature — Bear", oracle_text="", game_changer=False):
    return Card(
        name=name,
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        mana_cost="{1}{G}",
        color_identity=frozenset("G"),
        colors=frozenset("G"),
        legal_commander="legal",
        produced_mana=frozenset(),
        layout="normal",
        is_game_changer=game_changer,
        usd=None,
        keywords=(),
    )


def deck_of(cards):
    return ResolvedDeck(commanders=(), cards=tuple((1, c) for c in cards))


def codes(report):
    return [f.code for f in report.findings]


def test_rules_cover_brackets_one_through_five():
    assert set(RULES) == {1, 2, 3, 4, 5}


def test_bracket_four_and_five_are_unrestricted():
    assert RULES[4].game_changers_max is None
    assert RULES[5].game_changers_max is None


def test_clean_deck_is_compliant_at_bracket_two():
    report = check(deck_of([card("Bear")]), target=2)
    assert report.compliant is True
    assert report.findings == ()


def test_game_changer_in_bracket_two_is_an_error():
    report = check(deck_of([card("Rhystic Study", game_changer=True)]), target=2)
    assert "game_changers" in codes(report)
    assert report.findings[0].severity is Severity.ERROR
    assert report.compliant is False


def test_bracket_three_allows_up_to_three_game_changers():
    cards = [card(f"GC {i}", game_changer=True) for i in range(3)]
    report = check(deck_of(cards), target=3)
    assert "game_changers" not in codes(report)


def test_bracket_three_flags_a_fourth_game_changer():
    cards = [card(f"GC {i}", game_changer=True) for i in range(4)]
    report = check(deck_of(cards), target=3)
    assert "game_changers" in codes(report)


def test_bracket_four_permits_many_game_changers():
    cards = [card(f"GC {i}", game_changer=True) for i in range(12)]
    report = check(deck_of(cards), target=4)
    assert report.compliant is True


def test_report_lists_game_changer_names():
    report = check(deck_of([card("Rhystic Study", game_changer=True)]), target=2)
    assert report.game_changers == ("Rhystic Study",)


def test_mass_land_denial_flagged_below_bracket_four():
    armageddon = card("Armageddon", "Sorcery", "Destroy all lands.")
    report = check(deck_of([armageddon]), target=3)
    assert "mass_land_denial" in codes(report)
    assert report.mass_land_denial == ("Armageddon",)


def test_mass_land_denial_allowed_at_bracket_four():
    armageddon = card("Armageddon", "Sorcery", "Destroy all lands.")
    report = check(deck_of([armageddon]), target=4)
    assert "mass_land_denial" not in codes(report)


def test_extra_turn_density_warns_below_bracket_four():
    turns = [card(f"Time Warp {i}", "Sorcery", "Target player takes an extra turn after this one.")
             for i in range(3)]
    report = check(deck_of(turns), target=3)
    assert "extra_turns" in codes(report)
    assert len(report.extra_turns) == 3


def test_single_extra_turn_spell_is_fine():
    warp = card("Time Warp", "Sorcery", "Target player takes an extra turn after this one.")
    report = check(deck_of([warp]), target=2)
    assert "extra_turns" not in codes(report)


def test_tutor_density_warns_at_bracket_two():
    tutors = [card(f"Tutor {i}", "Sorcery",
                   "Search your library for a card, put that card into your hand, then shuffle.")
              for i in range(5)]
    report = check(deck_of(tutors), target=2)
    assert "tutor_density" in codes(report)
    assert report.tutor_count == 5
    severity = next(f.severity for f in report.findings if f.code == "tutor_density")
    assert severity is Severity.WARNING


def test_tutor_density_not_flagged_at_bracket_three():
    tutors = [card(f"Tutor {i}", "Sorcery",
                   "Search your library for a card, put that card into your hand, then shuffle.")
              for i in range(5)]
    report = check(deck_of(tutors), target=3)
    assert "tutor_density" not in codes(report)


def test_combo_detection_is_declared_deferred():
    report = check(deck_of([card("Bear")]), target=2)
    assert any("combo" in note.lower() for note in report.deferred_checks)


def test_invalid_bracket_raises():
    with pytest.raises(ValueError):
        check(deck_of([card("Bear")]), target=9)


def test_report_carries_target_name():
    assert check(deck_of([card("Bear")]), target=1).target_name == "Exhibition"
    assert check(deck_of([card("Bear")]), target=5).target_name == "cEDH"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_brackets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtgpt.brackets'`

- [ ] **Step 3: Write `mtgpt/brackets.py`**

```python
# mtgpt/brackets.py
"""Check a deck against the WotC Commander bracket it is aiming at.

Layer 1 decides the four constraints that card data alone can settle: Game
Changer count, mass land denial, extra-turn density, and tutor density.

Two-card infinite combo detection needs Commander Spellbook and arrives in
Layer 2. Until then every report states that gap in `deferred_checks`, because
a bracket verdict that silently skips a rule is worse than no verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

from .classify import classify_deck
from .models import Function, ResolvedDeck, Severity, Violation

F = Function

#: Extra-turn spells tolerated below bracket 4 before the density is flagged.
EXTRA_TURN_WARN_AT = 3

#: Tutors tolerated at brackets 1-2, where the guidance asks for sparse tutoring.
TUTOR_WARN_AT = 4


@dataclass(frozen=True)
class BracketRule:
    """One bracket's constraints. `None` means unrestricted."""

    number: int
    name: str
    game_changers_max: int | None
    allow_mass_land_denial: bool
    watch_extra_turns: bool
    tutor_guidance: str


RULES: dict[int, BracketRule] = {
    1: BracketRule(1, "Exhibition", 0, False, True, "minimal"),
    2: BracketRule(2, "Core", 0, False, True, "sparse"),
    3: BracketRule(3, "Upgraded", 3, False, True, "unrestricted"),
    4: BracketRule(4, "Optimized", None, True, False, "unrestricted"),
    5: BracketRule(5, "cEDH", None, True, False, "unrestricted"),
}

#: Checks Layer 1 cannot perform, reported so the gap stays visible.
DEFERRED_CHECKS = (
    "Two-card infinite combo detection requires Commander Spellbook (Layer 2).",
    "Chained extra turns are approximated by counting extra-turn spells, not by "
    "detecting repeatability.",
)


@dataclass(frozen=True)
class BracketReport:
    """Verdict for one target bracket."""

    target: int
    target_name: str
    findings: tuple[Violation, ...]
    game_changers: tuple[str, ...]
    tutor_count: int
    mass_land_denial: tuple[str, ...]
    extra_turns: tuple[str, ...]
    deferred_checks: tuple[str, ...] = DEFERRED_CHECKS

    @property
    def compliant(self) -> bool:
        """True when nothing rises to an error. Warnings do not block."""
        return not any(f.severity is Severity.ERROR for f in self.findings)


def check(
    deck: ResolvedDeck,
    tags: dict[str, frozenset[Function]] | None = None,
    target: int = 3,
) -> BracketReport:
    """Compare the deck against `target` bracket's constraints."""
    if target not in RULES:
        raise ValueError(f"Bracket must be 1-5, got {target}.")
    rule = RULES[target]
    tags = tags if tags is not None else classify_deck(deck)

    game_changers = tuple(
        card.name for _, card in deck.cards if card.is_game_changer
    ) + tuple(c.name for c in deck.commanders if c.is_game_changer)

    mld = tuple(
        card.name
        for _, card in deck.cards
        if F.MASS_LAND_DENIAL in tags.get(card.name, frozenset())
    )
    extra_turns = tuple(
        card.name
        for _, card in deck.cards
        if F.EXTRA_TURNS in tags.get(card.name, frozenset())
    )
    tutor_count = sum(
        qty for qty, card in deck.cards if F.TUTOR in tags.get(card.name, frozenset())
    )

    findings: list[Violation] = []

    if rule.game_changers_max is not None and len(game_changers) > rule.game_changers_max:
        findings.append(
            Violation(
                severity=Severity.ERROR,
                code="game_changers",
                message=(
                    f"{len(game_changers)} Game Changers, but bracket {rule.number} "
                    f"({rule.name}) allows at most {rule.game_changers_max}: "
                    f"{', '.join(game_changers)}."
                ),
            )
        )

    if mld and not rule.allow_mass_land_denial:
        findings.append(
            Violation(
                severity=Severity.ERROR,
                code="mass_land_denial",
                message=(
                    f"Bracket {rule.number} ({rule.name}) excludes mass land denial: "
                    f"{', '.join(mld)}."
                ),
            )
        )

    if rule.watch_extra_turns and len(extra_turns) >= EXTRA_TURN_WARN_AT:
        findings.append(
            Violation(
                severity=Severity.WARNING,
                code="extra_turns",
                message=(
                    f"{len(extra_turns)} extra-turn spells. Bracket {rule.number} "
                    f"({rule.name}) asks that extra turns not be chained: "
                    f"{', '.join(extra_turns)}."
                ),
            )
        )

    if rule.tutor_guidance in {"minimal", "sparse"} and tutor_count >= TUTOR_WARN_AT:
        findings.append(
            Violation(
                severity=Severity.WARNING,
                code="tutor_density",
                message=(
                    f"{tutor_count} tutors, while bracket {rule.number} ({rule.name}) "
                    f"expects {rule.tutor_guidance} tutoring. Land fetches are not "
                    "counted here."
                ),
            )
        )

    return BracketReport(
        target=rule.number,
        target_name=rule.name,
        findings=tuple(sorted(findings)),
        game_changers=game_changers,
        tutor_count=tutor_count,
        mass_land_denial=mld,
        extra_turns=extra_turns,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_brackets.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 5: Commit**

```bash
git add mtgpt/brackets.py tests/test_brackets.py
git commit -m "feat: check deck against Commander bracket constraints

Game Changer count, mass land denial, extra-turn and tutor density across
brackets 1-5. Combo detection needs Spellbook, so every report names that
gap in deferred_checks rather than implying a clean verdict.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Agent-callable operation surface

**This task was rewritten at the user's direction.** The original specified one `audit` command
that ran the whole pipeline and printed a report. That is a monolith: it forces a single order
and granularity, so an agent cannot look up one card, vet three candidate replacements, or
re-check only the bracket after a swap without re-running everything. Deck tuning is a loop —
measure, hypothesize, check a candidate, re-measure — and the interface has to support it.

Two layers:

- `mtgpt/api.py` — the stable facade. One function per operation, each taking the smallest input
  it needs and returning a plain JSON-able `dict`. **All serialization lives here**, so the CLI
  and any future MCP adapter stay thin.
- `mtgpt/cli.py` — argparse dispatch to the facade. JSON by default in a fixed envelope;
  `--text` renders the human report.

Every operation is stateless and independently callable. Errors are data, not prose: an
unresolved card name returns `{"ok": false, "error": {"type": "UnresolvedCards", "names": [...]}}`
so an agent can act on it programmatically.

This task also adds a general `search()` to `mtgpt/scryfall.py`, which until now only searched
internally for Game Changers. Finding candidate cards is what makes the toolkit useful for
tuning rather than only grading.

**Files:**
- Create: `mtgpt/api.py`
- Create: `mtgpt/cli.py`
- Modify: `mtgpt/scryfall.py` — add `ScryfallClient.search(query, *, limit=25)` only
- Create: `tests/fixtures/sample_deck.txt`
- Create: `tests/fixtures/search_results.json`
- Create: `tests/fixtures/collection_sample_deck.json`
- Test: `tests/test_api.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces, in `api.py`, each returning a JSON-able `dict`:
  - `lookup_card(name, *, client=None)`
  - `search_cards(query, *, limit=25, client=None)`
  - `classify_cards(names, *, client=None)`
  - `read_deck(text)`
  - `validate_deck(text, *, client=None)`
  - `audit_deck(text, *, client=None)`
  - `bracket_check(text, *, target=3, client=None)`
  - `full_report(text, *, target=3, client=None)`
  - `render_report(report) -> str`
- Produces, in `cli.py`: `build_parser()`, `main(argv=None, client=None) -> int`, subcommands
  `card`, `search`, `classify`, `read`, `validate`, `audit`, `bracket`, `report`.

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/sample_deck.txt`:

```text
Commander
1 Atraxa, Praetors' Voice (C16) 28

Deck
1 Sol Ring (C21) 263
1 Cultivate (M21) 177
1 Swords to Plowshares (STA) 51
1 Wrath of God (TSR) 37
36 Forest (UNF) 235

Maybeboard
1 Mana Crypt (EMA) 225
```

`tests/fixtures/search_results.json`:

```json
{
  "object": "list",
  "total_cards": 2,
  "has_more": false,
  "data": [
    {
      "object": "card",
      "name": "Cultivate",
      "cmc": 3.0,
      "type_line": "Sorcery",
      "oracle_text": "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.",
      "mana_cost": "{2}{G}",
      "color_identity": ["G"],
      "colors": ["G"],
      "layout": "normal",
      "keywords": [],
      "legalities": {"commander": "legal"},
      "prices": {"usd": "0.25"}
    },
    {
      "object": "card",
      "name": "Kodama's Reach",
      "cmc": 3.0,
      "type_line": "Sorcery — Arcane",
      "oracle_text": "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.",
      "mana_cost": "{2}{G}",
      "color_identity": ["G"],
      "colors": ["G"],
      "layout": "normal",
      "keywords": [],
      "legalities": {"commander": "legal"},
      "prices": {"usd": "0.40"}
    }
  ]
}
```

`tests/fixtures/collection_sample_deck.json` must resolve exactly the six distinct cards in
`sample_deck.txt`. Capture it from the live API once, then commit the file:

```bash
curl -sS -A 'mtgpt/0.1' -H 'Content-Type: application/json' \
  -d '{"identifiers":[{"name":"Atraxa, Praetors Voice"},{"name":"Sol Ring"},{"name":"Cultivate"},{"name":"Swords to Plowshares"},{"name":"Wrath of God"},{"name":"Forest"}]}' \
  https://api.scryfall.com/cards/collection -o tests/fixtures/collection_sample_deck.json
python3 -c "
import json; d=json.load(open('tests/fixtures/collection_sample_deck.json'))
assert not d.get('not_found'), d.get('not_found')
for c in d['data']:
    print(c['name'], '|', c['type_line'], '|', c.get('produced_mana'))
"
```

Confirm `Forest` reads `Basic Land — Forest` with `produced_mana` `["G"]`, and that `Cultivate`
carries its real land-search oracle text — the tests depend on it classifying as ramp, not tutor.

- [ ] **Step 2: Add `search()` to `mtgpt/scryfall.py`**

Add `import urllib.parse` to the imports, then append this method to `ScryfallClient` beside
`game_changers()`. Route it through the existing `self._request(...)` seam so the throttle still
applies. Change nothing else in the file.

```python
    def search(self, query: str, *, limit: int = 25) -> tuple[dict, ...]:
        """Run a Scryfall search and return up to `limit` card payloads.

        This is how an agent finds candidate cards. Results are capped because
        the caller is choosing among options, not enumerating a set.
        """
        url = (
            f"{API}/cards/search?q={urllib.parse.quote(query)}"
            "&unique=cards&order=edhrec"
        )
        found: list[dict] = []
        while url and len(found) < limit:
            try:
                body = self._request(url)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise SourceUnavailable("Scryfall search", str(exc)) from exc
            found.extend(body.get("data") or ())
            url = body.get("next_page") if body.get("has_more") else None
        return tuple(found[:limit])
```

Scryfall answers a zero-match search with HTTP 404, which `_http_transport` raises as
`URLError`, so an empty search surfaces as `SourceUnavailable`. That is acceptable here — note
it in your report rather than special-casing it.

- [ ] **Step 3: Write `tests/test_api.py`**

```python
# tests/test_api.py
import json
import pathlib

import pytest

from mtgpt import api
from mtgpt.errors import DeckStructureError, UnresolvedCards
from mtgpt.scryfall import ScryfallClient

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def deck_text():
    return (FIXTURES / "sample_deck.txt").read_text()


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload=None):
        self.calls.append((url, payload))
        if not self.responses:
            raise AssertionError(f"unexpected extra request to {url}")
        return self.responses.pop(0)


NO_GAME_CHANGERS = {"data": [], "has_more": False}


def client_for(*responses):
    return ScryfallClient(transport=FakeTransport(*responses), sleep=lambda _: None)


def deck_client():
    return client_for(load("collection_sample_deck.json"), NO_GAME_CHANGERS)


def test_lookup_card_returns_card_data_and_tags():
    result = api.lookup_card("Sol Ring", client=deck_client())
    assert result["name"] == "Sol Ring"
    assert result["mana_value"] == 1.0
    assert result["legal_commander"] == "legal"
    assert "ramp" in result["functions"]
    assert result["is_land"] is False


def test_lookup_card_raises_for_an_invented_name():
    client = client_for({"data": [], "not_found": [{"name": "Fake Card"}]})
    with pytest.raises(UnresolvedCards):
        api.lookup_card("Fake Card", client=client)


def test_search_cards_returns_candidates_with_tags():
    client = client_for(load("search_results.json"), NO_GAME_CHANGERS)
    result = api.search_cards("o:'search your library for' t:sorcery c:g", client=client)
    assert result["count"] == 2
    assert [c["name"] for c in result["cards"]] == ["Cultivate", "Kodama's Reach"]
    # Candidates arrive pre-tagged so the agent can confirm they fill the gap.
    assert "ramp" in result["cards"][0]["functions"]
    assert "tutor" not in result["cards"][0]["functions"]


def test_search_cards_respects_limit():
    client = client_for(load("search_results.json"), NO_GAME_CHANGERS)
    assert api.search_cards("c:g", limit=1, client=client)["count"] == 1


def test_classify_cards_maps_names_to_tags():
    result = api.classify_cards(["Sol Ring", "Cultivate"], client=deck_client())
    assert "ramp" in result["Sol Ring"]
    # The land fetch must be ramp, not a tutor — this drives the bracket verdict.
    assert "ramp" in result["Cultivate"]
    assert "tutor" not in result["Cultivate"]


def test_read_deck_needs_no_network():
    result = api.read_deck(deck_text())
    assert result["commanders"] == [{"qty": 1, "name": "Atraxa, Praetors' Voice"}]
    assert result["total_cards"] == 41
    assert {"qty": 36, "name": "Forest"} in result["entries"]
    # Maybeboard is excluded.
    assert all(e["name"] != "Mana Crypt" for e in result["entries"])


def test_read_deck_raises_on_garbage():
    with pytest.raises(DeckStructureError):
        api.read_deck("this is not a decklist")


def test_validate_deck_returns_only_legality():
    result = api.validate_deck(deck_text(), client=deck_client())
    assert result["legal"] is False  # the fixture deck is 41 cards, not 100
    assert "deck_size" in [v["code"] for v in result["violations"]]
    assert "audit" not in result


def test_audit_deck_returns_only_measurements():
    result = api.audit_deck(deck_text(), client=deck_client())
    assert result["land_count"] == 36
    assert "categories" in result and "pips" in result
    assert "violations" not in result


def test_bracket_check_returns_only_the_verdict():
    result = api.bracket_check(deck_text(), target=3, client=deck_client())
    assert result["target"] == 3
    assert result["target_name"] == "Upgraded"
    assert "deferred_checks" in result
    assert "categories" not in result


def test_full_report_composes_every_section():
    result = api.full_report(deck_text(), target=3, client=deck_client())
    for key in ("commanders", "violations", "audit", "bracket", "tags"):
        assert key in result


def test_render_report_surfaces_per_card_tags():
    """Classification is heuristic; the design's mitigation is visible tags."""
    rendered = api.render_report(api.full_report(deck_text(), target=3, client=deck_client()))
    assert "CARD TAGS" in rendered
    assert "Cultivate" in rendered
    assert "ramp" in rendered.lower()


def test_operations_are_independent():
    """bracket_check must work without audit_deck ever being called."""
    assert api.bracket_check(deck_text(), client=deck_client())["target"] == 3
```

- [ ] **Step 4: Run it to verify it fails**

Run: `python3 -m pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtgpt.api'`

- [ ] **Step 5: Write `mtgpt/api.py`**

```python
# mtgpt/api.py
"""The agent-facing facade.

One function per operation, each returning a plain JSON-able dict. An agent
composes these: look up a card, audit a deck, search for candidates, classify
them, re-audit. Nothing here holds state, and no function depends on another
having been called first.

Serialization lives in this module alone, so `cli.py` — and any future MCP
adapter — stays a thin dispatch layer.
"""

from __future__ import annotations

from .audit import AuditReport, audit
from .brackets import BracketReport, check
from .classify import classify, classify_deck
from .deckparse import parse
from .models import Card, Function, ResolvedDeck, Violation
from .scryfall import ScryfallClient, card_from_json, resolve
from .validate import validate


def _client(client: ScryfallClient | None) -> ScryfallClient:
    return client or ScryfallClient()


def _card_dict(card: Card, functions: frozenset[Function]) -> dict:
    return {
        "name": card.name,
        "mana_value": card.mana_value,
        "mana_cost": card.mana_cost,
        "type_line": card.type_line,
        "oracle_text": card.oracle_text,
        "color_identity": sorted(card.color_identity),
        "legal_commander": card.legal_commander,
        "is_land": card.is_land,
        "is_mdfc_land": card.is_mdfc_land,
        "is_game_changer": card.is_game_changer,
        "usd": card.usd,
        "functions": sorted(f.value for f in functions),
    }


def _violations(violations: tuple[Violation, ...]) -> list[dict]:
    return [
        {"severity": v.severity.name, "code": v.code, "message": v.message}
        for v in violations
    ]


def _audit_dict(report: AuditReport) -> dict:
    return {
        "total_cards": report.total_cards,
        "land_count": report.land_count,
        "mdfc_land_count": report.mdfc_land_count,
        "mana_sources": report.mana_sources,
        "average_mana_value": report.average_mana_value,
        "curve_status": report.curve_status,
        "curve": [list(pair) for pair in report.curve],
        "categories": [
            {
                "function": c.function.value,
                "count": c.count,
                "target": [c.target_min, c.target_max],
                "status": c.status,
                "delta": c.delta,
            }
            for c in report.categories
        ],
        "pips": [
            {
                "color": p.color,
                "total_pips": p.total_pips,
                "max_pips": p.max_pips,
                "sources": p.sources,
                "required": p.required,
                "ok": p.ok,
            }
            for p in report.pips
        ],
    }


def _bracket_dict(report: BracketReport) -> dict:
    return {
        "target": report.target,
        "target_name": report.target_name,
        "compliant": report.compliant,
        "game_changers": list(report.game_changers),
        "tutor_count": report.tutor_count,
        "mass_land_denial": list(report.mass_land_denial),
        "extra_turns": list(report.extra_turns),
        "findings": _violations(report.findings),
        "deferred_checks": list(report.deferred_checks),
    }


def _tags_dict(tags: dict[str, frozenset[Function]]) -> dict[str, list[str]]:
    return {name: sorted(f.value for f in fns) for name, fns in tags.items()}


# --- Card operations -------------------------------------------------------


def lookup_card(name: str, *, client: ScryfallClient | None = None) -> dict:
    """Resolve one card against Scryfall and tag it.

    Use this before naming any card in a recommendation. Raises
    UnresolvedCards if the name is not real.
    """
    cards, _ = _client(client).collection([name])
    card = card_from_json(cards[0])
    return _card_dict(card, classify(card))


def search_cards(
    query: str, *, limit: int = 25, client: ScryfallClient | None = None
) -> dict:
    """Find candidate cards with a Scryfall query, pre-tagged by function."""
    payloads = _client(client).search(query, limit=limit)
    cards = [card_from_json(p) for p in payloads]
    return {
        "query": query,
        "count": len(cards),
        "cards": [_card_dict(c, classify(c)) for c in cards],
    }


def classify_cards(
    names: list[str], *, client: ScryfallClient | None = None
) -> dict[str, list[str]]:
    """Tag several cards by function, keyed by name."""
    cards, _ = _client(client).collection(names)
    out: dict[str, list[str]] = {}
    for payload in cards:
        card = card_from_json(payload)
        out[card.name] = sorted(f.value for f in classify(card))
    return out


# --- Deck operations -------------------------------------------------------


def read_deck(text: str) -> dict:
    """Parse decklist text. No network, no verification — structure only."""
    deck = parse(text)
    return {
        "commanders": [{"qty": e.qty, "name": e.name} for e in deck.commanders],
        "entries": [{"qty": e.qty, "name": e.name} for e in deck.entries],
        "total_cards": deck.total_with_commanders,
    }


def _resolved(text: str, client: ScryfallClient | None) -> ResolvedDeck:
    return resolve(parse(text), client=_client(client))


def validate_deck(text: str, *, client: ScryfallClient | None = None) -> dict:
    """Legality only: size, singleton, commander, color identity, ban list."""
    deck = _resolved(text, client)
    violations = validate(deck)
    return {
        "commanders": [c.name for c in deck.commanders],
        "legal": not violations,
        "violations": _violations(violations),
    }


def audit_deck(text: str, *, client: ScryfallClient | None = None) -> dict:
    """Measurements only: ratios, curve, colored sources."""
    deck = _resolved(text, client)
    return _audit_dict(audit(deck, tags=classify_deck(deck)))


def bracket_check(
    text: str, *, target: int = 3, client: ScryfallClient | None = None
) -> dict:
    """Bracket verdict only."""
    deck = _resolved(text, client)
    return _bracket_dict(check(deck, tags=classify_deck(deck), target=target))


def full_report(
    text: str, *, target: int = 3, client: ScryfallClient | None = None
) -> dict:
    """Everything composed, for when the agent wants one complete picture."""
    deck = _resolved(text, client)
    tags = classify_deck(deck)
    return {
        "commanders": [c.name for c in deck.commanders],
        "color_identity": sorted(deck.command_zone_identity),
        "total_cards": deck.total_with_commanders,
        "violations": _violations(validate(deck)),
        "audit": _audit_dict(audit(deck, tags=tags)),
        "bracket": _bracket_dict(check(deck, tags=tags, target=target)),
        "tags": _tags_dict(tags),
    }


_STATUS_MARK = {"ok": "ok", "low": "LOW", "high": "HIGH"}


def render_report(report: dict) -> str:
    """Render a `full_report` dict as human-readable text."""
    from . import targets

    lines: list[str] = []
    commanders = ", ".join(report["commanders"]) or "(none declared)"
    identity = "".join(report["color_identity"]) or "C"
    audit_data = report["audit"]
    bracket = report["bracket"]

    def band(name):
        low, high = getattr(targets, name)
        return f"{low}-{high}"

    lines.append("=" * 68)
    lines.append(f"mtgpt — {commanders}")
    lines.append(f"Color identity: {{{identity}}}   Cards: {report['total_cards']}/100")
    lines.append("=" * 68)

    lines.append("")
    lines.append("LEGALITY")
    if report["violations"]:
        for v in report["violations"]:
            lines.append(f"  [{v['severity']}] {v['message']}")
    else:
        lines.append("  No violations found.")

    lines.append("")
    lines.append("COMPOSITION")
    lines.append(f"  {'Lands':<16}{audit_data['land_count']:>4}   target {band('LAND')}")
    if audit_data["mdfc_land_count"]:
        lines.append(
            f"  {'MDFC backs':<16}{audit_data['mdfc_land_count']:>4}   "
            "flex sources, not lands"
        )
    for c in audit_data["categories"]:
        if c["function"] == "land":
            continue
        label = c["function"].replace("_", " ").title()
        band_text = f"{c['target'][0]}-{c['target'][1]}"
        mark = _STATUS_MARK[c["status"]]
        note = ""
        if c["delta"]:
            note = f"  ({'add' if c['delta'] > 0 else 'cut'} {abs(c['delta'])})"
        lines.append(f"  {label:<16}{c['count']:>4}   target {band_text:<7} {mark}{note}")
    lines.append(
        f"  {'Mana sources':<16}{audit_data['mana_sources']:>4}   "
        f"target {band('MANA_SOURCES')}"
    )

    lines.append("")
    lines.append("CURVE")
    lines.append(
        f"  Average mana value: {audit_data['average_mana_value']} "
        f"({_STATUS_MARK[audit_data['curve_status']]}, target {band('AVERAGE_MV_BAND')})"
    )
    for bucket, count in audit_data["curve"]:
        label = "7+" if bucket >= 7 else str(bucket)
        lines.append(f"  {label:>3} | {'#' * min(count, 40)} {count}")

    lines.append("")
    lines.append("COLORED SOURCES")
    if audit_data["pips"]:
        for p in audit_data["pips"]:
            verdict = "ok" if p["ok"] else "SHORT"
            lines.append(
                f"  {{{p['color']}}}  sources {p['sources']:>3}   "
                f"need {p['required']:>3} for a {p['max_pips']}-pip card   {verdict}"
            )
    else:
        lines.append("  No colored pips in the deck.")

    lines.append("")
    lines.append(f"BRACKET {bracket['target']} — {bracket['target_name']}")
    lines.append(f"  Verdict: {'compliant' if bracket['compliant'] else 'NOT compliant'}")
    gc = bracket["game_changers"]
    lines.append(f"  Game Changers: {len(gc)}" + (f" ({', '.join(gc)})" if gc else ""))
    lines.append(f"  Tutors: {bracket['tutor_count']} (land fetches excluded)")
    for f in bracket["findings"]:
        lines.append(f"  [{f['severity']}] {f['message']}")
    lines.append("  Not checked at this layer:")
    for note in bracket["deferred_checks"]:
        lines.append(f"    - {note}")

    # Classification is heuristic, so the tags are shown for correction.
    lines.append("")
    lines.append("CARD TAGS")
    for name in sorted(report["tags"]):
        lines.append(f"  {name:<34} {', '.join(report['tags'][name])}")

    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 6: Run the facade tests**

Run: `python3 -m pytest tests/test_api.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 7: Write `tests/test_cli.py`**

```python
# tests/test_cli.py
import io
import json
import pathlib

from mtgpt import cli

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_every_subcommand_is_registered():
    parser = cli.build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    assert actions, "expected a subcommand dest named 'command'"
    assert set(actions[0].choices) == {
        "card", "search", "classify", "read", "validate", "audit", "bracket", "report",
    }


def test_card_emits_a_success_envelope(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "lookup_card",
                        lambda name, client=None: {"name": name, "functions": ["ramp"]})
    assert cli.main(["card", "Sol Ring"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "card"
    assert payload["data"]["name"] == "Sol Ring"


def test_unresolved_card_emits_a_machine_readable_error(monkeypatch, capsys):
    from mtgpt.errors import UnresolvedCards

    def boom(name, client=None):
        raise UnresolvedCards(["Blatantly Fake Card"])

    monkeypatch.setattr(cli.api, "lookup_card", boom)
    code = cli.main(["card", "Blatantly Fake Card"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["error"]["type"] == "UnresolvedCards"
    # The offending names are data, so the agent can act on them.
    assert payload["error"]["names"] == ["Blatantly Fake Card"]


def test_search_passes_limit_through(monkeypatch):
    seen = {}

    def fake(query, limit=25, client=None):
        seen["query"], seen["limit"] = query, limit
        return {"query": query, "count": 0, "cards": []}

    monkeypatch.setattr(cli.api, "search_cards", fake)
    assert cli.main(["search", "c:g t:sorcery", "--limit", "5"]) == 0
    assert seen == {"query": "c:g t:sorcery", "limit": 5}


def test_classify_accepts_several_names(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "classify_cards",
                        lambda names, client=None: {n: ["ramp"] for n in names})
    assert cli.main(["classify", "Sol Ring", "Arcane Signet"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["data"]) == {"Sol Ring", "Arcane Signet"}


def test_read_needs_no_network(capsys):
    assert cli.main(["read", "--file", str(FIXTURES / "sample_deck.txt")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["total_cards"] == 41


def test_deck_subcommands_accept_stdin(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "audit_deck", lambda text, client=None: {"land_count": 36})
    monkeypatch.setattr("sys.stdin", io.StringIO("1 Sol Ring\n36 Forest\n"))
    assert cli.main(["audit", "--stdin"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["land_count"] == 36


def test_bracket_passes_target_through(monkeypatch):
    seen = {}

    def fake(text, target=3, client=None):
        seen["target"] = target
        return {"target": target, "target_name": "Core"}

    monkeypatch.setattr(cli.api, "bracket_check", fake)
    cli.main(["bracket", "--file", str(FIXTURES / "sample_deck.txt"), "--target", "2"])
    assert seen["target"] == 2


def test_report_text_mode_renders_instead_of_json(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "full_report",
                        lambda text, target=3, client=None: {"stub": True})
    monkeypatch.setattr(cli.api, "render_report", lambda report: "RENDERED REPORT")
    assert cli.main(["report", "--file", str(FIXTURES / "sample_deck.txt"), "--text"]) == 0
    out = capsys.readouterr().out
    assert "RENDERED REPORT" in out
    assert "{" not in out


def test_missing_deck_input_is_a_user_error(capsys):
    assert cli.main(["audit"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "Export" in payload["error"]["message"]


def test_unparseable_deck_is_reported_as_data(capsys, tmp_path):
    path = tmp_path / "deck.txt"
    path.write_text("not a decklist\n")
    assert cli.main(["read", "--file", str(path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == "DeckStructureError"
```

- [ ] **Step 8: Write `mtgpt/cli.py`**

```python
# mtgpt/cli.py
"""Command-line surface over the agent-facing facade.

Each subcommand is one operation, independently callable:

    python3 -m mtgpt.cli card "Sol Ring"
    python3 -m mtgpt.cli search "o:'add one mana of any color' t:creature c:g" --limit 10
    python3 -m mtgpt.cli classify "Cultivate" "Demonic Tutor"
    python3 -m mtgpt.cli read     --file deck.txt
    python3 -m mtgpt.cli validate --file deck.txt
    python3 -m mtgpt.cli audit    --file deck.txt
    python3 -m mtgpt.cli bracket  --file deck.txt --target 3
    python3 -m mtgpt.cli report   --file deck.txt --bracket 3 [--text]

Output is JSON in a fixed envelope so results feed the next decision:

    {"ok": true,  "command": "audit", "data": {...}}
    {"ok": false, "command": "audit", "error": {"type": "...", "message": "...", ...}}

Errors are data, never prose only: UnresolvedCards carries the offending names.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import api
from .errors import DeckStructureError, MtgptError, SourceUnavailable, UnresolvedCards
from .scryfall import ScryfallClient

EXIT_OK = 0
EXIT_USER_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mtgpt",
        description="Agent-callable operations for Magic: The Gathering Commander decks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    card = sub.add_parser("card", help="Resolve and tag one card")
    card.add_argument("name")

    search = sub.add_parser("search", help="Find candidate cards by Scryfall query")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=25)

    classify_cmd = sub.add_parser("classify", help="Tag several cards by function")
    classify_cmd.add_argument("names", nargs="+")

    for name, help_text in (
        ("read", "Parse a decklist without verifying it"),
        ("validate", "Check legality only"),
        ("audit", "Measure ratios, curve, and colored sources"),
        ("bracket", "Check bracket compliance only"),
        ("report", "Everything composed"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        source = cmd.add_mutually_exclusive_group()
        source.add_argument("--file", help="Path to a decklist text file")
        source.add_argument("--stdin", action="store_true", help="Read the decklist from stdin")
        if name == "bracket":
            cmd.add_argument("--target", type=int, default=3, choices=[1, 2, 3, 4, 5])
        if name == "report":
            cmd.add_argument("--bracket", type=int, default=3, choices=[1, 2, 3, 4, 5])
            cmd.add_argument("--text", action="store_true", help="Human-readable output")

    return parser


def _emit(command: str, data, *, ok: bool = True) -> None:
    key = "data" if ok else "error"
    print(json.dumps({"ok": ok, "command": command, key: data}, indent=2))


def _error_payload(exc: Exception) -> dict:
    payload = {"type": type(exc).__name__, "message": str(exc)}
    if isinstance(exc, UnresolvedCards):
        payload["names"] = list(exc.names)
    if isinstance(exc, SourceUnavailable):
        payload["source"] = exc.source
    return payload


def _read_deck_text(args, command: str) -> str | None:
    """Return the decklist text, or None after emitting a user error."""
    if args.file:
        try:
            return open(args.file, encoding="utf-8").read()
        except OSError as exc:
            _emit(command, {"type": "OSError", "message": str(exc)}, ok=False)
            return None
    if args.stdin:
        return sys.stdin.read()
    _emit(
        command,
        {
            "type": "MissingInput",
            "message": (
                "No decklist given. Pass --file <path> or --stdin. "
                "In Moxfield use Export, then paste or save the text."
            ),
        },
        ok=False,
    )
    return None


def main(argv: list[str] | None = None, client: ScryfallClient | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command

    try:
        if command == "card":
            _emit(command, api.lookup_card(args.name, client=client))
        elif command == "search":
            _emit(command, api.search_cards(args.query, limit=args.limit, client=client))
        elif command == "classify":
            _emit(command, api.classify_cards(args.names, client=client))
        else:
            text = _read_deck_text(args, command)
            if text is None:
                return EXIT_USER_ERROR
            if command == "read":
                _emit(command, api.read_deck(text))
            elif command == "validate":
                _emit(command, api.validate_deck(text, client=client))
            elif command == "audit":
                _emit(command, api.audit_deck(text, client=client))
            elif command == "bracket":
                _emit(command, api.bracket_check(text, target=args.target, client=client))
            elif command == "report":
                report = api.full_report(text, target=args.bracket, client=client)
                if args.text:
                    print(api.render_report(report))
                else:
                    _emit(command, report)
    except MtgydError if False else (UnresolvedCards, DeckStructureError, SourceUnavailable) as exc:
        _emit(command, _error_payload(exc), ok=False)
        return EXIT_USER_ERROR
    except MtgptError as exc:
        _emit(command, _error_payload(exc), ok=False)
        return EXIT_USER_ERROR

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
```

**Note on the except clause above:** the first `except` line as written is deliberately
nonsense to catch a copy-paste. Write it as:

```python
    except (UnresolvedCards, DeckStructureError, SourceUnavailable) as exc:
```

- [ ] **Step 9: Run the CLI tests and the whole suite**

Run: `python3 -m pytest tests/test_cli.py -v`
Expected: PASS, 11 tests.

Run: `python3 -m pytest -q`
Expected: PASS, everything.

- [ ] **Step 10: Verify against the live API**

Run each and record the actual output in your report:

```bash
python3 -m mtgpt.cli card "Sol Ring"
python3 -m mtgpt.cli search "o:'search your library for' t:sorcery c:g" --limit 5
python3 -m mtgpt.cli classify "Nature's Lore" "Three Visits" "Demonic Tutor" "Scapeshift"
python3 -m mtgpt.cli validate --file tests/fixtures/sample_deck.txt
python3 -m mtgpt.cli audit    --file tests/fixtures/sample_deck.txt
python3 -m mtgpt.cli bracket  --file tests/fixtures/sample_deck.txt --target 2
python3 -m mtgpt.cli report   --file tests/fixtures/sample_deck.txt --bracket 3 --text
```

**The `classify` call is the important one.** `Nature's Lore`, `Three Visits`, and `Scapeshift`
must all report `["ramp"]` with no `tutor`; `Demonic Tutor` must report `["tutor"]`. That
distinction drives the bracket verdict and has been broken twice before. Confirm the `report
--text` output contains a `CARD TAGS` section.

- [ ] **Step 11: Commit**

```bash
git add mtgpt/api.py mtgpt/cli.py mtgpt/scryfall.py tests/test_api.py tests/test_cli.py tests/fixtures/
git commit -m "feat: expose mtgpt as agent-callable operations

Each pipeline stage becomes an independently callable operation returning
JSON in a fixed envelope, plus card lookup and Scryfall candidate search.
Errors are data: an unresolved name carries the offending names so an agent
can act on them rather than parse prose.

api.py owns serialization so cli.py stays thin and a future MCP adapter
would be an adapter rather than a rewrite. The text report surfaces per-card
function tags, which is the design's stated mitigation for classification
being heuristic.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Skill, references, and README

Makes the repo an installable plugin Claude can actually load. `SKILL.md` stays short and routes to references on demand; the hygiene reference explains the numbers and points at `targets.py` for values.

**Files:**
- Create: `skills/mtgpt/SKILL.md`
- Create: `skills/mtgpt/references/deckbuilding-hygiene.md`
- Create: `skills/mtgpt/references/brackets.md`
- Create: `README.md`

**Interfaces:**
- Consumes: the CLI from Task 8.
- Produces: no code. The skill is the user-facing contract.

- [ ] **Step 1: Write `skills/mtgpt/SKILL.md`**

```markdown
---
name: mtgpt
description: Use when building, auditing, or tuning a Magic: The Gathering Commander/EDH deck - resolves every card against Scryfall, checks legality and color identity, measures ratios/curve/colored sources against deckbuilding targets, and reports bracket compliance. Triggers on "EDH", "Commander deck", "decklist", "tune my deck", "deck audit", "is this legal", "what bracket".
---

# mtgpt

Build and tune Commander decks on verified data.

## The rule that matters

**Never name a card from memory as a recommendation.** Every card you propose
must come back from Scryfall first. Card names, oracle text, legality, and
color identity are all things a model misremembers confidently, and a
decklist with a fake card in it is worse than no decklist.

Run the tooling. Report what it says.

## Audit or tune an existing deck

1. **Get the list.** Ask the user to open their deck in Moxfield, click
   **Export**, and paste the text. Moxfield blocks scripted reads with a
   Cloudflare challenge, so pasting is the supported path.
2. **Run the audit:**

   ```bash
   python3 -m mtgpt.cli audit --file <path> --bracket <1-5>
   ```

   For a pasted list, write it to a file first, or pipe it with `--stdin`.
   Add `--json` when you need the structured data to reason over.
3. **Read the report before commenting.** It gives legality violations,
   category counts against targets, the curve, colored-source adequacy, and
   the bracket verdict.
4. **Interpret, don't restate.** The user can read numbers. Say what the
   numbers mean for how the deck plays: which target the deck misses that
   will actually cost it games, and what to do about it.

Default to bracket 3 if the user has not said what they are aiming at, and
say that you assumed it.

## When the report shows a problem

- **Unresolved card names** — the run stops and names them. These are typos
  or cards that do not exist. Ask the user; never substitute a guess.
- **A category is LOW** — name the specific cards worth adding, and verify
  each one exists and is in color identity before you say it.
- **The curve is high** — look for expensive cards that do not advance the
  commander's plan, rather than cutting the biggest mana values by reflex.
- **Colored sources are SHORT** — this is a mana base problem, not a spell
  problem. Fix the lands before touching the spell list.
- **Bracket not compliant** — report exactly which rule broke and the cards
  responsible.

Every report ends with what was **not** checked. Two-card infinite combos
need Commander Spellbook, which this layer does not have. Do not tell the
user their deck is bracket-legal without naming that gap.

## Reference material

Load these only when the question calls for them:

- `references/deckbuilding-hygiene.md` — what each target means and why.
  Read this before arguing with a number the audit reports.
- `references/brackets.md` — the bracket 1-5 rules in full.

## Not yet available

Building a deck from a commander, EDHREC synergy recommendations, combo
detection, and goldfish simulation are Layers 2 and 3. If the user asks for
one of those, say it is not built yet rather than improvising it by hand.
```

- [ ] **Step 2: Write `skills/mtgpt/references/deckbuilding-hygiene.md`**

```markdown
# Deckbuilding hygiene

The values the audit checks live in `mtgpt/targets.py`. This file explains
what each one is for. When a number here and a number there disagree,
`targets.py` is correct and this file needs updating.

These are starting points for a typical midrange Commander deck, not laws. A
deck that misses a target for a reason it can articulate is fine. A deck that
misses one because nobody counted is the problem this tool solves.

## Lands: 36-38

The most common failure in a homebrew deck is too few lands. 36 is the floor
for a deck with an average mana value near 3; go to 38 when the curve is
higher or ramp is thin. You may go below 36 only when the deck is genuinely
cheap and carries 12 or more ramp pieces.

MDFC land backs are reported separately and counted as flex sources, not as
lands. A card whose front you want to cast is not a land on the turn you
need it to be one.

## Ramp: 10-12

Mana rocks, mana dorks, and land-fetch spells. Land fetches count here, not
as tutors. Decks with an average mana value above 3.2 want the upper end.

## Card draw: 8-12

Commander games go long, and card advantage is what converts a good board
into a win. Repeatable draw engines are worth more than one-shot refills, so
a deck at 8 with three engines is healthier than one at 12 with none.

## Spot removal: 5-8

Targeted answers. Below 5, the table's best threat resolves and stays.
Instant-speed and unconditional answers are worth more than sorcery-speed
conditional ones.

## Sweepers: 2-3

Board wipes. Go to the low end when the deck itself goes wide, since a
symmetrical wipe hurts a token deck more than it helps.

## Protection: 3-5

Ways to keep the commander or the board alive. Weight this up when the deck
cannot function without its commander on the battlefield.

## Total mana sources: 46-50

Lands plus ramp plus MDFC land backs. This is the number that actually
predicts whether the deck functions, and it is the one to check first when a
deck feels clunky.

## Average mana value: 2.8-3.2

Computed across the nonland cards. Above the band, the deck needs more lands
and more ramp than the defaults. Below it, the deck can afford to cut a land.

## Colored sources

A card with three pips of one color needs far more sources of that color than
a card with one. The thresholds in `targets.py` are adapted from Frank
Karsten's source-count methodology for 100-card singleton decks:

| Pips of a color in one card | Sources of that color wanted |
|---|---|
| 1 | 14 |
| 2 | 20 |
| 3 | 26 |

The audit sizes each color against the most demanding single card in that
color, which is the honest test: a deck that cannot reliably cast its own
triple-pip card has a mana base problem regardless of how the totals look.

Hybrid pips count toward both colors, which overstates demand slightly. That
is the safe direction to be wrong in.
```

- [ ] **Step 3: Write `skills/mtgpt/references/brackets.md`**

```markdown
# Commander brackets

WotC's bracket system describes what a deck is trying to do, so players can
match expectations before shuffling. The machine-readable rules are in
`mtgpt/brackets.py`.

| Bracket | Name | Game Changers | Mass land denial | Extra turns | Tutors |
|---|---|---|---|---|---|
| 1 | Exhibition | none | no | not chained | minimal |
| 2 | Core | none | no | not chained | sparse |
| 3 | Upgraded | up to 3 | no | not chained | unrestricted |
| 4 | Optimized | unrestricted | yes | yes | unrestricted |
| 5 | cEDH | unrestricted | yes | yes | unrestricted |

Brackets 1-3 also exclude two-card infinite combos; bracket 3 tolerates them
only as late-game finishers.

## The Game Changers list

Maintained by WotC and revised over time, so mtgpt fetches it from Scryfall's
`is:gamechanger` rather than hardcoding it. A deck that goes over its
bracket's allowance is reported as an error with the offending cards named.

## What mtgpt checks, and what it does not

Checked from card data alone:

- Game Changer count
- Mass land denial
- Extra-turn spell density (a count, which approximates "not chained")
- Tutor density, with land fetches excluded

Not checked in Layer 1:

- **Two-card infinite combos.** This needs Commander Spellbook, which arrives
  in Layer 2. Every bracket report names this gap. A deck that passes Layer 1
  at bracket 2 may still be running an infinite combo.
- **Repeatable extra turns.** Counting extra-turn spells is a proxy for
  chaining, not a detection of it.

Say so when reporting a verdict. A bracket claim that hides a skipped rule is
worse than no claim.
```

- [ ] **Step 4: Write `README.md`**

The README must include this subsection under `## Status`, recording Task 10's verified outcome:

```markdown
### Moxfield URL fetching

Not available. Moxfield serves scripted requests a Cloudflare challenge, so automated
fetching needs a real browser — and Chromium cannot launch in this environment without
system libraries that require root to install (`libnspr4`, `libnss3`, `libnssutil3`,
`libasound2`). Verified 2026-09-30.

Use Moxfield's **Export** button and pass the text to `--file` or `--stdin`. Every deck
operation accepts both.
```


```markdown
# mtgpt

Build and tune Magic: The Gathering Commander decks with Claude, on data that
has been verified rather than remembered.

The problem it solves: asked to tune a deck, a language model will invent
cards, misquote oracle text, break color identity, and reason about mana
bases by feel. mtgpt splits the work — scripts establish facts, the model
makes judgments. Every card in a report has been resolved against Scryfall,
so a fake card cannot reach you.

## Install

```bash
/plugin marketplace add DavidRimel/mtgpt
```

Then ask Claude to audit a deck.

## Use directly

```bash
# Audit a decklist against bracket 3
python3 -m mtgpt.cli audit --file mydeck.txt --bracket 3

# Pipe from the clipboard, get JSON
pbpaste | python3 -m mtgpt.cli audit --stdin --json
```

Export your list from Moxfield with the **Export** button and paste it into a
file. Moxfield serves scripted requests a Cloudflare challenge, so automated
fetching is not available.

## What it reports

- **Legality** — 100 cards, singleton, commander legality, color identity, ban list
- **Composition** — lands, ramp, draw, removal, sweepers, protection against target bands
- **Curve** — average mana value and a mana value histogram
- **Colored sources** — whether the mana base supports each color's heaviest card
- **Bracket** — compliance with brackets 1-5, and what it could not check

## Status

Layer 1 of three. Working now: ingest, Scryfall resolution, validation,
function classification, the ratio and curve audit, and bracket checks.

Not built yet: EDHREC synergy recommendations and combo detection (Layer 2),
goldfish simulation (Layer 3), and building a deck from scratch given a
commander.

## Data sources

| Source | Use |
|---|---|
| [Scryfall](https://scryfall.com) | Card data, legality, color identity, Game Changers |
| [EDHREC](https://edhrec.com) | Synergy and inclusion rates (Layer 2) |
| [Commander Spellbook](https://commanderspellbook.com) | Combo detection (Layer 2) |

## Development

```bash
python3 -m pytest
```

Tests never touch the network; they run against fixtures in `tests/fixtures/`.

## Design docs

- [Design](docs/superpowers/specs/2026-09-30-mtgpt-design.md)
- [Layer 1 plan](docs/superpowers/plans/2026-09-30-mtgpt-layer1.md)
```

- [ ] **Step 5: Verify the skill loads**

Run:

```bash
python3 -c "
import pathlib, re
p = pathlib.Path('skills/mtgpt/SKILL.md').read_text()
assert p.startswith('---'), 'missing frontmatter'
fm = p.split('---')[1]
assert re.search(r'^name:\s*mtgpt\s*$', fm, re.M), 'name must be mtgpt'
desc = re.search(r'^description:\s*(.+)$', fm, re.M)
assert desc and len(desc.group(1)) > 40, 'description too thin to trigger on'
print('SKILL.md frontmatter ok')
"
```

Expected: `SKILL.md frontmatter ok`

- [ ] **Step 6: Run the full suite one more time**

Run: `python3 -m pytest`
Expected: PASS, 143 tests.

- [ ] **Step 7: Commit**

```bash
git add skills/ README.md
git commit -m "docs: add mtgpt skill, references, and README

SKILL.md leads with the rule that matters: never name a card from memory.
The hygiene reference explains the targets and defers to targets.py for
values, so prose and code cannot drift.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Moxfield browser fetch — RESOLVED AS DEFERRED (verified 2026-09-30)

> **Outcome: deferred, not built.** Step 1's verification was run and failed. Evidence:
>
> ```
> $ python3 -m pip install --user playwright          # OK, no sudo needed
> $ python3 -m playwright install chromium            # OK, 114 MB downloaded
> $ chrome-headless-shell --headless --dump-dom ...
> error while loading shared libraries: libnspr4.so: cannot open shared object file
>   libnspr4.so, libnss3.so, libnssutil3.so, libasound.so.2 => not found
> $ python3 -m playwright install-deps --dry-run chromium
> Missing system dependencies (37)
> ```
>
> Those libraries install via `apt-get` as root, and `sudo` requires a password in this
> environment. Per Step 2, no fetcher was written: an intermittently-failing browser fetch is
> worse than an absent one that is documented. The export/paste path from Task 2 is the
> supported route and works. Task 9's README must carry the note below.
>
> The Playwright package and Chromium binary remain installed under `~/.local` and
> `~/.cache/ms-playwright`. They are harmless; if the system libraries are ever installed
> (`sudo apt-get install libnss3 libnspr4 libasound2t64`), re-run Step 1 and the remaining
> steps become viable unchanged.

### Original task (retained for reference)

The user asked for the browser to perform the Moxfield export on their behalf, so the pipeline accepts a URL and not just pasted text. Moxfield serves scripted requests a Cloudflare challenge (403, confirmed against `api2.moxfield.com` on 2026-09-30 including with a browser User-Agent), so a real browser is the only automated route.

**This task is verify-first and may legitimately end in deferral.** `pip install playwright` needs no elevation, but Chromium's system libraries normally do, and `sudo` requires a password in this environment. Step 1 settles it before any fetcher code is written. Do not build the fetcher against an unproven browser, and do not leave a half-working fetcher in place — the paste path from Task 2 already works, and a fetcher that fails intermittently is worse than one that is absent and documented.

**Files:**
- Create: `mtgpt/moxfield.py` (only if Step 1 succeeds)
- Modify: `mtgpt/cli.py` — add `--url`
- Modify: `pyproject.toml` — add the `browser` optional-dependency group
- Test: `tests/test_moxfield.py`

**Interfaces:**
- Consumes: `parse` from Task 2, `DeckStructureError`/`SourceUnavailable` from Task 1.
- Produces: `MOXFIELD_URL_RE`, `public_id(url: str) -> str`, `fetch_decklist(url: str, *, page_factory=None) -> str`.

- [ ] **Step 1: Determine whether Chromium can run here**

```bash
python3 -m pip install --user playwright 2>&1 | tail -2
python3 -m playwright install chromium 2>&1 | tail -5
python3 - <<'EOF'
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page()
    page.goto("https://example.com", timeout=30000)
    print("TITLE:", page.title())
    browser.close()
EOF
```

Expected on success: `TITLE: Example Domain`.

If the launch fails with missing shared libraries (`libnss3`, `libatk-1.0`, `libgbm`, or similar), **stop and take the deferral branch in Step 2.** Do not attempt `playwright install-deps`; it requires sudo, which is not available without a password here.

- [ ] **Step 2 (deferral branch only): Record the outcome and stop**

Only if Step 1 failed. Append to `README.md` under `## Status`:

```markdown
### Moxfield URL fetching

Not available in this environment. Moxfield serves scripted requests a
Cloudflare challenge, so automated fetching needs a real browser, and
Chromium cannot launch here without system libraries that require root to
install. Use the **Export** button in Moxfield and pass the text to
`--file` or `--stdin`.
```

Then commit and treat Layer 1 as complete:

```bash
git add README.md
git commit -m "docs: record that Moxfield URL fetching is unavailable

Chromium cannot launch without root-installed system libraries, so the
export/paste path is the supported route. Documented rather than left as a
fetcher that fails at runtime.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

Report the deferral to the user plainly, and skip the remaining steps.

- [ ] **Step 3: Write the failing test**

```python
# tests/test_moxfield.py
import pytest

from mtgpt.errors import SourceUnavailable
from mtgpt.moxfield import fetch_decklist, public_id


def test_public_id_from_canonical_url():
    assert public_id("https://www.moxfield.com/decks/AbC123_xyz") == "AbC123_xyz"


def test_public_id_tolerates_trailing_path_and_query():
    assert public_id("https://moxfield.com/decks/AbC123_xyz/primer?x=1") == "AbC123_xyz"


def test_public_id_rejects_non_moxfield_url():
    with pytest.raises(ValueError):
        public_id("https://archidekt.com/decks/12345")


def test_public_id_rejects_url_without_a_deck_id():
    with pytest.raises(ValueError):
        public_id("https://www.moxfield.com/users/someone")


class FakePage:
    """Stands in for a Playwright page."""

    def __init__(self, text, *, fail=False):
        self.text = text
        self.fail = fail
        self.visited = []

    def goto(self, url, **kwargs):
        self.visited.append(url)
        if self.fail:
            raise RuntimeError("net::ERR_FAILED")

    def inner_text(self, selector):
        return self.text

    def wait_for_selector(self, selector, **kwargs):
        return None


def test_fetch_decklist_returns_page_text():
    page = FakePage("1 Sol Ring (C21) 263\n36 Forest\n")
    text = fetch_decklist(
        "https://www.moxfield.com/decks/AbC123_xyz",
        page_factory=lambda: page,
    )
    assert "Sol Ring" in text
    assert page.visited and "AbC123_xyz" in page.visited[0]


def test_fetch_decklist_wraps_navigation_failure():
    page = FakePage("", fail=True)
    with pytest.raises(SourceUnavailable):
        fetch_decklist(
            "https://www.moxfield.com/decks/AbC123_xyz",
            page_factory=lambda: page,
        )


def test_fetch_decklist_rejects_empty_result():
    page = FakePage("   \n")
    with pytest.raises(SourceUnavailable):
        fetch_decklist(
            "https://www.moxfield.com/decks/AbC123_xyz",
            page_factory=lambda: page,
        )
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_moxfield.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtgpt.moxfield'`

- [ ] **Step 5: Write `mtgpt/moxfield.py`**

```python
# mtgpt/moxfield.py
"""Fetch a public Moxfield decklist through a real browser.

Moxfield answers scripted HTTP requests with a Cloudflare challenge, so the
only automated route is a browser that can satisfy it. Playwright is an
optional dependency: import errors surface as SourceUnavailable so the CLI can
tell the user to paste an export instead.

`page_factory` is injected in tests, which is what keeps this module's logic
testable without a browser.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from .errors import SourceUnavailable

#: Public deck URLs look like https://www.moxfield.com/decks/<publicId>
MOXFIELD_URL_RE = re.compile(
    r"^https?://(?:www\.)?moxfield\.com/decks/(?P<id>[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)

#: Moxfield's export view renders the plain decklist inside a <pre> block.
EXPORT_SELECTOR = "pre"

PAGE_TIMEOUT_MS = 45_000


def public_id(url: str) -> str:
    """Extract the public deck id from a Moxfield URL."""
    match = MOXFIELD_URL_RE.match(url.strip())
    if not match:
        raise ValueError(
            f"Not a Moxfield deck URL: {url!r}. "
            "Expected https://www.moxfield.com/decks/<id>."
        )
    return match.group("id")


def _default_page_factory():
    """Launch headless Chromium and return a page.

    Raises SourceUnavailable when Playwright or its browser is missing, so the
    caller can fall back to asking for a pasted export.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SourceUnavailable(
            "Moxfield fetch",
            "Playwright is not installed. Install it with "
            "'pip install playwright && python3 -m playwright install chromium', "
            "or paste a Moxfield export instead.",
        ) from exc

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:
        playwright.stop()
        raise SourceUnavailable("Moxfield fetch", f"Chromium failed to launch: {exc}") from exc
    return browser.new_page()


def fetch_decklist(url: str, *, page_factory: Callable[[], object] | None = None) -> str:
    """Return the plain-text decklist for a public Moxfield deck.

    Raises SourceUnavailable on any navigation or extraction failure. The
    caller is expected to suggest the export/paste path rather than retrying.
    """
    deck_id = public_id(url)
    page = (page_factory or _default_page_factory)()
    export_url = f"https://www.moxfield.com/decks/{deck_id}/export/text"

    try:
        page.goto(export_url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        page.wait_for_selector(EXPORT_SELECTOR, timeout=PAGE_TIMEOUT_MS)
        text = page.inner_text(EXPORT_SELECTOR)
    except SourceUnavailable:
        raise
    except Exception as exc:
        raise SourceUnavailable(
            "Moxfield fetch",
            f"{exc}. Moxfield may be challenging the browser; "
            "use the Export button and paste the text instead.",
        ) from exc

    if not text or not text.strip():
        raise SourceUnavailable(
            "Moxfield fetch",
            "The export view returned no text. The deck may be private; "
            "use the Export button and paste the text instead.",
        )
    return text
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python3 -m pytest tests/test_moxfield.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 7: Wire `--url` into the CLI**

In `mtgpt/cli.py`, add to the mutually exclusive group in `_build_parser`:

```python
    source.add_argument("--url", help="Public Moxfield deck URL (requires Playwright)")
```

In `main`, add a branch before the `--stdin` branch:

```python
    elif args.url:
        from .moxfield import fetch_decklist

        try:
            text = fetch_decklist(args.url)
        except (ValueError, SourceUnavailable) as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_USER_ERROR
```

Add a test to `tests/test_cli.py`:

```python
def test_main_url_failure_suggests_pasting(monkeypatch, capsys):
    from mtgpt.errors import SourceUnavailable

    def boom(url):
        raise SourceUnavailable("Moxfield fetch", "Chromium failed to launch: nope")

    monkeypatch.setattr("mtgpt.moxfield.fetch_decklist", boom)
    code = cli.main(["audit", "--url", "https://www.moxfield.com/decks/AbC123_xyz"])
    assert code == 2
    assert "Chromium failed to launch" in capsys.readouterr().err
```

- [ ] **Step 8: Add the optional dependency group**

In `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0"]
browser = ["playwright>=1.44"]
```

- [ ] **Step 9: Verify against a real public deck**

Ask the user for a public Moxfield deck URL of theirs, then run:

```bash
python3 -m mtgpt.cli audit --url "<their deck url>" --bracket 3
```

Expected: the same report shape as the `--file` path. If Cloudflare challenges the headless browser, report that honestly and fall back to the deferral branch in Step 2 rather than adding retry loops or stealth workarounds.

- [ ] **Step 10: Update the README and commit**

Replace the Moxfield paragraph under `## Use directly` in `README.md`:

```markdown
Pass a public deck URL with `--url` (requires the `browser` extra:
`pip install -e '.[browser]' && python3 -m playwright install chromium`).
Moxfield challenges plain scripted requests, so a real browser does the
export. If that is blocked, use Moxfield's **Export** button and pass the
text to `--file` or `--stdin`.
```

```bash
git add mtgpt/moxfield.py mtgpt/cli.py tests/test_moxfield.py tests/test_cli.py pyproject.toml README.md
git commit -m "feat: fetch public Moxfield decklists via headless Chromium

Moxfield answers scripted requests with a Cloudflare challenge, so a real
browser performs the export. Playwright is an optional dependency and every
failure surfaces as SourceUnavailable pointing at the paste path.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---


## Task 11: EDHREC operations — synergy and community staples

The first external-resource operation. Scryfall says what a card *is*; EDHREC says what players
actually do with a commander. That is where candidate ideas come from, and without it the
toolkit can only grade a deck, never improve it.

`json.edhrec.com` was probed live on 2026-09-30 and returns, per commander: `tag_counts`
(themes with deck counts), `bracket_counts` (decks per bracket), and `container.json_dict.cardlists`
— named lists including "High Synergy Cards", "Top Cards", "Game Changers", and per-type lists.
Each card carries `synergy` (float), `num_decks`, and `potential_decks`, so a recommendation can
cite real inclusion evidence instead of asserting. Bracket-variant pages exist at
`/commanders/<slug>/upgraded.json`, `/budget.json`, and `/cedh.json`.

It is an unofficial endpoint with no compatibility contract, so every failure must surface as
`SourceUnavailable` and the toolkit must stay useful without it.

**Files:**
- Create: `mtgpt/edhrec.py`
- Modify: `mtgpt/api.py` — add `commander_synergy`, `commander_themes`
- Modify: `mtgpt/cli.py` — add `synergy`, `themes` subcommands
- Create: `tests/fixtures/edhrec_commander.json`
- Test: `tests/test_edhrec.py`

**Interfaces:**
- Consumes: `SourceUnavailable` from Task 1; the `Transport` seam pattern from Task 3.
- Produces:
  - `edhrec.commander_slug(name) -> str`
  - `edhrec.EdhrecClient(transport=None, sleep=time.sleep)` with `commander(name, variant=None) -> dict`
  - `edhrec.synergy_cards(payload, *, limit=40) -> tuple[dict, ...]` — name, synergy, inclusion rate, list header
  - `edhrec.themes(payload) -> tuple[dict, ...]` — theme slug, label, deck count
  - `edhrec.bracket_distribution(payload) -> dict[str, int]`
  - `api.commander_synergy(name, *, variant=None, limit=40, client=None, edhrec_client=None) -> dict`
  - `api.commander_themes(name, *, client=None) -> dict`

- [ ] **Step 1: Capture the fixture**

Run this once to record a real response, then commit the file. Tests read it offline.

```bash
curl -sS -A 'mtgpt/0.1' \
  'https://json.edhrec.com/pages/commanders/atraxa-praetors-voice.json' \
  -o tests/fixtures/edhrec_commander.json
python3 -c "
import json; d=json.load(open('tests/fixtures/edhrec_commander.json'))
print('cardlists:', [c['header'] for c in d['container']['json_dict']['cardlists']])
print('tag_counts sample:', d['tag_counts'][:3])
print('bracket_counts:', d['bracket_counts'])
"
```

Expected: cardlists including "High Synergy Cards" and "Game Changers"; `tag_counts` entries with
`count`/`slug`/`value`; `bracket_counts` keyed "1" through "5".

If the fixture exceeds 2 MB, trim it to the keys the code reads (`tag_counts`, `bracket_counts`,
`container.json_dict.cardlists`) and say so in your report.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_edhrec.py
import json
import pathlib

import pytest

from mtgpt import edhrec
from mtgpt.errors import SourceUnavailable

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def payload():
    return json.loads((FIXTURES / "edhrec_commander.json").read_text())


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if not self.responses:
            raise AssertionError(f"unexpected request to {url}")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize(
    "name,slug",
    [
        ("Atraxa, Praetors' Voice", "atraxa-praetors-voice"),
        ("Kozilek, Butcher of Truth", "kozilek-butcher-of-truth"),
        ("Ur-Dragon", "ur-dragon"),
        ("Jhoira, Weatherlight Captain", "jhoira-weatherlight-captain"),
        # Accented characters are stripped, not escaped.
        ("Nazgûl", "nazgul"),
        ("Minsc & Data, Timeless Heroes", "minsc-and-data-timeless-heroes"),
    ],
)
def test_commander_slug(name, slug):
    assert edhrec.commander_slug(name) == slug


def test_commander_fetches_the_base_page():
    transport = FakeTransport(payload())
    client = edhrec.EdhrecClient(transport=transport, sleep=lambda _: None)
    client.commander("Atraxa, Praetors' Voice")
    assert transport.calls == [
        "https://json.edhrec.com/pages/commanders/atraxa-praetors-voice.json"
    ]


def test_commander_fetches_a_bracket_variant():
    transport = FakeTransport(payload())
    client = edhrec.EdhrecClient(transport=transport, sleep=lambda _: None)
    client.commander("Atraxa, Praetors' Voice", variant="upgraded")
    assert transport.calls[0].endswith("/atraxa-praetors-voice/upgraded.json")


def test_commander_rejects_an_unknown_variant():
    client = edhrec.EdhrecClient(transport=FakeTransport(), sleep=lambda _: None)
    with pytest.raises(ValueError):
        client.commander("Atraxa, Praetors' Voice", variant="nonsense")


def test_commander_wraps_transport_failure():
    client = edhrec.EdhrecClient(
        transport=FakeTransport(OSError("connection reset")), sleep=lambda _: None
    )
    with pytest.raises(SourceUnavailable) as excinfo:
        client.commander("Atraxa, Praetors' Voice")
    assert "EDHREC" in str(excinfo.value)


def test_synergy_cards_carry_evidence():
    cards = edhrec.synergy_cards(payload(), limit=10)
    assert cards, "expected synergy cards from the fixture"
    first = cards[0]
    assert set(first) >= {"name", "synergy", "num_decks", "potential_decks", "list", "inclusion_rate"}
    assert isinstance(first["name"], str)
    # inclusion_rate is derived so a recommendation can cite it directly.
    assert 0.0 <= first["inclusion_rate"] <= 1.0


def test_synergy_cards_are_sorted_by_synergy_descending():
    cards = edhrec.synergy_cards(payload(), limit=20)
    synergies = [c["synergy"] for c in cards]
    assert synergies == sorted(synergies, reverse=True)


def test_synergy_cards_respects_limit():
    assert len(edhrec.synergy_cards(payload(), limit=5)) == 5


def test_synergy_cards_deduplicates_across_lists():
    """A card appearing in several cardlists must be returned once."""
    cards = edhrec.synergy_cards(payload(), limit=200)
    names = [c["name"] for c in cards]
    assert len(names) == len(set(names))


def test_themes_are_sorted_by_deck_count():
    themes = edhrec.themes(payload())
    counts = [t["count"] for t in themes]
    assert counts == sorted(counts, reverse=True)
    assert set(themes[0]) >= {"slug", "label", "count"}


def test_bracket_distribution_keys_are_ints_one_to_five():
    dist = edhrec.bracket_distribution(payload())
    assert set(dist) <= {1, 2, 3, 4, 5}
    assert all(isinstance(v, int) for v in dist.values())


def test_missing_sections_degrade_to_empty_rather_than_raising():
    """EDHREC is unofficial; a shape change must not crash the toolkit."""
    assert edhrec.synergy_cards({}, limit=5) == ()
    assert edhrec.themes({}) == ()
    assert edhrec.bracket_distribution({}) == {}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest tests/test_edhrec.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtgpt.edhrec'`

- [ ] **Step 4: Write `mtgpt/edhrec.py`**

```python
# mtgpt/edhrec.py
"""EDHREC: what players actually build around a commander.

Scryfall establishes what a card is; EDHREC establishes what the community does
with it. Synergy scores and inclusion rates are what let a recommendation cite
evidence rather than assert taste.

`json.edhrec.com` is an unofficial endpoint with no compatibility contract, so
every failure raises SourceUnavailable and every missing section degrades to
empty. The toolkit must stay useful when EDHREC is down.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable

from .errors import SourceUnavailable

BASE = "https://json.edhrec.com/pages/commanders"

#: Bracket/budget variant pages EDHREC publishes per commander.
VARIANTS = frozenset({"budget", "expensive", "upgraded", "cedh"})

REQUEST_DELAY = 0.1
USER_AGENT = "mtgpt/0.1"

#: Cardlists worth mining for candidates, in priority order. Type-specific
#: lists are skipped: they repeat these and dilute the synergy signal.
CANDIDATE_LISTS = (
    "High Synergy Cards",
    "Top Cards",
    "New Cards",
    "Game Changers",
)

_NON_SLUG = re.compile(r"[^a-z0-9]+")

Transport = Callable[[str], dict]


def commander_slug(name: str) -> str:
    """Convert a commander name to its EDHREC URL slug.

    Accents are stripped rather than escaped, and "&" becomes "and", matching
    EDHREC's own slugs.
    """
    folded = unicodedata.normalize("NFKD", name)
    ascii_name = folded.encode("ascii", "ignore").decode("ascii")
    ascii_name = ascii_name.replace("&", " and ")
    return _NON_SLUG.sub("-", ascii_name.lower()).strip("-")


def _http_transport(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


class EdhrecClient:
    """Fetch EDHREC commander pages."""

    def __init__(self, transport: Transport | None = None, sleep=time.sleep):
        self._transport = transport or _http_transport
        self._sleep = sleep
        self._made_request = False

    def commander(self, name: str, *, variant: str | None = None) -> dict:
        """Fetch a commander page, optionally a bracket/budget variant."""
        if variant is not None and variant not in VARIANTS:
            raise ValueError(
                f"Unknown EDHREC variant {variant!r}. Expected one of "
                f"{', '.join(sorted(VARIANTS))}."
            )
        slug = commander_slug(name)
        suffix = f"/{variant}" if variant else ""
        url = f"{BASE}/{slug}{suffix}.json"

        if self._made_request:
            self._sleep(REQUEST_DELAY)
        self._made_request = True

        try:
            return self._transport(url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SourceUnavailable("EDHREC", f"{url}: {exc}") from exc


def _cardlists(payload: dict) -> list[dict]:
    container = payload.get("container") or {}
    json_dict = container.get("json_dict") or {}
    return json_dict.get("cardlists") or []


def synergy_cards(payload: dict, *, limit: int = 40) -> tuple[dict, ...]:
    """Candidate cards with their synergy score and inclusion evidence.

    Deduplicated by name across lists, sorted by synergy descending. A missing
    or reshaped payload yields an empty tuple rather than raising.
    """
    seen: dict[str, dict] = {}
    for cardlist in _cardlists(payload):
        header = cardlist.get("header") or ""
        if header not in CANDIDATE_LISTS:
            continue
        for view in cardlist.get("cardviews") or ():
            name = view.get("name")
            if not name or name in seen:
                continue
            num = view.get("num_decks") or 0
            potential = view.get("potential_decks") or 0
            seen[name] = {
                "name": name,
                "synergy": float(view.get("synergy") or 0.0),
                "num_decks": num,
                "potential_decks": potential,
                "inclusion_rate": round(num / potential, 4) if potential else 0.0,
                "list": header,
            }

    ranked = sorted(seen.values(), key=lambda c: c["synergy"], reverse=True)
    return tuple(ranked[:limit])


def themes(payload: dict) -> tuple[dict, ...]:
    """Archetypes this commander is built as, most common first."""
    out = [
        {
            "slug": tag.get("slug", ""),
            "label": tag.get("value", ""),
            "count": int(tag.get("count") or 0),
        }
        for tag in payload.get("tag_counts") or ()
    ]
    return tuple(sorted(out, key=lambda t: t["count"], reverse=True))


def bracket_distribution(payload: dict) -> dict[int, int]:
    """How many recorded decks sit in each bracket, keyed 1-5."""
    raw = payload.get("bracket_counts") or {}
    out: dict[int, int] = {}
    for key, value in raw.items():
        try:
            bracket = int(key)
        except (TypeError, ValueError):
            continue
        if 1 <= bracket <= 5:
            out[bracket] = int(value)
    return out
```

- [ ] **Step 5: Add the facade operations to `mtgpt/api.py`**

```python
def commander_synergy(
    name: str,
    *,
    variant: str | None = None,
    limit: int = 40,
    client: ScryfallClient | None = None,
    edhrec_client=None,
) -> dict:
    """Candidate cards for a commander, with synergy and inclusion evidence.

    Each candidate is resolved against Scryfall and tagged by function, so the
    agent can see what role it would fill before proposing it — and so a card
    EDHREC lists but Scryfall cannot resolve never reaches the user.
    """
    from .edhrec import EdhrecClient, synergy_cards

    source = edhrec_client or EdhrecClient()
    payload = source.commander(name, variant=variant)
    candidates = synergy_cards(payload, limit=limit)
    if not candidates:
        return {"commander": name, "variant": variant, "count": 0, "cards": []}

    verified, _ = _client(client).collection([c["name"] for c in candidates])
    by_name = {}
    for p in verified:
        card = card_from_json(p)
        by_name[card.name.casefold()] = card
        front, _, _ = card.name.partition("//")
        by_name.setdefault(front.strip().casefold(), card)

    out = []
    for candidate in candidates:
        card = by_name.get(candidate["name"].casefold())
        if card is None:
            continue
        entry = _card_dict(card, classify(card))
        entry["synergy"] = candidate["synergy"]
        entry["inclusion_rate"] = candidate["inclusion_rate"]
        entry["edhrec_list"] = candidate["list"]
        out.append(entry)

    return {"commander": name, "variant": variant, "count": len(out), "cards": out}


def commander_themes(name: str, *, edhrec_client=None) -> dict:
    """Archetypes this commander is usually built as, plus bracket spread."""
    from .edhrec import EdhrecClient, bracket_distribution, themes

    source = edhrec_client or EdhrecClient()
    payload = source.commander(name)
    return {
        "commander": name,
        "themes": [dict(t) for t in themes(payload)],
        "bracket_distribution": {str(k): v for k, v in bracket_distribution(payload).items()},
    }
```

- [ ] **Step 6: Add the subcommands to `mtgpt/cli.py`**

In `build_parser`:

```python
    synergy = sub.add_parser("synergy", help="EDHREC candidate cards for a commander")
    synergy.add_argument("commander")
    synergy.add_argument("--variant", choices=["budget", "expensive", "upgraded", "cedh"])
    synergy.add_argument("--limit", type=int, default=40)

    themes_cmd = sub.add_parser("themes", help="How a commander is usually built")
    themes_cmd.add_argument("commander")
```

In `main`, beside the other card-name commands:

```python
        elif command == "synergy":
            _emit(command, api.commander_synergy(
                args.commander, variant=args.variant, limit=args.limit, client=client))
        elif command == "themes":
            _emit(command, api.commander_themes(args.commander))
```

Add `"synergy"` and `"themes"` to the expected subcommand set in `test_every_subcommand_is_registered`.

- [ ] **Step 7: Add facade tests to `tests/test_api.py`**

```python
class FakeEdhrec:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def commander(self, name, *, variant=None):
        self.calls.append((name, variant))
        return self.payload


def test_commander_synergy_verifies_candidates_and_tags_them():
    edh = FakeEdhrec(json.loads((FIXTURES / "edhrec_commander.json").read_text()))
    client = client_for(load("collection_basic.json"), NO_GAME_CHANGERS)
    result = api.commander_synergy(
        "Atraxa, Praetors' Voice", limit=3, client=client, edhrec_client=edh
    )
    assert result["commander"] == "Atraxa, Praetors' Voice"
    for card in result["cards"]:
        # Evidence travels with the candidate.
        assert "synergy" in card and "inclusion_rate" in card
        # And it is a verified card with function tags.
        assert "functions" in card and card["legal_commander"]


def test_commander_synergy_drops_candidates_scryfall_cannot_verify():
    """A card EDHREC lists but Scryfall cannot resolve must not reach the user."""
    edh = FakeEdhrec(json.loads((FIXTURES / "edhrec_commander.json").read_text()))
    # Scryfall returns only Sol Ring, whatever EDHREC suggested.
    client = client_for(load("collection_basic.json"), NO_GAME_CHANGERS)
    result = api.commander_synergy(
        "Atraxa, Praetors' Voice", limit=40, client=client, edhrec_client=edh
    )
    names = {c["name"] for c in result["cards"]}
    assert names <= {"Sol Ring", "Atraxa, Praetors' Voice", "Dockside Extortionist"}


def test_commander_synergy_passes_the_variant_through():
    edh = FakeEdhrec(json.loads((FIXTURES / "edhrec_commander.json").read_text()))
    client = client_for(load("collection_basic.json"), NO_GAME_CHANGERS)
    api.commander_synergy(
        "Atraxa, Praetors' Voice", variant="upgraded", limit=1,
        client=client, edhrec_client=edh,
    )
    assert edh.calls == [("Atraxa, Praetors' Voice", "upgraded")]


def test_commander_themes_returns_themes_and_bracket_spread():
    edh = FakeEdhrec(json.loads((FIXTURES / "edhrec_commander.json").read_text()))
    result = api.commander_themes("Atraxa, Praetors' Voice", edhrec_client=edh)
    assert result["themes"] and "label" in result["themes"][0]
    assert result["bracket_distribution"]
```

- [ ] **Step 8: Run the tests**

Run: `python3 -m pytest tests/test_edhrec.py tests/test_api.py tests/test_cli.py -v`
Expected: PASS — 13 edhrec tests, 17 api tests, 12 cli tests.

Run: `python3 -m pytest -q`
Expected: PASS, all tests.

- [ ] **Step 9: Verify against the live endpoint**

```bash
python3 -m mtgpt.cli themes "Atraxa, Praetors' Voice"
python3 -m mtgpt.cli synergy "Atraxa, Praetors' Voice" --limit 5
python3 -m mtgpt.cli synergy "Atraxa, Praetors' Voice" --variant upgraded --limit 5
```

Expected: `themes` lists infect / +1+1 counters / planeswalkers with deck counts. `synergy`
returns five verified cards each carrying `synergy`, `inclusion_rate`, and `functions`. Record
the actual output in your report, and confirm the `upgraded` variant returns a different set
than the base page.

- [ ] **Step 10: Commit**

```bash
git add mtgpt/edhrec.py mtgpt/api.py mtgpt/cli.py tests/test_edhrec.py tests/test_api.py tests/test_cli.py tests/fixtures/edhrec_commander.json
git commit -m "feat: add EDHREC synergy and theme operations

Scryfall says what a card is; EDHREC says what players build around a
commander. Candidates carry synergy scores and inclusion rates so a
recommendation cites evidence instead of asserting taste, and every
candidate is Scryfall-verified and function-tagged before it reaches the
user — a card EDHREC lists but Scryfall cannot resolve is dropped.

EDHREC is unofficial, so failures raise SourceUnavailable and missing
sections degrade to empty rather than crashing the toolkit.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Commander Spellbook operations — combo detection

Closes the gap every bracket report currently names in `deferred_checks`. Brackets 1-3 exclude
two-card infinite combos, and until now mtgpt could only say it had not checked.

`backend.commanderspellbook.com/variants/` was probed live on 2026-09-30. A variant carries
`uses` (the cards), `produces` (the features, e.g. "Win the game"), `bracketTag`, `salt`,
`identity`, and `legalities` — so a detected combo can be judged against the deck's target
bracket directly.

**Files:**
- Create: `mtgpt/spellbook.py`
- Modify: `mtgpt/api.py` — add `deck_combos`, `card_combos`
- Modify: `mtgpt/cli.py` — add `combos`, `card-combos` subcommands
- Modify: `mtgpt/brackets.py` — accept detected combos and drop that deferred-check line
- Create: `tests/fixtures/spellbook_variants.json`
- Test: `tests/test_spellbook.py`

**Interfaces:**
- Consumes: `SourceUnavailable`, `ResolvedDeck`.
- Produces:
  - `spellbook.SpellbookClient(transport=None, sleep=time.sleep)` with `variants_for_card(name, limit=50)`
  - `spellbook.combos_in_deck(variants, deck_card_names) -> tuple[dict, ...]`
  - `api.deck_combos(text, *, client=None, spellbook_client=None) -> dict`
  - `api.card_combos(name, *, spellbook_client=None) -> dict`
  - `brackets.check(deck, tags=None, target=3, combos=None)` — `combos` is a tuple of combo dicts; when provided, two-card infinite combos are enforced per bracket and the corresponding `deferred_checks` entry is omitted.

- [ ] **Step 1: Capture the fixture**

```bash
curl -sS -A 'mtgpt/0.1' \
  "https://backend.commanderspellbook.com/variants/?q=card%3A%22Thassa's%20Oracle%22&limit=5" \
  -o tests/fixtures/spellbook_variants.json
python3 -c "
import json; d=json.load(open('tests/fixtures/spellbook_variants.json'))
r=d['results'][0]
print('uses:', [u['card']['name'] for u in r['uses']])
print('produces:', [p['feature']['name'] for p in r['produces']])
print('bracketTag:', r.get('bracketTag'), 'salt:', r.get('salt'))
"
```

Expected: a variant using Thassa's Oracle plus a partner card, producing "Win the game", with a
`bracketTag` string.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_spellbook.py
import json
import pathlib

import pytest

from mtgpt import spellbook
from mtgpt.errors import SourceUnavailable

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def variants_payload():
    return json.loads((FIXTURES / "spellbook_variants.json").read_text())


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_variants_for_card_quotes_the_name():
    transport = FakeTransport(variants_payload())
    client = spellbook.SpellbookClient(transport=transport, sleep=lambda _: None)
    client.variants_for_card("Thassa's Oracle")
    assert "Thassa" in transport.calls[0]
    assert "variants" in transport.calls[0]


def test_variants_for_card_wraps_transport_failure():
    client = spellbook.SpellbookClient(
        transport=FakeTransport(OSError("boom")), sleep=lambda _: None
    )
    with pytest.raises(SourceUnavailable) as excinfo:
        client.variants_for_card("Thassa's Oracle")
    assert "Commander Spellbook" in str(excinfo.value)


def test_parse_variant_extracts_cards_and_outcome():
    raw = variants_payload()["results"][0]
    combo = spellbook.parse_variant(raw)
    assert set(combo) >= {"id", "cards", "produces", "bracket_tag", "salt", "card_count"}
    assert combo["card_count"] == len(combo["cards"])
    assert all(isinstance(c, str) for c in combo["cards"])


def test_combos_in_deck_requires_every_card_present():
    raw = variants_payload()["results"][0]
    combo = spellbook.parse_variant(raw)
    cards = combo["cards"]
    # All pieces present -> detected.
    assert spellbook.combos_in_deck((raw,), set(cards))
    # One piece missing -> not detected.
    assert spellbook.combos_in_deck((raw,), set(cards[:-1])) == ()


def test_combos_in_deck_is_case_insensitive():
    raw = variants_payload()["results"][0]
    cards = {c.lower() for c in spellbook.parse_variant(raw)["cards"]}
    assert spellbook.combos_in_deck((raw,), cards)


def test_combos_in_deck_deduplicates_by_id():
    raw = variants_payload()["results"][0]
    cards = set(spellbook.parse_variant(raw)["cards"])
    assert len(spellbook.combos_in_deck((raw, raw), cards)) == 1


def test_two_card_combos_are_identified():
    raw = variants_payload()["results"][0]
    combo = spellbook.parse_variant(raw)
    assert spellbook.is_two_card_combo(combo) == (combo["card_count"] == 2)


def test_malformed_variant_degrades_rather_than_raising():
    assert spellbook.parse_variant({})["cards"] == ()
    assert spellbook.combos_in_deck(({},), {"anything"}) == ()
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest tests/test_spellbook.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mtgpt.spellbook'`

- [ ] **Step 4: Write `mtgpt/spellbook.py`**

```python
# mtgpt/spellbook.py
"""Commander Spellbook: which combos a decklist actually assembles.

This closes the gap every bracket report has had to declare: brackets 1-3
exclude two-card infinite combos, and without combo data mtgpt could only say
it had not looked.

The backend is unofficial. Failures raise SourceUnavailable, and a malformed
variant degrades to empty rather than crashing a deck audit.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable

from .errors import SourceUnavailable

BASE = "https://backend.commanderspellbook.com/variants/"
REQUEST_DELAY = 0.1
USER_AGENT = "mtgpt/0.1"

Transport = Callable[[str], dict]


def _http_transport(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


class SpellbookClient:
    """Query Commander Spellbook for combos involving a card."""

    def __init__(self, transport: Transport | None = None, sleep=time.sleep):
        self._transport = transport or _http_transport
        self._sleep = sleep
        self._made_request = False

    def variants_for_card(self, name: str, *, limit: int = 50) -> tuple[dict, ...]:
        """Raw combo variants that use `name`."""
        query = urllib.parse.quote(f'card:"{name}"')
        url = f"{BASE}?q={query}&limit={limit}"

        if self._made_request:
            self._sleep(REQUEST_DELAY)
        self._made_request = True

        try:
            body = self._transport(url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SourceUnavailable("Commander Spellbook", f"{url}: {exc}") from exc
        return tuple(body.get("results") or ())


def parse_variant(raw: dict) -> dict:
    """Normalize one Spellbook variant into a flat dict."""
    cards: list[str] = []
    for use in raw.get("uses") or ():
        card = (use or {}).get("card") or {}
        name = card.get("name")
        if name:
            cards.append(name)

    produces: list[str] = []
    for product in raw.get("produces") or ():
        feature = (product or {}).get("feature") or {}
        label = feature.get("name")
        if label:
            produces.append(label)

    return {
        "id": raw.get("id"),
        "cards": tuple(cards),
        "card_count": len(cards),
        "produces": tuple(produces),
        "bracket_tag": raw.get("bracketTag"),
        "salt": raw.get("salt"),
        "description": raw.get("description"),
    }


def is_two_card_combo(combo: dict) -> bool:
    """Brackets 1-3 care specifically about two-card combos."""
    return combo.get("card_count") == 2


def combos_in_deck(
    variants: Iterable[dict], deck_card_names: Iterable[str]
) -> tuple[dict, ...]:
    """Combos whose every piece is present in the deck.

    Matching is case-insensitive. A variant with no usable card list is skipped
    rather than treated as a combo that trivially assembles.
    """
    present = {name.casefold() for name in deck_card_names}
    found: dict[object, dict] = {}

    for raw in variants:
        combo = parse_variant(raw)
        if not combo["cards"]:
            continue
        if all(card.casefold() in present for card in combo["cards"]):
            key = combo["id"] if combo["id"] is not None else combo["cards"]
            found.setdefault(key, combo)

    return tuple(found.values())
```

- [ ] **Step 5: Teach `brackets.check` about combos**

Change the signature to `check(deck, tags=None, target=3, combos=None)`. Add to `BracketRule` a
field `allow_two_card_combos: bool` — `False` for brackets 1, 2, and 3; `True` for 4 and 5.
When `combos` is not None:

```python
    two_card = tuple(c for c in (combos or ()) if c.get("card_count") == 2)
    if combos is not None and two_card and not rule.allow_two_card_combos:
        names = "; ".join(" + ".join(c["cards"]) for c in two_card[:3])
        findings.append(
            Violation(
                severity=Severity.ERROR,
                code="two_card_combo",
                message=(
                    f"{len(two_card)} two-card infinite combo(s) detected, which bracket "
                    f"{rule.number} ({rule.name}) excludes: {names}."
                ),
            )
        )
```

and build `deferred_checks` dynamically, omitting the combo line when `combos is not None`:

```python
#: Checks Layer 1 cannot perform without combo data.
COMBO_DEFERRED = (
    "Two-card infinite combo detection requires Commander Spellbook (not supplied)."
)
EXTRA_TURN_APPROXIMATION = (
    "Chained extra turns are approximated by counting extra-turn spells, not by "
    "detecting repeatability."
)
```

In `check`, assemble `deferred = (EXTRA_TURN_APPROXIMATION,)` when `combos is not None`, else
`(COMBO_DEFERRED, EXTRA_TURN_APPROXIMATION)`. Keep `BracketReport.deferred_checks` as a field
with no default so a caller cannot forget it.

Add to `tests/test_brackets.py`:

```python
def test_combo_deferred_note_disappears_when_combos_are_supplied():
    report = check(deck_of([card("Bear")]), target=2, combos=())
    assert not any("Spellbook" in note for note in report.deferred_checks)


def test_combo_note_present_when_combos_are_not_supplied():
    report = check(deck_of([card("Bear")]), target=2)
    assert any("Spellbook" in note for note in report.deferred_checks)


def test_two_card_combo_is_an_error_below_bracket_four():
    combos = [{"card_count": 2, "cards": ("Thassa's Oracle", "Demonic Consultation")}]
    report = check(deck_of([card("Bear")]), target=2, combos=combos)
    assert "two_card_combo" in [f.code for f in report.findings]
    assert report.compliant is False


def test_two_card_combo_is_allowed_at_bracket_four():
    combos = [{"card_count": 2, "cards": ("Thassa's Oracle", "Demonic Consultation")}]
    report = check(deck_of([card("Bear")]), target=4, combos=combos)
    assert "two_card_combo" not in [f.code for f in report.findings]


def test_three_card_combo_is_not_flagged_as_a_two_card_combo():
    combos = [{"card_count": 3, "cards": ("A", "B", "C")}]
    report = check(deck_of([card("Bear")]), target=2, combos=combos)
    assert "two_card_combo" not in [f.code for f in report.findings]
```

- [ ] **Step 6: Add the facade operations**

```python
def card_combos(name: str, *, spellbook_client=None) -> dict:
    """Combos that use a given card."""
    from .spellbook import SpellbookClient, parse_variant

    source = spellbook_client or SpellbookClient()
    variants = source.variants_for_card(name)
    return {
        "card": name,
        "count": len(variants),
        "combos": [parse_variant(v) | {"cards": list(parse_variant(v)["cards"]),
                                       "produces": list(parse_variant(v)["produces"])}
                   for v in variants],
    }


def deck_combos(text: str, *, client: ScryfallClient | None = None, spellbook_client=None) -> dict:
    """Combos the deck actually assembles.

    Queries Spellbook once per card that participates in any known combo would
    be prohibitive, so this queries per distinct deck card and keeps only the
    variants whose every piece is in the deck.
    """
    from .spellbook import SpellbookClient, combos_in_deck

    deck = _resolved(text, client)
    names = [card.name for _, card in deck.cards] + [c.name for c in deck.commanders]
    source = spellbook_client or SpellbookClient()

    variants: list[dict] = []
    for name in names:
        try:
            variants.extend(source.variants_for_card(name))
        except SourceUnavailable:
            raise

    found = combos_in_deck(variants, names)
    return {
        "commanders": [c.name for c in deck.commanders],
        "count": len(found),
        "two_card_count": sum(1 for c in found if c["card_count"] == 2),
        "combos": [
            c | {"cards": list(c["cards"]), "produces": list(c["produces"])} for c in found
        ],
    }
```

`deck_combos` makes one request per distinct card, which for a 100-card deck is ~70 requests at
100ms — roughly 7 seconds. Note that cost in your report; do not add caching or concurrency in
this task.

Also extend `full_report` to accept `combos: bool = False`; when true, fetch combos and pass them
into `check(...)`, and include a `"combos"` section. Default stays false so `report` remains one
cheap call.

- [ ] **Step 7: Add the subcommands**

```python
    combos_cmd = sub.add_parser("combos", help="Combos a decklist assembles")
    src = combos_cmd.add_mutually_exclusive_group()
    src.add_argument("--file")
    src.add_argument("--stdin", action="store_true")

    card_combos_cmd = sub.add_parser("card-combos", help="Combos that use one card")
    card_combos_cmd.add_argument("name")
```

Wire both in `main`, add them to the registered-subcommand test, and add `--combos` to the
`report` subcommand passing through to `full_report`.

- [ ] **Step 8: Run the tests**

Run: `python3 -m pytest tests/test_spellbook.py tests/test_brackets.py -v`
Expected: PASS — 8 spellbook tests, 22 bracket tests.

Run: `python3 -m pytest -q`
Expected: PASS, all tests.

- [ ] **Step 9: Verify against the live endpoint**

```bash
python3 -m mtgpt.cli card-combos "Thassa's Oracle"
printf "Commander
1 Thassa's Oracle

Deck
1 Demonic Consultation
" > /tmp/combo-deck.txt
python3 -m mtgpt.cli combos --file /tmp/combo-deck.txt
```

Expected: `card-combos` returns variants including the Demonic Consultation pairing with a
"Win the game" product. `combos` on the two-card file detects that combo with `card_count` 2.
Record actual output.

- [ ] **Step 10: Commit**

```bash
git add mtgpt/spellbook.py mtgpt/brackets.py mtgpt/api.py mtgpt/cli.py tests/
git commit -m "feat: detect combos via Commander Spellbook

Closes the gap every bracket report had to declare. Brackets 1-3 exclude
two-card infinite combos; with combo data supplied, brackets.check now
enforces that and drops the corresponding deferred-check line, so a bracket
verdict no longer has to hedge on its central rule.

Combos carry bracketTag and salt, and a malformed variant degrades to empty
rather than crashing a deck audit.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Candidate suggestions — the operation that ties it together

The payoff operation. Given a decklist and a target bracket, propose specific cards to add,
ranked, each justified by the gap it fills and the evidence behind it.

This composes what already exists: audit finds the under-served categories, EDHREC supplies
candidates with synergy and inclusion rates, Scryfall verifies them and enforces color identity
and legality, classify confirms a candidate actually fills the gap it was selected for, and the
bracket rules reject anything that would break the target.

**No card reaches the user unverified, in the wrong color identity, banned, or over its bracket's
Game Changer allowance.** That is the whole point of the pipeline feeding this operation.

**Files:**
- Modify: `mtgpt/api.py` — add `suggest_additions`
- Modify: `mtgpt/cli.py` — add `suggest` subcommand
- Test: `tests/test_suggest.py`

**Interfaces:**
- Consumes: `audit_deck`, `commander_synergy`, `classify`, `validate`, `brackets.RULES`.
- Produces: `api.suggest_additions(text, *, target=3, variant=None, limit=10, client=None, edhrec_client=None) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_suggest.py
import json
import pathlib

import pytest

from mtgpt import api
from mtgpt.scryfall import ScryfallClient

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)

    def __call__(self, url, payload=None):
        return self.responses.pop(0)


class FakeEdhrec:
    def __init__(self, payload):
        self.payload = payload

    def commander(self, name, *, variant=None):
        return self.payload


def test_suggest_reports_the_gaps_it_is_filling():
    """A deck short on ramp must be told so, with the target band."""
    text = (FIXTURES / "sample_deck.txt").read_text()
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False},
            load("collection_basic.json"), {"data": [], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=5, client=client,
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    assert result["gaps"], "expected under-served categories"
    gap = result["gaps"][0]
    assert set(gap) >= {"function", "count", "target", "needed"}
    assert gap["needed"] > 0


def test_every_suggestion_is_legal_in_the_commanders_identity():
    text = (FIXTURES / "sample_deck.txt").read_text()
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False},
            load("collection_basic.json"), {"data": [], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=10, client=client,
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    allowed = set(result["color_identity"])
    for s in result["suggestions"]:
        assert set(s["color_identity"]) <= allowed, s["name"]
        assert s["legal_commander"] == "legal", s["name"]


def test_suggestions_never_include_a_card_already_in_the_deck():
    text = (FIXTURES / "sample_deck.txt").read_text()
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False},
            load("collection_basic.json"), {"data": [], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=10, client=client,
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    present = {"Sol Ring", "Cultivate", "Swords to Plowshares", "Wrath of God", "Forest"}
    assert not ({s["name"] for s in result["suggestions"]} & present)


def test_each_suggestion_states_the_gap_it_fills_and_its_evidence():
    text = (FIXTURES / "sample_deck.txt").read_text()
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False},
            load("collection_basic.json"), {"data": [], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=5, client=client,
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    for s in result["suggestions"]:
        assert s["fills"], "every suggestion must name the gap it fills"
        assert "reason" in s and s["reason"]
        assert "synergy" in s or "inclusion_rate" in s


def test_game_changers_are_excluded_below_their_bracket_allowance():
    """A bracket-2 deck must never be offered a Game Changer."""
    text = (FIXTURES / "sample_deck.txt").read_text()
    # Mark Sol Ring as a Game Changer via the search response.
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"),
            {"data": [{"object": "card", "name": "Rhystic Study"}], "has_more": False},
            load("collection_basic.json"),
            {"data": [{"object": "card", "name": "Rhystic Study"}], "has_more": False},
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=2, limit=20, client=client,
        edhrec_client=FakeEdhrec(load("edhrec_commander.json")),
    )
    assert all(not s["is_game_changer"] for s in result["suggestions"])


def test_suggest_degrades_when_edhrec_is_unavailable():
    """Losing the idea source must not lose the gap analysis."""
    from mtgpt.errors import SourceUnavailable

    class DeadEdhrec:
        def commander(self, name, *, variant=None):
            raise SourceUnavailable("EDHREC", "down")

    text = (FIXTURES / "sample_deck.txt").read_text()
    client = ScryfallClient(
        transport=FakeTransport(
            load("collection_sample_deck.json"), {"data": [], "has_more": False}
        ),
        sleep=lambda _: None,
    )
    result = api.suggest_additions(
        text, target=3, limit=5, client=client, edhrec_client=DeadEdhrec()
    )
    assert result["gaps"], "gap analysis must survive an EDHREC outage"
    assert result["suggestions"] == []
    assert "EDHREC" in result["degraded"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_suggest.py -v`
Expected: FAIL — `AttributeError: module 'mtgpt.api' has no attribute 'suggest_additions'`

- [ ] **Step 3: Implement `suggest_additions` in `mtgpt/api.py`**

```python
def suggest_additions(
    text: str,
    *,
    target: int = 3,
    variant: str | None = None,
    limit: int = 10,
    client: ScryfallClient | None = None,
    edhrec_client=None,
) -> dict:
    """Propose specific cards to add, ranked, each justified.

    Composition, not new logic: audit finds the gaps, EDHREC supplies
    candidates, Scryfall verifies them, classify confirms each one fills the
    gap it was chosen for, and the bracket rules reject anything that would
    break the target.

    Degrades rather than failing: if EDHREC is unreachable the gap analysis is
    still returned, with the outage named in `degraded`.
    """
    from .brackets import RULES
    from .errors import SourceUnavailable as _SourceUnavailable

    scry = _client(client)
    deck = _resolved(text, scry)
    tags = classify_deck(deck)
    report = audit(deck, tags=tags)
    allowed = deck.command_zone_identity
    present = {c.name.casefold() for _, c in deck.cards} | {
        c.name.casefold() for c in deck.commanders
    }

    gaps = [
        {
            "function": c.function.value,
            "count": c.count,
            "target": [c.target_min, c.target_max],
            "needed": c.delta,
        }
        for c in report.categories
        if c.status == "low"
    ]
    gaps.sort(key=lambda g: g["needed"], reverse=True)
    wanted = {g["function"] for g in gaps}

    if not deck.commanders:
        return {
            "commanders": [],
            "color_identity": sorted(allowed),
            "target_bracket": target,
            "gaps": gaps,
            "suggestions": [],
            "degraded": ["no commander declared, so no candidate source"],
        }

    degraded: list[str] = []
    try:
        pool = commander_synergy(
            deck.commanders[0].name,
            variant=variant,
            limit=max(limit * 8, 80),
            client=scry,
            edhrec_client=edhrec_client,
        )["cards"]
    except _SourceUnavailable as exc:
        degraded.append(str(exc))
        pool = []

    rule = RULES[target]
    gc_allowance = rule.game_changers_max
    gc_in_deck = sum(1 for _, c in deck.cards if c.is_game_changer)

    suggestions = []
    for candidate in pool:
        if candidate["name"].casefold() in present:
            continue
        if not set(candidate["color_identity"]) <= allowed:
            continue
        if candidate["legal_commander"] != "legal":
            continue
        if gc_allowance is not None and candidate["is_game_changer"]:
            if gc_in_deck >= gc_allowance:
                continue
        fills = sorted(set(candidate["functions"]) & wanted)
        if not fills:
            continue
        rate = candidate.get("inclusion_rate")
        entry = dict(candidate)
        entry["fills"] = fills
        entry["reason"] = (
            f"fills {', '.join(fills)}; "
            f"played in {rate:.0%} of recorded {deck.commanders[0].name} decks"
            if rate
            else f"fills {', '.join(fills)}"
        )
        suggestions.append(entry)

    suggestions.sort(
        key=lambda s: (len(s["fills"]), s.get("synergy") or 0.0), reverse=True
    )

    return {
        "commanders": [c.name for c in deck.commanders],
        "color_identity": sorted(allowed),
        "target_bracket": target,
        "gaps": gaps,
        "suggestions": suggestions[:limit],
        "degraded": degraded,
    }
```

- [ ] **Step 4: Add the subcommand**

```python
    suggest = sub.add_parser("suggest", help="Propose cards to add, with reasons")
    src = suggest.add_mutually_exclusive_group()
    src.add_argument("--file")
    src.add_argument("--stdin", action="store_true")
    suggest.add_argument("--bracket", type=int, default=3, choices=[1, 2, 3, 4, 5])
    suggest.add_argument("--variant", choices=["budget", "expensive", "upgraded", "cedh"])
    suggest.add_argument("--limit", type=int, default=10)
```

Wire it in `main` and add `"suggest"` to the registered-subcommand test.

- [ ] **Step 5: Run the tests**

Run: `python3 -m pytest tests/test_suggest.py -v`
Expected: PASS, 6 tests.

Run: `python3 -m pytest -q`
Expected: PASS, all tests.

- [ ] **Step 6: Verify against the live services**

```bash
python3 -m mtgpt.cli suggest --file tests/fixtures/sample_deck.txt --bracket 3 --limit 5
```

Expected: `gaps` naming the under-served categories of that 41-card fixture, and up to five
suggestions, each inside Atraxa's `{W}{U}{B}{G}` identity, each with `fills` and a `reason`
citing an inclusion rate. **Confirm explicitly that no suggestion is a red card** — Atraxa's
identity excludes red, and a red suggestion would mean the colour-identity filter is not working.
Record the actual output.

- [ ] **Step 7: Commit**

```bash
git add mtgpt/api.py mtgpt/cli.py tests/test_suggest.py
git commit -m "feat: suggest ranked card additions with reasons

The payoff operation: audit finds under-served categories, EDHREC supplies
candidates with inclusion evidence, Scryfall verifies them, classify
confirms each fills the gap it was chosen for, and bracket rules reject
anything that would break the target.

No suggestion can be unverified, outside the commander's colour identity,
banned, or over its bracket's Game Changer allowance. Degrades to gap
analysis alone when EDHREC is unreachable.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---
## Verification checklist

Layer 1 is done when all of the following hold:

- [ ] `python3 -m pytest` passes with no network access.
- [ ] `python3 -m mtgpt.cli audit --file tests/fixtures/sample_deck.txt --bracket 3` renders a report against the live API.
- [ ] A decklist containing an invented card name exits 2 and names the card.
- [ ] `Cultivate` classifies as `ramp` and not `tutor`.
- [ ] `Dockside Extortionist` classifies as `ramp` and reports as banned.
- [ ] An MDFC is excluded from `land_count` and counted in `mdfc_land_count`.
- [ ] Every bracket report lists its deferred checks.
- [ ] `git log` shows one commit per task.
- [ ] Moxfield URL fetching either works end to end against a real public deck, or the README states plainly that it is unavailable and why. A fetcher that only sometimes works is not an acceptable outcome.
