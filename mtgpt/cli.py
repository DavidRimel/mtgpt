# mtgpt/cli.py
"""Command-line surface over the agent-facing facade.

Each subcommand is one operation, independently callable:

    python3 -m mtgpt.cli card "Sol Ring"
    python3 -m mtgpt.cli search "o:'add one mana of any color' t:creature c:g" --limit 10
    python3 -m mtgpt.cli find     ramp --identity wubg --limit 10
    python3 -m mtgpt.cli cross-check recursion --identity wubrg
    python3 -m mtgpt.cli classify "Cultivate" "Demonic Tutor"
    python3 -m mtgpt.cli import   "https://archidekt.com/decks/2000000/"
    python3 -m mtgpt.cli read     --file deck.txt
    python3 -m mtgpt.cli validate --file deck.txt
    python3 -m mtgpt.cli audit    --file deck.txt
    python3 -m mtgpt.cli bracket  --file deck.txt --target 3
    python3 -m mtgpt.cli report   --file deck.txt --bracket 3 [--text] [--combos]
    python3 -m mtgpt.cli combos   --file deck.txt
    python3 -m mtgpt.cli compare  --file deck.txt
    python3 -m mtgpt.cli card-combos "Thassa's Oracle"

Every deck operation accepts --file, --stdin, or --url (an Archidekt link).

Output is JSON in a fixed envelope so results feed the next decision:

    {"ok": true,  "command": "audit", "data": {...}}
    {"ok": false, "command": "audit", "error": {"type": "...", "message": "...", ...}}

Errors are data, never prose only: UnresolvedCards carries the offending names.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import api, tagger
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

    find = sub.add_parser(
        "find", help="Find cards by community-curated function tag (Scryfall Tagger)"
    )
    find.add_argument(
        "function", metavar="FUNCTION", choices=tagger.vocabulary(),
        # Listed in the help rather than left to the metavar: these are the only
        # tags verified to resolve, and an unverified one comes back from
        # Scryfall as a 404 that reads like an empty result.
        help="One of: " + ", ".join(tagger.vocabulary()),
    )
    find.add_argument("--identity", help="Color identity to scope to, e.g. wubg")
    find.add_argument("--limit", type=int, default=25)
    find.add_argument("--query", help="Extra Scryfall query terms, ANDed in")
    find.add_argument(
        "--no-cross-check", dest="cross_check", action="store_false",
        help="Omit the comparison against mtgpt's own classification",
    )

    cross_check = sub.add_parser(
        "cross-check",
        help="Score classify.py against Scryfall Tagger in BOTH directions",
    )
    cross_check.add_argument(
        "function", metavar="FUNCTION", choices=tagger.vocabulary(),
        help="One of: " + ", ".join(tagger.vocabulary()),
    )
    cross_check.add_argument("--identity")
    cross_check.add_argument("--limit", type=int, default=25)
    cross_check.add_argument(
        "--direction", choices=["both", "recall", "precision"], default="both",
        help="recall: do we tag what the community tagged. precision: does the "
             "community tag what we tagged. Default both — one alone misleads.",
    )

    classify_cmd = sub.add_parser("classify", help="Tag several cards by function")
    classify_cmd.add_argument("names", nargs="+")

    import_cmd = sub.add_parser("import", help="Import a deck from an Archidekt URL")
    import_cmd.add_argument("url", help="Archidekt deck URL, /api/ URL, or bare deck id")

    for name, help_text in (
        ("read", "Parse a decklist without verifying it"),
        ("validate", "Check legality only"),
        ("audit", "Measure ratios, curve, and colored sources"),
        ("bracket", "Check bracket compliance only"),
        ("report", "Everything composed"),
        ("compare", "Diff the deck against EDHREC's average build"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        source = cmd.add_mutually_exclusive_group()
        source.add_argument("--file", help="Path to a decklist text file")
        source.add_argument("--stdin", action="store_true", help="Read the decklist from stdin")
        source.add_argument("--url", help="Archidekt deck URL to fetch the list from")
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
    src.add_argument("--url", help="Archidekt deck URL to fetch the list from")

    card_combos_cmd = sub.add_parser("card-combos", help="Combos that use one card")
    card_combos_cmd.add_argument("name")

    suggest = sub.add_parser("suggest", help="Propose cards to add, with reasons")
    src = suggest.add_mutually_exclusive_group()
    src.add_argument("--file")
    src.add_argument("--stdin", action="store_true")
    src.add_argument("--url", help="Archidekt deck URL to fetch the list from")
    suggest.add_argument("--bracket", type=int, default=3, choices=[1, 2, 3, 4, 5])
    suggest.add_argument("--variant", choices=["budget", "expensive", "upgraded", "cedh"])
    suggest.add_argument("--limit", type=int, default=10)

    return parser


def _emit(command: str, data, *, ok: bool = True) -> None:
    key = "data" if ok else "error"
    print(json.dumps({"ok": ok, "command": command, key: data}, indent=2))


def _read_deck_text(args, command: str) -> str | None:
    """Return the decklist text, or None after emitting a user error.

    `--url` fetches from Archidekt and then behaves exactly as `--file` does, so
    every deck operation works on a link without a second code path. A fetch
    failure raises SourceUnavailable, which `main`'s handler turns into the error
    envelope.
    """
    if getattr(args, "url", None):
        return api.import_deck(args.url)["decklist"]
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                return handle.read()
        except UnicodeDecodeError as exc:
            # UnicodeDecodeError is a ValueError, so the old `except OSError`
            # let it escape as a raw traceback with exit 1 — outside the JSON
            # envelope every other failure respects. A cp1252 export of a card
            # name with a curly apostrophe ("Urza's") and a UTF-16 file from
            # PowerShell 5's `> deck.txt` both land here.
            _emit(
                command,
                {
                    "type": "UnicodeDecodeError",
                    "message": (
                        f"{args.file} is not valid UTF-8 ({exc.reason} at byte "
                        f"{exc.start}). Re-save it as UTF-8 — in PowerShell use "
                        "`Out-File -Encoding utf8`, and in an editor choose "
                        '"UTF-8" rather than "UTF-16" or "ANSI". '
                        "Or pipe the text in with --stdin."
                    ),
                },
                ok=False,
            )
            return None
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
                "No decklist given. Pass --file <path>, --stdin, or --url "
                "<archidekt link>. Moxfield cannot be fetched — there, use "
                "Export and paste or save the text."
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
        elif command == "find":
            _emit(command, api.find_cards(
                args.function, identity=args.identity, limit=args.limit,
                extra_query=args.query, cross_check=args.cross_check, client=client))
        elif command == "cross-check":
            if args.direction == "recall":
                _emit(command, api.find_cards(
                    args.function, identity=args.identity, limit=args.limit,
                    client=client))
            elif args.direction == "precision":
                _emit(command, api.check_classifier(
                    args.function, identity=args.identity, limit=args.limit,
                    client=client))
            else:
                _emit(command, api.cross_check_function(
                    args.function, identity=args.identity, limit=args.limit,
                    client=client))
        elif command == "classify":
            _emit(command, api.classify_cards(args.names, client=client))
        elif command == "import":
            _emit(command, api.import_deck(args.url, client=client))
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
            elif command == "compare":
                _emit(command, api.compare_to_average(text, client=client))
            elif command == "suggest":
                _emit(command, api.suggest_additions(
                    text, target=args.bracket, variant=args.variant,
                    limit=args.limit, client=client,
                ))
    except (UnresolvedCards, DeckStructureError, SourceUnavailable) as exc:
        _emit(command, api.error_payload(exc), ok=False)
        return EXIT_USER_ERROR
    except ValueError as exc:
        # Bad input the type system cannot reject: an unverified function tag, a
        # colour-identity typo, a URL with no deck id in it. A user error, so it
        # belongs in the envelope rather than escaping as a traceback.
        _emit(command, api.error_payload(exc), ok=False)
        return EXIT_USER_ERROR
    except MtgptError as exc:
        _emit(command, api.error_payload(exc), ok=False)
        return EXIT_USER_ERROR

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
