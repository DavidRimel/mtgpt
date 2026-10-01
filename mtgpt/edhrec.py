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
