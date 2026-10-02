"""Resolve card names against Scryfall.

The only module in mtgpt that performs network I/O. All requests pass through
an injectable `transport`, so tests exercise the mapping and batching logic
offline.

Scryfall asks clients to identify themselves and to leave 50-100ms between
requests. We send a real User-Agent and use the polite end of that range.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence

from .errors import SourceUnavailable, UnresolvedCards
from .models import Card, ParsedDeck, ResolvedDeck

API = "https://api.scryfall.com"

#: Scryfall accepts at most 75 identifiers per /cards/collection request.
COLLECTION_BATCH_SIZE = 75

#: Scryfall's requested courtesy delay between requests, in seconds.
REQUEST_DELAY = 0.1

#: Maximum pages for paginated endpoints before raising an error.
MAX_GAME_CHANGER_PAGES = 20

#: Maximum pages `search` will follow before raising an error.
MAX_SEARCH_PAGES = 20

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
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except ValueError as exc:
        raise SourceUnavailable("Scryfall", f"malformed response: {exc}") from exc


def collection_identifier(name: str) -> str:
    """The name to send to /cards/collection for a possibly-split card name.

    Scryfall's /cards/collection rejects every full "A // B" name as an
    identifier: `Fire // Ice`, `Dusk // Dawn`, `Bottomless Pool // Locker Room`
    and the Zendikar MDFC lands all land in `not_found`, while the front half
    resolves to the same card. A Moxfield export carries the full name, so
    sending it verbatim tells the user a correctly-spelled card is misspelled.

    Done here rather than as a retry of `not_found`, so there is one code path:
    a retry would leave the first request still able to report a real card as
    missing if the retry itself failed.
    """
    front, separator, _ = name.partition("//")
    return front.strip() if separator else name


def _front_face(payload: dict) -> dict:
    """The face whose cost you pay. Falls back to the card itself."""
    faces = payload.get("card_faces")
    return faces[0] if faces else payload


_LEADING_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _stat(value) -> float | None:
    """Parse a power or toughness string. "*" is 0 and "1+*" is 1.

    None stays None: it means the card has no power at all, which is not the
    same as a 0-power creature.
    """
    if value is None:
        return None
    match = _LEADING_NUMBER.match(str(value))
    return float(match.group()) if match else 0.0


def _number(value, default: float | None = None) -> float | None:
    """Coerce a Scryfall numeric field, falling back rather than raising.

    Scryfall is well-behaved today, but a reshaped or null field must not turn a
    card lookup into a traceback — the same rule edhrec.py applies to its counts.

    Unlike `edhrec._number`, this substitutes the default instead of signalling
    "skip". The difference is deliberate: an EDHREC cardview with a broken count
    is one suggestion among many and is better dropped, whereas a Scryfall card
    is the thing the caller asked for. Its name, text and legality are still
    correct, so returning it with an unknown price is better than returning
    nothing.
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def card_from_json(payload: dict, *, game_changers: frozenset[str]) -> Card:
    """Map a Scryfall card payload onto a Card.

    For multi-face cards, cost/colors/text come from the front face while
    `type_line` stays at the top level (so it reads "Sorcery // Land" and
    `is_mdfc_land` can do its job). Using only the front face's text is
    deliberate: a land back face would otherwise register as ramp.

    `game_changers` is required, not defaulted. A default of `frozenset()`
    silently reports `is_game_changer=False` for every card, which reads as a
    clean bill of health at a bracket that allows none; omitting it is now a
    TypeError at the call site instead. Pass `frozenset()` explicitly only
    where the caller has already recorded the list as unavailable.
    """
    front = _front_face(payload)
    name = payload.get("name", "")
    price = (payload.get("prices") or {}).get("usd")

    return Card(
        name=name,
        mana_value=_number(payload.get("cmc"), 0.0),
        type_line=payload.get("type_line") or front.get("type_line", ""),
        oracle_text=front.get("oracle_text") or payload.get("oracle_text") or "",
        mana_cost=front.get("mana_cost") or payload.get("mana_cost") or "",
        color_identity=frozenset(payload.get("color_identity") or ()),
        colors=frozenset(front.get("colors") or payload.get("colors") or ()),
        legal_commander=(payload.get("legalities") or {}).get("commander", "unknown"),
        produced_mana=frozenset(payload.get("produced_mana") or ()),
        layout=payload.get("layout", "normal"),
        is_game_changer=name.casefold() in game_changers,
        usd=_number(price),
        keywords=tuple(payload.get("keywords") or ()),
        power=_stat(front.get("power", payload.get("power"))),
        toughness=_stat(front.get("toughness", payload.get("toughness"))),
    )


class ScryfallClient:
    """Thin Scryfall wrapper with batching and courtesy delays."""

    def __init__(self, transport: Transport | None = None, sleep=time.sleep):
        self._transport = transport or _http_transport
        self._sleep = sleep
        self._made_request = False

    def _request(self, url: str, payload: dict | None = None) -> dict:
        """Perform a request, honoring Scryfall's courtesy delay across calls.

        The delay is owned here rather than in each method's loop, so two
        different endpoints hit back to back are still spaced.
        """
        if self._made_request:
            self._sleep(REQUEST_DELAY)
        self._made_request = True
        if payload is not None:
            return self._transport(url, payload)
        return self._transport(url)

    def collection(
        self, names: Sequence[str], *, strict: bool = True
    ) -> tuple[tuple[dict, ...], tuple[str, ...]]:
        """Resolve names in batches of 75.

        With `strict=True` (the default) any name Scryfall reports as not found
        raises UnresolvedCards. That is the right behavior for a user's
        decklist: a typo must stop the run rather than be silently dropped.

        With `strict=False` the unresolved names are returned alongside the
        found ones instead of raising. That is for candidate lists from an
        unofficial source, where one unresolvable suggestion must not discard
        the rest.
        """
        found: list[dict] = []
        missing: list[str] = []

        for index in range(0, len(names), COLLECTION_BATCH_SIZE):
            batch = names[index : index + COLLECTION_BATCH_SIZE]
            # Split/Room/aftermath/MDFC names go up as their front half; keep a
            # map back so an unresolved name is reported as the user wrote it.
            sent = [collection_identifier(n) for n in batch]
            as_written: dict[str, str] = {}
            for original, identifier in zip(batch, sent):
                as_written.setdefault(identifier.casefold(), original)
            payload = {"identifiers": [{"name": n} for n in sent]}
            try:
                body = self._request(f"{API}/cards/collection", payload)
            except (urllib.error.URLError, OSError) as exc:
                raise SourceUnavailable("Scryfall", str(exc)) from exc
            found.extend(body.get("data") or ())
            for entry in body.get("not_found") or ():
                reported = entry.get("name", "<unknown>")
                missing.append(as_written.get(reported.casefold(), reported))

        if strict and missing:
            raise UnresolvedCards(missing)

        return tuple(found), tuple(missing)

    def game_changers(self) -> frozenset[str]:
        """Casefolded names on the current Game Changers list.

        WotC revises this list, so it is fetched rather than hardcoded.
        """
        names: set[str] = set()
        url = f"{API}/cards/search?q=is%3Agamechanger&unique=cards"
        pages = 0
        while url:
            pages += 1
            if pages > MAX_GAME_CHANGER_PAGES:
                raise SourceUnavailable("Scryfall Game Changers", f"pagination exceeded {MAX_GAME_CHANGER_PAGES} pages")
            try:
                body = self._request(url)
            except (urllib.error.URLError, OSError) as exc:
                raise SourceUnavailable("Scryfall Game Changers", str(exc)) from exc
            for card in body.get("data") or ():
                name = card.get("name")
                if name:
                    names.add(name.casefold())
            url = body.get("next_page") if body.get("has_more") else None
        return frozenset(names)

    def search(
        self, query: str, *, limit: int = 25, allow_empty: bool = False
    ) -> tuple[dict, ...]:
        """Run a Scryfall search and return up to `limit` card payloads.

        This is how an agent finds candidate cards. Results are capped because
        the caller is choosing among options, not enumerating a set.

        `allow_empty` changes what a 404 means. Scryfall answers a query that
        matches nothing with HTTP 404, not an empty list, so by default a
        zero-result search is reported as SourceUnavailable — which is right for
        a hand-written query, where "no cards" almost always means the query was
        wrong. With `allow_empty=True` a 404 returns an empty tuple instead, for
        callers that build the query themselves from a verified vocabulary
        (`tagger.build_query`) and for whom "nothing in these colours" is a real
        answer rather than a mistake. Any other HTTP status still raises.
        """
        url = (
            f"{API}/cards/search?q={urllib.parse.quote(query)}"
            "&unique=cards&order=edhrec"
        )
        found: list[dict] = []
        pages = 0
        while url and len(found) < limit:
            pages += 1
            if pages > MAX_SEARCH_PAGES:
                raise SourceUnavailable(
                    "Scryfall search", f"pagination exceeded {MAX_SEARCH_PAGES} pages"
                )
            try:
                body = self._request(url)
            except urllib.error.HTTPError as exc:
                # Caught before URLError, which it subclasses.
                if allow_empty and exc.code == 404:
                    break
                raise SourceUnavailable("Scryfall search", str(exc)) from exc
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise SourceUnavailable("Scryfall search", str(exc)) from exc
            found.extend(body.get("data") or ())
            url = body.get("next_page") if body.get("has_more") else None
        return tuple(found[:limit])


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
    outage degrades instead: the audit proceeds with every card unflagged and
    `game_changers_available` is set False, which `brackets.check` turns into a
    deferred-check note. The flag exists because the degraded result is
    indistinguishable from a clean one: zero Game Changers found reads as
    compliant at every bracket.
    """
    client = client or ScryfallClient()
    all_entries = list(deck.commanders) + list(deck.entries)
    names = [entry.name for entry in all_entries]

    payloads, _ = client.collection(names)

    game_changers_available = True
    try:
        game_changers = client.game_changers()
    except SourceUnavailable:
        game_changers = frozenset()
        game_changers_available = False

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
        game_changers_available=game_changers_available,
    )
