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
from .errors import DeckStructureError, SourceUnavailable, UnresolvedCards
from .goal import GoalError
from .goldfish.engine import (
    DEFAULT_TURN_CAP, GameState, IllegalAction, apply, available_mana, from_dict,
    legal_actions, new_game, prepare, to_dict,
)
from .goldfish.run import DEFAULT_GAMES, compare, simulate
from .models import Card, Function, ResolvedDeck, Violation
from .scryfall import ScryfallClient, card_from_json, resolve
from .validate import validate


def _client(client: ScryfallClient | None) -> ScryfallClient:
    return client or ScryfallClient()


def _game_changers(client: ScryfallClient) -> frozenset[str]:
    """Fetch the Game Changers list, degrading to empty on an outage.

    Every call site that builds a Card must pass this: `card_from_json` requires
    `game_changers=` precisely so omitting it cannot silently report
    `is_game_changer=False` for a card on the live list. Callers that then
    enforce a bracket allowance are responsible for saying the list was
    unavailable — `scryfall.resolve` records it on the deck, and
    `commander_synergy` returns `game_changers_available`.
    """
    try:
        return client.game_changers()
    except SourceUnavailable:
        return frozenset()


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


def error_payload(exc: Exception) -> dict:
    """Serialize an exception into the error envelope's `data`.

    Lives here, not in `cli.py`, because serialization is api.py's job: a
    future MCP adapter needs this mapping too, and should not have to
    reimplement it.
    """
    payload = {"type": type(exc).__name__, "message": str(exc)}
    if isinstance(exc, UnresolvedCards):
        payload["names"] = list(exc.names)
    if isinstance(exc, SourceUnavailable):
        payload["source"] = exc.source
    if isinstance(exc, GoalError):
        payload["field"] = exc.field
        payload["values"] = list(exc.values)
    if isinstance(exc, IllegalAction):
        payload["action"] = exc.action
        payload["legal_actions"] = exc.legal
    return payload


# --- Card operations -------------------------------------------------------


def lookup_card(name: str, *, client: ScryfallClient | None = None) -> dict:
    """Resolve one card against Scryfall and tag it.

    Use this before naming any card in a recommendation. Raises
    UnresolvedCards if the name is not real.

    Matching is by name, not by response position: Scryfall returns a modal DFC
    under its full "A // B" name even when requested as "A", and relying on
    position would silently return the wrong card.
    """
    scry = _client(client)
    payloads, _ = scry.collection([name])
    wanted = name.casefold()
    for payload in payloads:
        candidate = payload.get("name", "")
        front, _, _ = candidate.partition("//")
        faces = payload.get("card_faces") or ()
        aliases = {candidate.casefold(), front.strip().casefold()}
        if faces:
            aliases.add((faces[0].get("name") or "").strip().casefold())
        if wanted in aliases:
            # Fetched only once the card is known to exist, so an invented name
            # costs one request rather than two.
            card = card_from_json(payload, game_changers=_game_changers(scry))
            return _card_dict(card, classify(card))
    raise UnresolvedCards([name])


def search_cards(
    query: str, *, limit: int = 25, client: ScryfallClient | None = None
) -> dict:
    """Find candidate cards with a Scryfall query, pre-tagged by function."""
    scry = _client(client)
    payloads = scry.search(query, limit=limit)
    game_changers = _game_changers(scry)
    cards = [card_from_json(p, game_changers=game_changers) for p in payloads]
    return {
        "query": query,
        "count": len(cards),
        "cards": [_card_dict(c, classify(c)) for c in cards],
    }


def find_cards(
    function: str,
    *,
    identity: str | None = None,
    limit: int = 25,
    extra_query: str | None = None,
    cross_check: bool = True,
    client: ScryfallClient | None = None,
) -> dict:
    """Find cards by the function a human said they perform.

    Scryfall exposes community-curated oracle tags as `otag:`, which is a
    different kind of evidence from everything else here: `search` matches text
    you wrote, `synergy` reports what players play, and this reports what the
    tagging community decided a card *does*. Results come back in `order=edhrec`,
    so the most-played candidates lead.

    Only tags verified to resolve are accepted — see `tagger.TAGS` — because
    Scryfall answers an unknown `otag:` with a 404 that is indistinguishable
    from "nothing in those colours".

    `cross_check` (default true) adds `agrees_with_classify` per card and
    `recall_estimate` overall, comparing the human tag against `classify.py`'s
    regex. A disagreement means one of the two is wrong; report it rather than
    silently trusting whichever you looked at first. It is omitted for tags our
    own classifier has no notion of (`wheel`, `theft`, ...), where claiming
    either agreement or disagreement would be inventing a verdict.

    **`recall_estimate` measures recall ONLY, and the name says so on purpose.**
    This operation samples cards Tagger labelled and asks whether `classify`
    agrees, so it cannot see a card `classify` tagged that Tagger did not — it is
    blind to false positives by construction. A `recall_estimate` of 0.67 read as
    "67% accurate" once hid a classifier at 0.92 precision with 135 false
    positives. For the other direction call `check_classifier`, which samples
    what `classify` tagged; for both at once call `cross_check_function`. Never
    report one of these numbers without its label.
    """
    from . import tagger

    label = tagger.canonical_label(function)
    otag = tagger.function_tag(label)
    query = tagger.build_query(label, identity=identity, extra=extra_query)

    scry = _client(client)
    # allow_empty: the query is machine-built from a verified vocabulary, so a
    # 404 here means "no such card in these colours", not a broken query.
    payloads = scry.search(query, limit=limit, allow_empty=True)
    game_changers = _game_changers(scry)

    expected = tagger.cross_check_functions(label) if cross_check else frozenset()
    cards: list[dict] = []
    agreed = 0
    for payload in payloads:
        card = card_from_json(payload, game_changers=game_changers)
        functions = classify(card)
        entry = _card_dict(card, functions)
        entry["source"] = "scryfall-tagger"
        entry["otag"] = otag
        if expected:
            agrees = bool(functions & expected)
            entry["agrees_with_classify"] = agrees
            agreed += agrees
        cards.append(entry)

    result = {
        "function": label,
        "otag": otag,
        "query": query,
        "identity": identity,
        "count": len(cards),
        "cards": cards,
    }
    if expected:
        result["classify_expects"] = sorted(f.value for f in expected)
        result["recall_estimate"] = round(agreed / len(cards), 4) if cards else None
        result["measures"] = (
            "recall only: of cards the community tagged, the share classify also "
            "tagged. Blind to false positives — call check_classifier for those."
        )
    else:
        result["classify_expects"] = []
        result["recall_estimate"] = None
        result["measures"] = None
        if cross_check:
            result["cross_check_note"] = (
                f"classify.py has no tag corresponding to otag:{otag}, so no "
                "agreement can be claimed either way."
            )
    return result


def check_classifier(
    function: str,
    *,
    identity: str | None = None,
    limit: int = 25,
    client: ScryfallClient | None = None,
) -> dict:
    """The reverse of `find_cards`: does the community agree with OUR tag?

    `find_cards` samples what Tagger labelled and asks whether `classify` agrees.
    That is recall, and recall alone cannot see a false positive. This samples the
    other way — cards `classify` tags with `function`, drawn from a Scryfall
    search for cards that mention the function's own vocabulary — and asks whether
    Tagger agrees, which is precision.

    Both numbers are needed because they fail independently. A regex that tags
    every card in the format scores recall 1.0 and precision near zero; the
    original `_RECURSION` scored recall 0.73 at precision 0.92, and the 135 false
    positives behind that 0.92 were invisible to the recall measurement.

    The sample is a cheap estimate, not a corpus scan: Scryfall's bulk
    `oracle-tags` export plus the oracle-card export is the way to measure these
    properly, and `references/sources.md` says how. Use this to notice a problem,
    then measure it against the bulk data.
    """
    from . import tagger

    label = tagger.canonical_label(function)
    otag = tagger.function_tag(label)
    expected = tagger.cross_check_functions(label)
    if not expected:
        return {
            "function": label,
            "otag": otag,
            "count": 0,
            "cards": [],
            "precision_estimate": None,
            "measures": None,
            "cross_check_note": (
                f"classify.py has no tag corresponding to otag:{otag}, so there "
                "is nothing to check in this direction."
            ),
        }

    scry = _client(client)
    # Sample cards that mention the subject matter at all, then keep the ones our
    # own classifier tags. Searching `otag:` here would beg the question.
    probe = " or ".join(f"o:{word}" for word in _PROBE_WORDS.get(label, (label,)))
    query = f"({probe}) legal:commander"
    if identity:
        query += f" {tagger.identity_filter(identity)}"
    payloads = scry.search(query, limit=max(limit * 6, 60), allow_empty=True)
    game_changers = _game_changers(scry)

    tagged = []
    for payload in payloads:
        card = card_from_json(payload, game_changers=game_changers)
        functions = classify(card)
        if functions & expected:
            tagged.append((card, functions))
        if len(tagged) >= limit:
            break

    confirmed = 0
    cards: list[dict] = []
    if tagged:
        # One search per card would be slow; ask Scryfall once whether each name
        # carries the tag, in a single `otag:<tag> (!"a" or !"b" ...)` query.
        names = " or ".join(f'!"{card.name}"' for card, _ in tagged)
        try:
            confirmations = scry.search(
                f"otag:{otag} ({names})", limit=len(tagged), allow_empty=True
            )
        except SourceUnavailable:
            confirmations = ()
        has_tag = {p.get("name", "").casefold() for p in confirmations}
        for card, functions in tagged:
            agrees = card.name.casefold() in has_tag
            entry = _card_dict(card, functions)
            entry["source"] = "classify"
            entry["otag"] = otag
            entry["community_agrees"] = agrees
            confirmed += agrees
            cards.append(entry)

    return {
        "function": label,
        "otag": otag,
        "query": query,
        "identity": identity,
        "count": len(cards),
        "cards": cards,
        "precision_estimate": round(confirmed / len(cards), 4) if cards else None,
        "measures": (
            "precision only: of cards classify tagged, the share the community "
            "tagged too. Blind to false negatives — call find_cards for those."
        ),
    }


def cross_check_function(
    function: str,
    *,
    identity: str | None = None,
    limit: int = 25,
    client: ScryfallClient | None = None,
) -> dict:
    """Both directions at once: recall from `find_cards`, precision from `check_classifier`.

    Reported together because reporting either alone has already misled once.
    """
    scry = _client(client)
    forward = find_cards(function, identity=identity, limit=limit, client=scry)
    reverse = check_classifier(function, identity=identity, limit=limit, client=scry)
    return {
        "function": forward["function"],
        "otag": forward["otag"],
        "identity": identity,
        "recall_estimate": forward["recall_estimate"],
        "precision_estimate": reverse["precision_estimate"],
        "recall_sample": forward["count"],
        "precision_sample": reverse["count"],
        "recall_disagreements": [
            c["name"] for c in forward["cards"] if c.get("agrees_with_classify") is False
        ],
        "precision_disagreements": [
            c["name"] for c in reverse["cards"] if c.get("community_agrees") is False
        ],
        "measures": (
            "recall_estimate: of cards the community tagged, the share classify "
            "also tagged. precision_estimate: of cards classify tagged, the share "
            "the community tagged too. Both are small samples — see "
            "references/sources.md for the bulk-data method that measures them "
            "over the whole corpus."
        ),
    }


#: Oracle-text words that find candidates for each function without using
#: `otag:`. Searching `otag:` to measure precision against `otag:` would beg the
#: question, so `check_classifier` samples by subject matter instead.
_PROBE_WORDS: dict[str, tuple[str, ...]] = {
    "ramp": ("mana", "land"),
    "mana_rock": ("mana",),
    "mana_dork": ("mana",),
    "land_ramp": ("land",),
    "draw": ("draw",),
    "card_advantage": ("draw",),
    "wheel": ("draw", "discard"),
    "spot_removal": ("destroy", "exile", "damage"),
    "creature_removal": ("destroy", "exile"),
    "removal": ("destroy", "exile", "damage"),
    "sweeper": ("destroy", "each"),
    "mass_removal": ("destroy", "each"),
    "board_wipe": ("destroy", "each"),
    "tutor": ("search",),
    "counterspell": ("counter",),
    "protection": ("hexproof", "indestructible", "protection"),
    "recursion": ("graveyard",),
    "extra_turns": ("turn",),
    "wincon": ("win", "lose"),
    "mass_land_denial": ("land",),
}


def classify_cards(
    names: list[str], *, client: ScryfallClient | None = None
) -> dict[str, list[str]]:
    """Tag several cards by function, keyed by name."""
    scry = _client(client)
    cards, _ = scry.collection(names)
    game_changers = _game_changers(scry)
    out: dict[str, list[str]] = {}
    for payload in cards:
        card = card_from_json(payload, game_changers=game_changers)
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
    text: str,
    *,
    target: int = 3,
    client: ScryfallClient | None = None,
    combos: bool = False,
    spellbook_client=None,
) -> dict:
    """Everything composed, for when the agent wants one complete picture.

    `combos` defaults to false so `report` stays one cheap Scryfall-only call.
    When true, it queries Commander Spellbook once per distinct deck card
    (roughly 7 seconds for a 100-card deck) and the bracket verdict enforces
    two-card infinite combos rather than deferring that check.
    """
    deck = _resolved(text, client)
    tags = classify_deck(deck)
    detected: tuple[dict, ...] | None = None
    combos_section: dict | None = None
    if combos:
        from .spellbook import SpellbookClient, combos_in_deck

        names = [card.name for _, card in deck.cards] + [c.name for c in deck.commanders]
        source = spellbook_client or SpellbookClient()
        variants: list[dict] = []
        for name in names:
            variants.extend(source.variants_for_card(name))
        detected = combos_in_deck(variants, names)
        combos_section = {
            "count": len(detected),
            "two_card_count": sum(1 for c in detected if c["card_count"] == 2),
            "combos": [
                c | {"cards": list(c["cards"]), "produces": list(c["produces"])}
                for c in detected
            ],
        }

    result = {
        "commanders": [c.name for c in deck.commanders],
        "color_identity": sorted(deck.command_zone_identity),
        "total_cards": deck.total_with_commanders,
        "violations": _violations(validate(deck)),
        "audit": _audit_dict(audit(deck, tags=tags)),
        "bracket": _bracket_dict(check(deck, tags=tags, target=target, combos=detected)),
        "tags": _tags_dict(tags),
    }
    if combos_section is not None:
        result["combos"] = combos_section
    return result


# --- Archidekt operations ---------------------------------------------------


def import_deck(
    url: str, *, client: ScryfallClient | None = None, archidekt_client=None
) -> dict:
    """Fetch a deck from an Archidekt URL and parse it.

    This is the only URL import that works from here: Moxfield answers scripted
    requests with a Cloudflare challenge and no browser is available. Accepts a
    full URL, the `/api/` form, or a bare deck id.

    `declared_bracket` is the `edhBracket` the deck's author set — a claim about
    the deck, not a verdict on it. Pass the returned `decklist` to `bracket` (or
    use `--url` on it directly) to find out what the rules actually say; the two
    disagreeing is worth telling the user about.

    `client` is accepted for signature symmetry with the other operations and is
    unused: parsing is structural, so import makes no Scryfall request. Every
    operation that verifies cards takes the text from here.
    """
    from .archidekt import ArchidektClient, declared_bracket, deck_id, deck_name, to_decklist

    source = archidekt_client or ArchidektClient()
    identifier = deck_id(url)
    payload = source.deck(identifier)
    text = to_decklist(payload)
    return {
        "source": "archidekt",
        "deck_id": identifier,
        "name": deck_name(payload),
        "declared_bracket": declared_bracket(payload),
        "decklist": text,
        "parsed": read_deck(text),
    }


# --- Commander Spellbook operations -----------------------------------------


def card_combos(name: str, *, spellbook_client=None) -> dict:
    """Combos that use a given card."""
    from .spellbook import SpellbookClient, parse_variant

    source = spellbook_client or SpellbookClient()
    variants = source.variants_for_card(name)
    combos = [parse_variant(v) for v in variants]
    return {
        "card": name,
        "count": len(variants),
        "combos": [
            c | {"cards": list(c["cards"]), "produces": list(c["produces"])}
            for c in combos
        ],
    }


def deck_combos(
    text: str, *, client: ScryfallClient | None = None, spellbook_client=None
) -> dict:
    """Combos the deck actually assembles.

    Querying Spellbook once per card that *could* participate in a combo would
    require knowing the combos in advance, so this queries per distinct deck
    card instead and keeps only the variants whose every piece is in the deck.
    For a 100-card deck that is roughly 70 requests at 100ms apart, about 7
    seconds — accepted for now; no caching or concurrency.
    """
    from .spellbook import SpellbookClient, combos_in_deck

    deck = _resolved(text, client)
    names = [card.name for _, card in deck.cards] + [c.name for c in deck.commanders]
    source = spellbook_client or SpellbookClient()

    variants: list[dict] = []
    for name in names:
        variants.extend(source.variants_for_card(name))

    found = combos_in_deck(variants, names)
    return {
        "commanders": [c.name for c in deck.commanders],
        "count": len(found),
        "two_card_count": sum(1 for c in found if c["card_count"] == 2),
        "combos": [
            c | {"cards": list(c["cards"]), "produces": list(c["produces"])} for c in found
        ],
    }


# --- EDHREC operations ------------------------------------------------------


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

    The returned `game_changers_available` flag tells the caller whether
    `is_game_changer` on these candidates is trustworthy: a Game Changers
    outage degrades to every candidate reporting `False` rather than failing
    the call, and a caller enforcing a bracket allowance must be able to tell
    the difference between "verified clean" and "unverifiable."
    """
    from .edhrec import EdhrecClient, synergy_cards

    source = edhrec_client or EdhrecClient()
    payload = source.commander(name, variant=variant)
    candidates = synergy_cards(payload, limit=limit)
    if not candidates:
        return {
            "commander": name, "variant": variant, "count": 0, "cards": [],
            "game_changers_available": True,
        }

    scry = _client(client)
    verified, _ = scry.collection([c["name"] for c in candidates], strict=False)
    gc_available = True
    try:
        game_changers = scry.game_changers()
    except SourceUnavailable:
        # The audit and the candidate list are still useful without it, but the
        # caller must be able to tell that is_game_changer is unenforced.
        game_changers = frozenset()
        gc_available = False

    by_name = {}
    for p in verified:
        card = card_from_json(p, game_changers=game_changers)
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

    return {
        "commander": name, "variant": variant, "count": len(out), "cards": out,
        "game_changers_available": gc_available,
    }


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


def compare_to_average(
    text: str, *, client: ScryfallClient | None = None, edhrec_client=None
) -> dict:
    """Diff a decklist against EDHREC's consensus build of its commander.

    Answers the question no other operation here does: not "is this deck legal
    and well-proportioned", but "what does the typical build of this commander
    play that this one does not". The average deck is a popularity artefact, not
    a correct deck — `unique_to_yours` is where a deck's actual ideas live, and a
    low overlap is not by itself a fault.

    Each entry in `missing_from_yours` carries its `functions`, so the agent can
    see which gap it would fill and cross the diff with the audit instead of
    listing cards for their own sake.

    The average list is resolved with `strict=False`: it is a community source,
    and one name Scryfall cannot resolve must not abort the comparison. Those
    names are reported in `unresolved_average_names` rather than dropped
    silently.
    """
    from .edhrec import EdhrecClient, average_cards

    scry = _client(client)
    deck = _resolved(text, scry)
    if not deck.commanders:
        raise DeckStructureError(
            "No commander declared, so there is no average deck to compare "
            "against. Add a `Commander` section or a *CMDR* flag to the list."
        )
    commander = deck.commanders[0].name

    source = edhrec_client or EdhrecClient()
    average = average_cards(source.average_deck(commander))
    # Dedupe by name: the average list is already one row per card, but an
    # unofficial source must not be able to inflate the denominator.
    by_name: dict[str, dict] = {}
    for entry in average:
        by_name.setdefault(entry["name"].casefold(), entry)

    yours: dict[str, str] = {}
    for _, card in deck.cards:
        yours[card.name.casefold()] = card.name
        front, _, _ = card.name.partition("//")
        yours.setdefault(front.strip().casefold(), card.name)

    in_both = [e["name"] for key, e in by_name.items() if key in yours]
    missing_keys = [key for key in by_name if key not in yours]

    resolved: dict[str, Card] = {}
    unresolved: list[str] = []
    if missing_keys:
        wanted = [by_name[key]["name"] for key in missing_keys]
        payloads, not_found = scry.collection(wanted, strict=False)
        unresolved = list(not_found)
        game_changers = _game_changers(scry)
        for payload in payloads:
            card = card_from_json(payload, game_changers=game_changers)
            resolved[card.name.casefold()] = card
            front, _, _ = card.name.partition("//")
            resolved.setdefault(front.strip().casefold(), card)

    missing: list[dict] = []
    for key in missing_keys:
        entry = by_name[key]
        card = resolved.get(key)
        if card is None:
            continue
        row = _card_dict(card, classify(card))
        row["average_qty"] = entry["qty"]
        row["average_type"] = entry["type"]
        missing.append(row)

    # Walked from the deck rather than from `yours`, whose front-face aliases
    # would list a modal DFC under two names.
    average_names = set(by_name)
    unique_seen: set[str] = set()
    for _, card in deck.cards:
        aliases = {
            card.name.casefold(),
            card.name.partition("//")[0].strip().casefold(),
        }
        if aliases & average_names:
            continue
        unique_seen.add(card.name)
    unique = sorted(unique_seen)

    size = len(by_name)
    return {
        "commander": commander,
        "average_size": size,
        "in_both": sorted(in_both),
        "missing_from_yours": missing,
        "unique_to_yours": unique,
        "unresolved_average_names": unresolved,
        "overlap_pct": round(100 * len(in_both) / size, 1) if size else 0.0,
    }


# --- Suggestion operations --------------------------------------------------


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
        pool_result = commander_synergy(
            deck.commanders[0].name,
            variant=variant,
            limit=max(limit * 8, 80),
            client=scry,
            edhrec_client=edhrec_client,
        )
        pool = pool_result["cards"]
        if not pool_result.get("game_changers_available", True):
            degraded.append(
                "Scryfall Game Changers list unavailable, so the bracket "
                "Game Changer allowance could not be enforced"
            )
    except _SourceUnavailable as exc:
        degraded.append(exc.source)
        pool = []

    rule = RULES[target]
    gc_allowance = rule.game_changers_max
    # The command zone counts, exactly as brackets.check counts it: Tergrid,
    # Grand Arbiter Augustin IV and Braids, Cabal Minion are all Game Changers
    # and all legal commanders. Counting only the 99 computed a budget one too
    # large, so suggest offered a card that `bracket` then called non-compliant.
    gc_in_deck = sum(1 for _, c in deck.cards if c.is_game_changer) + sum(
        1 for c in deck.commanders if c.is_game_changer
    )
    gc_budget = None if gc_allowance is None else gc_allowance - gc_in_deck

    suggestions = []
    for candidate in pool:
        if candidate["name"].casefold() in present:
            continue
        if not set(candidate["color_identity"]) <= allowed:
            continue
        if candidate["legal_commander"] != "legal":
            continue
        if candidate["is_game_changer"]:
            if gc_budget is not None and gc_budget <= 0:
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
        if candidate["is_game_changer"] and gc_budget is not None:
            gc_budget -= 1

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


_STATUS_MARK = {"ok": "ok", "low": "LOW", "high": "HIGH"}


# --- Goldfish ---------------------------------------------------------------


def goldfish(
    text: str,
    goal: dict,
    *,
    games: int = DEFAULT_GAMES,
    turns: int = DEFAULT_TURN_CAP,
    seed: int = 1,
    disruption: bool = True,
    client: ScryfallClient | None = None,
) -> dict:
    """Play `games` auto games against the deck's goal file and report.

    A deck that fails validation is still simulated — goldfishing a draft is
    legitimate — and its violations come back under `warnings`.
    """
    deck = _resolved(text, client)
    report = simulate(deck, goal, games=games, turn_cap=turns, seed=seed,
                      disruption=disruption)
    report["warnings"] = _violations(validate(deck))
    return report


def goldfish_compare(
    before_text: str,
    after_text: str,
    goal: dict,
    *,
    games: int = DEFAULT_GAMES,
    turns: int = DEFAULT_TURN_CAP,
    seed: int = 1,
    disruption: bool = True,
    client: ScryfallClient | None = None,
) -> dict:
    """Simulate two versions of a deck on the same seeds: before, after, delta."""
    scry = _client(client)
    before, after = _resolved(before_text, scry), _resolved(after_text, scry)
    result = compare(before, after, goal, games=games, turn_cap=turns, seed=seed,
                     disruption=disruption)
    result["before"]["warnings"] = _violations(validate(before))
    result["after"]["warnings"] = _violations(validate(after))
    return result


def goldfish_new(
    text: str,
    goal: dict,
    *,
    turns: int = DEFAULT_TURN_CAP,
    seed: int = 1,
    game: int = 0,
    disruption: bool = True,
    client: ScryfallClient | None = None,
) -> dict:
    """Start one game for Claude to pilot. Returns the state and a view of it.

    Pilot game `game` of `seed` is dealt exactly as auto game `game` of the
    same seed: same shuffle, same disruption dice.
    """
    deck = _resolved(text, client)
    state = new_game(prepare(deck, goal), seed=f"{seed}-{game}", turn_cap=turns, disruption=disruption)
    return {"state": to_dict(state), "view": game_view(state)}


def goldfish_scan(text: str, goal: dict | None = None, *,
                  client: ScryfallClient | None = None) -> dict:
    """What the sim does with every card in the deck, and which still need a
    person's review before a goldfish can be trusted."""
    from . import card_rules

    return card_rules.scan(_resolved(text, client), goal)


def card_rule_show(name: str) -> dict:
    from . import card_rules

    return {"name": name, "entry": card_rules.load()["cards"].get(name)}


def card_rule_set(name: str, *, status: str, rule: dict | None, note: str,
                  client: ScryfallClient | None = None) -> dict:
    """Record a reviewed rule. The name is checked against Scryfall first, so a
    misspelled or invented card can never enter the library."""
    from . import card_rules

    real = lookup_card(name, client=client)["name"]
    try:
        entry = card_rules.record(real, status=status, rule=rule, note=note)
    except ValueError as exc:
        raise GoalError("card-rule", str(exc), [status]) from exc
    return {"name": real, "entry": entry}


def goldfish_step(state: dict, action: dict) -> dict:
    """Apply one action to a piloted game. Raises IllegalAction for a bad one."""
    after = apply(from_dict(state), action, in_place=True)
    return {"state": to_dict(after), "view": game_view(after)}


def game_view(state: GameState) -> dict:
    """What a pilot may see: no library order, which a player would not know."""
    cards = state.cards
    return {
        "turn": state.turn,
        "extra_turn": state.extra_turn,
        "extra_turns_pending": state.extra_turns_pending,
        "over": state.over,
        "hand": sorted(cards[i].name for i in state.hand),
        "battlefield": [
            {"name": p.name, "power": p.power, "tapped": p.tapped,
             "creature": p.is_creature, "land": p.is_land}
            for p in state.battlefield
        ],
        "command_zone": [{"name": cards[i].name, "tax": state.tax.get(i, 0)}
                         for i in state.command_zone],
        "graveyard": [cards[i].name for i in state.graveyard],
        "library_count": len(state.library),
        "mana_available": available_mana(state),
        "treasures": state.treasures,
        "opponent_life_lost": state.opponent_life_lost,
        "commander_damage": state.commander_damage,
        "checkpoints": dict(state.checkpoints),
        "win_by": state.win_by,
        "events": list(state.events),
        "pending_tutor": state.pending_tutor,
        "pending_put_back": state.pending_put_back,
        "loss_by": state.loss_by,
        "legal_actions": legal_actions(state),
        "log": state.log[-12:],
    }


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
