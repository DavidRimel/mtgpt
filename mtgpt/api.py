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
from .errors import SourceUnavailable, UnresolvedCards
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
    payloads, _ = _client(client).collection([name])
    wanted = name.casefold()
    for payload in payloads:
        candidate = payload.get("name", "")
        front, _, _ = candidate.partition("//")
        faces = payload.get("card_faces") or ()
        aliases = {candidate.casefold(), front.strip().casefold()}
        if faces:
            aliases.add((faces[0].get("name") or "").strip().casefold())
        if wanted in aliases:
            card = card_from_json(payload)
            return _card_dict(card, classify(card))
    raise UnresolvedCards([name])


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
    gc_in_deck = sum(1 for _, c in deck.cards if c.is_game_changer)
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
