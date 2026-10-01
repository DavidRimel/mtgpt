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
from .errors import UnresolvedCards
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
