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
    python3 -m mtgpt.cli report   --file deck.txt --bracket 3 [--text] [--combos]
    python3 -m mtgpt.cli combos   --file deck.txt
    python3 -m mtgpt.cli card-combos "Thassa's Oracle"

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
            cmd.add_argument(
                "--combos", action="store_true",
                help="Fetch Commander Spellbook combos and enforce them in the bracket check",
            )

    synergy = sub.add_parser("synergy", help="EDHREC candidate cards for a commander")
    synergy.add_argument("commander")
    synergy.add_argument("--variant", choices=["budget", "expensive", "upgraded", "cedh"])
    synergy.add_argument("--limit", type=int, default=40)

    themes_cmd = sub.add_parser("themes", help="How a commander is usually built")
    themes_cmd.add_argument("commander")

    combos_cmd = sub.add_parser("combos", help="Combos a decklist assembles")
    src = combos_cmd.add_mutually_exclusive_group()
    src.add_argument("--file")
    src.add_argument("--stdin", action="store_true")

    card_combos_cmd = sub.add_parser("card-combos", help="Combos that use one card")
    card_combos_cmd.add_argument("name")

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
        elif command == "synergy":
            _emit(command, api.commander_synergy(
                args.commander, variant=args.variant, limit=args.limit, client=client))
        elif command == "themes":
            _emit(command, api.commander_themes(args.commander))
        elif command == "card-combos":
            _emit(command, api.card_combos(args.name))
        elif command == "combos":
            text = _read_deck_text(args, command)
            if text is None:
                return EXIT_USER_ERROR
            _emit(command, api.deck_combos(text, client=client))
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
                report = api.full_report(
                    text, target=args.bracket, client=client, combos=args.combos
                )
                if args.text:
                    print(api.render_report(report))
                else:
                    _emit(command, report)
    except (UnresolvedCards, DeckStructureError, SourceUnavailable) as exc:
        _emit(command, _error_payload(exc), ok=False)
        return EXIT_USER_ERROR
    except MtgptError as exc:
        _emit(command, _error_payload(exc), ok=False)
        return EXIT_USER_ERROR

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
