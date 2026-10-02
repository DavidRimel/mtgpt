# mtgpt/archidekt.py
"""Archidekt: import a deck from a URL.

Moxfield is the format's most-used deckbuilder and is unreachable from here —
Cloudflare answers scripted requests with a challenge and no browser is
available to solve it. Archidekt's JSON API is open, so a user with an Archidekt
link no longer has to paste an export.

The output is *text*, deliberately. `to_decklist` emits the same shape
`deckparse.parse` already reads, so there is one decklist parser in this
toolkit rather than two that can disagree about what a commander is.

The API is undocumented and carries no compatibility contract, so every
transport failure raises SourceUnavailable and every reshaped field degrades to
a skipped entry rather than a traceback — the same rule `edhrec.py` follows.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from .errors import SourceUnavailable

BASE = "https://archidekt.com/api/decks"

REQUEST_DELAY = 0.1
USER_AGENT = "mtgpt/0.1"

#: The category Archidekt puts the command zone in.
COMMANDER_CATEGORY = "Commander"

#: Categories excluded from the 100 unless the deck's own `categories` block
#: explicitly marks them `includedInDeck`. Mirrors
#: `deckparse.EXCLUDED_SECTIONS`, and is only a fallback: the deck's own flags
#: win, because the author may have chosen to count their sideboard.
DEFAULT_EXCLUDED_CATEGORIES = frozenset({"maybeboard", "sideboard", "considering"})

#: `/decks/<id>` in a web URL and `/api/decks/<id>/` both match this.
_DECK_ID_RE = re.compile(r"(?:^|/)decks?/(\d+)")

Transport = Callable[[str], dict]


def deck_id(url_or_id: str) -> str:
    """Extract an Archidekt deck id from a URL or accept a bare id.

    Accepts `https://archidekt.com/decks/2000000/some-slug`, the `/api/decks/`
    form, and `2000000` on its own. Raises ValueError rather than guessing: a
    URL we cannot parse must not silently become a request for some other deck.
    """
    text = str(url_or_id).strip()
    if text.isdigit():
        return text
    match = _DECK_ID_RE.search(text)
    if match:
        return match.group(1)
    raise ValueError(
        f"Could not find an Archidekt deck id in {url_or_id!r}. Expected a URL "
        "like https://archidekt.com/decks/2000000/my-deck or a bare numeric id. "
        "Moxfield links cannot be fetched — ask the user for the Export text."
    )


def _http_transport(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


class ArchidektClient:
    """Fetch Archidekt deck payloads."""

    def __init__(self, transport: Transport | None = None, sleep=time.sleep):
        self._transport = transport or _http_transport
        self._sleep = sleep
        self._made_request = False

    def _request(self, url: str) -> dict:
        """Perform a request, honoring the courtesy delay across calls."""
        if self._made_request:
            self._sleep(REQUEST_DELAY)
        self._made_request = True
        try:
            return self._transport(url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SourceUnavailable("Archidekt", f"{url}: {exc}") from exc

    def deck(self, identifier: str) -> dict:
        """Fetch one deck. `identifier` may be a URL or a bare id."""
        return self._request(f"{BASE}/{deck_id(identifier)}/")


def _mapping(value) -> dict:
    """`value` if it is a dict, else an empty dict. See `edhrec._mapping`."""
    return value if isinstance(value, dict) else {}


def _sequence(value) -> list:
    """`value` if it is a list or tuple, else an empty list. A str is excluded."""
    return list(value) if isinstance(value, (list, tuple)) else []


def _quantity(value) -> int:
    """Coerce a quantity. Anything uncoercible or non-positive yields 0.

    0 means "skip this entry". A quantity that arrived as a string, as null, or
    as something else entirely must not turn a deck import into a traceback, and
    inventing a 1 would silently add a card the author may have removed.
    """
    try:
        qty = int(value)
    except (TypeError, ValueError):
        return 0
    return qty if qty > 0 else 0


def _card_name(entry: dict) -> str:
    """The oracle name for a deck entry, or "" if the shape is unusable.

    `card.oracleCard.name` is the name Scryfall will recognise; `card.displayName`
    is whatever art variant the author picked and is not always resolvable.
    """
    card = _mapping(entry.get("card"))
    oracle = _mapping(card.get("oracleCard"))
    name = oracle.get("name") or card.get("displayName") or ""
    return name.strip() if isinstance(name, str) else ""


def _category_names(entry: dict) -> list[str]:
    return [c for c in _sequence(entry.get("categories")) if isinstance(c, str)]


def _inclusion_flags(payload: dict) -> dict[str, bool]:
    """Casefolded category name -> whether the deck counts it in the 100."""
    flags: dict[str, bool] = {}
    for category in _sequence(_mapping(payload).get("categories")):
        category = _mapping(category)
        name = category.get("name")
        if isinstance(name, str):
            flags[name.casefold()] = bool(category.get("includedInDeck", True))
    return flags


def _is_excluded(categories: list[str], flags: dict[str, bool]) -> bool:
    """Whether an entry sits outside the deck proper.

    The deck's own `includedInDeck` flag is authoritative when present; the
    static name list only covers categories the payload did not describe.
    """
    for name in categories:
        key = name.casefold()
        if key in flags:
            if not flags[key]:
                return True
        elif key in DEFAULT_EXCLUDED_CATEGORIES:
            return True
    return False


def deck_name(payload: dict) -> str:
    """The deck's title, or "" if absent."""
    name = _mapping(payload).get("name")
    return name.strip() if isinstance(name, str) else ""


def declared_bracket(payload: dict) -> int | None:
    """The `edhBracket` the author set, or None.

    This is a claim, not a verdict. `api.bracket_check` computes one.
    """
    raw = _mapping(payload).get("edhBracket")
    if raw is None:
        return None
    try:
        bracket = int(raw)
    except (TypeError, ValueError):
        return None
    return bracket if 1 <= bracket <= 5 else None


def to_decklist(payload: dict) -> str:
    """Render an Archidekt payload as text `deckparse.parse` understands.

    Entries whose categories include `Commander` go under a `Commander` header;
    everything else goes under `Deck`. Maybeboard and sideboard entries are
    dropped per `_is_excluded`. An entry with no usable name or a non-positive
    quantity is skipped, so one reshaped row costs one card rather than the
    whole import.
    """
    payload = _mapping(payload)
    flags = _inclusion_flags(payload)

    commanders: list[str] = []
    deck: list[str] = []

    for entry in _sequence(payload.get("cards")):
        entry = _mapping(entry)
        name = _card_name(entry)
        qty = _quantity(entry.get("quantity"))
        if not name or not qty:
            continue
        categories = _category_names(entry)
        if _is_excluded(categories, flags):
            continue
        line = f"{qty} {name}"
        if COMMANDER_CATEGORY.casefold() in {c.casefold() for c in categories}:
            commanders.append(line)
        else:
            deck.append(line)

    lines: list[str] = []
    if commanders:
        lines.append("Commander")
        lines.extend(commanders)
        lines.append("")
    lines.append("Deck")
    lines.extend(deck)
    return "\n".join(lines) + "\n"
