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

#: The consensus decklist pages. A different path root from `BASE`, not a
#: suffix: `pages/average-decks/<slug>.json`.
AVERAGE_BASE = "https://json.edhrec.com/pages/average-decks"

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

#: Apostrophes are DELETED before the non-slug substitution, not replaced.
#:
#: EDHREC writes "Yuriko, the Tiger's Shadow" as `yuriko-the-tigers-shadow`.
#: Substituting a hyphen gave `yuriko-the-tiger-s-shadow`, which the CDN answers
#: with 403 — so `synergy`, `themes`, `suggest` and `compare` all failed for
#: every commander with an apostrophe inside a word. Verified live against
#: Gishath, Sun's Avatar; K'rrik, Son of Yawgmoth; and Hanna, Ship's Navigator.
#: Names where the apostrophe is followed by a space ("Praetors' Voice") are
#: unaffected either way, which is why the bug survived.
_APOSTROPHES = re.compile(r"['‘’ʼ]")

Transport = Callable[[str], dict]


def commander_slug(name: str) -> str:
    """Convert a commander name to its EDHREC URL slug.

    Accents are stripped rather than escaped, and "&" becomes "and", matching
    EDHREC's own slugs.
    """
    folded = unicodedata.normalize("NFKD", _APOSTROPHES.sub("", name))
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

    def _request(self, url: str) -> dict:
        """Fetch one page, honoring the courtesy delay across calls.

        The delay lives here rather than in each method, so a commander page and
        an average-deck page fetched back to back are still spaced.
        """
        if self._made_request:
            self._sleep(REQUEST_DELAY)
        self._made_request = True

        try:
            return self._transport(url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SourceUnavailable("EDHREC", f"{url}: {exc}") from exc

    def commander(self, name: str, *, variant: str | None = None) -> dict:
        """Fetch a commander page, optionally a bracket/budget variant."""
        if variant is not None and variant not in VARIANTS:
            raise ValueError(
                f"Unknown EDHREC variant {variant!r}. Expected one of "
                f"{', '.join(sorted(VARIANTS))}."
            )
        slug = commander_slug(name)
        suffix = f"/{variant}" if variant else ""
        return self._request(f"{BASE}/{slug}{suffix}.json")

    def average_deck(self, name: str) -> dict:
        """Fetch the consensus decklist EDHREC publishes for a commander.

        This is the "what does the typical build actually play" page: a full 99
        with quantities, aggregated across every recorded deck. Distinct from
        `commander`, which ranks individual cards by synergy without committing
        to a list.
        """
        return self._request(f"{AVERAGE_BASE}/{commander_slug(name)}.json")


def _mapping(value) -> dict:
    """`value` if it is a dict, else an empty dict.

    Every traversal of an EDHREC payload goes through this. `payload.get(...)`
    on a reshaped response raises AttributeError, which is neither TypeError nor
    ValueError and so escaped the guards the rest of this module already had.
    """
    return value if isinstance(value, dict) else {}


def _sequence(value) -> list:
    """`value` if it is a list or tuple, else an empty list.

    A str is deliberately excluded: iterating it yields characters, which would
    turn a reshaped field into a long run of nonsense rather than a skip.
    """
    return list(value) if isinstance(value, (list, tuple)) else []


#: Returned by `_number` when a field is present but is not a number. Distinct
#: from a default, so a caller skips the entry instead of substituting a count it
#: invented — a silent 0 would misreport inclusion rate as 0% rather than
#: declining to claim one.
UNCOERCIBLE = object()


def _number(value, default: float = 0.0):
    """Coerce an EDHREC count. Missing or null yields `default`.

    A value that is present but not a number yields `UNCOERCIBLE`, which every
    caller treats as "skip this entry". Counts have arrived as strings, and
    `min(1.0, num / potential)` on a string raises TypeError straight out of
    synergy_cards — the function on the critical path for both `synergy` and
    `suggest`, and the only one here that had no guard.
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return UNCOERCIBLE


def _cardlists(payload: dict) -> list[dict]:
    """The payload's cardlists, or an empty list for any other shape."""
    container = _mapping(_mapping(payload).get("container"))
    json_dict = _mapping(container.get("json_dict"))
    return [c for c in _sequence(json_dict.get("cardlists")) if isinstance(c, dict)]


def synergy_cards(payload: dict, *, limit: int = 40) -> tuple[dict, ...]:
    """Candidate cards with their synergy score and inclusion evidence.

    Deduplicated by name across lists, sorted by synergy descending. A missing
    or reshaped payload yields an empty tuple rather than raising, and a single
    reshaped cardview is skipped while its well-formed siblings survive: this is
    the critical path for both `synergy` and `suggest`, so one bad count from an
    unofficial source must not take the operation down.

    `CANDIDATE_LISTS` is iterated in its declared priority order rather than
    the payload's own list order, so a card appearing in two candidate lists
    is attributed to the higher-priority one.
    """
    by_header: dict[str, list[dict]] = {}
    for cardlist in _cardlists(payload):
        header = cardlist.get("header") or ""
        if not isinstance(header, str):
            continue
        views = [v for v in _sequence(cardlist.get("cardviews")) if isinstance(v, dict)]
        by_header.setdefault(header, []).extend(views)

    seen: dict[str, dict] = {}
    for header in CANDIDATE_LISTS:
        for view in by_header.get(header, ()):
            name = view.get("name")
            if not name or not isinstance(name, str) or name in seen:
                continue
            num = _number(view.get("num_decks"))
            potential = _number(view.get("potential_decks"))
            synergy = _number(view.get("synergy"))
            if UNCOERCIBLE in (num, potential, synergy):
                # Skip the offending cardview, keep its well-formed siblings.
                continue
            # Glitch data from an unofficial source must not claim >100% inclusion.
            rate = min(1.0, num / potential) if potential > 0 else 0.0
            seen[name] = {
                "name": name,
                "synergy": synergy,
                "num_decks": int(num),
                "potential_decks": int(potential),
                "inclusion_rate": round(rate, 4),
                "list": header,
            }

    ranked = sorted(seen.values(), key=lambda c: c["synergy"], reverse=True)
    return tuple(ranked[:limit])


def themes(payload: dict) -> tuple[dict, ...]:
    """Archetypes this commander is built as, most common first."""
    out = []
    for tag in _sequence(_mapping(payload).get("tag_counts")):
        if not isinstance(tag, dict):
            # Unofficial source: a reshaped entry must not crash the toolkit.
            continue
        count = _number(tag.get("count"))
        if count is UNCOERCIBLE:
            continue
        out.append(
            {
                "slug": str(tag.get("slug") or ""),
                "label": str(tag.get("value") or ""),
                "count": int(count),
            }
        )
    return tuple(sorted(out, key=lambda t: t["count"], reverse=True))


def bracket_distribution(payload: dict) -> dict[int, int]:
    """How many recorded decks sit in each bracket, keyed 1-5."""
    raw = _mapping(_mapping(payload).get("bracket_counts"))
    out: dict[int, int] = {}
    for key, value in raw.items():
        try:
            bracket = int(key)
        except (TypeError, ValueError):
            # Unofficial source: a reshaped key must not crash the toolkit.
            continue
        count = _number(value)
        if count is UNCOERCIBLE:
            continue
        if 1 <= bracket <= 5:
            out[bracket] = int(count)
    return out


def average_commanders(payload: dict) -> tuple[str, ...]:
    """The commander(s) the average deck is built around, or empty."""
    deck = _mapping(_mapping(payload).get("deck"))
    return tuple(
        name.strip()
        for name in _sequence(deck.get("commander"))
        if isinstance(name, str) and name.strip()
    )


def average_cards(payload: dict) -> tuple[dict, ...]:
    """The average deck's 99, flattened to `{name, qty, type}` entries.

    Read from `deck.cards`, a mapping of card type to `[name, quantity]` pairs,
    because that is the only place the page states quantities — the `cardlists`
    block the commander pages use carries names alone, so four basics there mean
    four *kinds* of basic, not four cards. `cardlists` is the fallback for a
    reshaped payload, with every quantity recorded as 1 and that visible in the
    data rather than assumed.

    Commanders are excluded: EDHREC keeps them in `deck.commander`, and a diff
    against your deck should not report your own commander as a difference.

    Degrades to an empty tuple on any other shape, and skips a single malformed
    row while keeping its siblings.
    """
    deck = _mapping(_mapping(payload).get("deck"))
    grouped = _mapping(deck.get("cards"))

    out: list[dict] = []
    for card_type, rows in grouped.items():
        for row in _sequence(rows):
            pair = _sequence(row)
            if not pair or not isinstance(pair[0], str) or not pair[0].strip():
                continue
            qty = _number(pair[1], 1.0) if len(pair) > 1 else 1.0
            if qty is UNCOERCIBLE or qty < 1:
                continue
            out.append(
                {
                    "name": pair[0].strip(),
                    "qty": int(qty),
                    "type": str(card_type),
                }
            )

    if out:
        return tuple(out)

    # Fallback: the type-split cardlists. Names only, so every qty is 1 and the
    # caller can see that from the data.
    for cardlist in _cardlists(payload):
        header = cardlist.get("header")
        for view in _sequence(cardlist.get("cardviews")):
            if not isinstance(view, dict):
                continue
            name = view.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            out.append(
                {
                    "name": name.strip(),
                    "qty": 1,
                    "type": str(header) if isinstance(header, str) else "",
                }
            )
    return tuple(out)
