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
