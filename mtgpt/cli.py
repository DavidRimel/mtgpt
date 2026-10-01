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
    python3 -m mtgpt.cli goldfish         --file deck.txt --goal deck.goal.json
    python3 -m mtgpt.cli goldfish-compare --file old.txt --file new.txt --goal deck.goal.json
    python3 -m mtgpt.cli goldfish-new     --file deck.txt --goal deck.goal.json --out game.json
    python3 -m mtgpt.cli goldfish-step    --state game.json --action '{"cast": "Sol Ring"}'

Output is JSON in a fixed envelope so results feed the next decision:

    {"ok": true,  "command": "audit", "data": {...}}
    {"ok": false, "command": "audit", "error": {"type": "...", "message": "...", ...}}

Errors are data, never prose only: UnresolvedCards carries the offending names.
"""

from __future__ import annotations

import argparse
import json
import os
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

    suggest = sub.add_parser("suggest", help="Propose cards to add, with reasons")
    src = suggest.add_mutually_exclusive_group()
    src.add_argument("--file")
    src.add_argument("--stdin", action="store_true")
    suggest.add_argument("--bracket", type=int, default=3, choices=[1, 2, 3, 4, 5])
    suggest.add_argument("--variant", choices=["budget", "expensive", "upgraded", "cedh"])
    suggest.add_argument("--limit", type=int, default=10)

    for name, help_text in (
        ("goldfish", "Simulate games and measure how the deck plays"),
        ("goldfish-new", "Start one game to pilot turn by turn"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        src = cmd.add_mutually_exclusive_group()
        src.add_argument("--file")
        src.add_argument("--stdin", action="store_true")
        _add_goldfish_options(cmd, games=name == "goldfish")
        if name == "goldfish-new":
            cmd.add_argument("--game", type=_non_negative_int, default=0,
                             help="Replay auto game N of --seed (default 0)")
            cmd.add_argument("--out", required=True, help="Where to write the game state")

    goldfish_compare = sub.add_parser(
        "goldfish-compare", help="Simulate two versions of a deck on the same seeds")
    goldfish_compare.add_argument(
        "--file", action="append", required=True, help="Pass twice: the deck before, then after")
    _add_goldfish_options(goldfish_compare, games=True)

    step = sub.add_parser("goldfish-step", help="Apply one action to a piloted game")
    step.add_argument("--state", required=True, help="The game file; rewritten in place")
    step.add_argument("--action", required=True, help='JSON, e.g. {"cast": "Sol Ring"}')

    return parser


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return number


def _add_goldfish_options(cmd, *, games: bool) -> None:
    cmd.add_argument("--goal", required=True, help="Path to the deck's goal file (JSON)")
    if games:
        cmd.add_argument("--games", type=_positive_int, default=1000)
    cmd.add_argument("--turns", type=_positive_int, default=10)
    cmd.add_argument("--seed", type=int, default=1)
    cmd.add_argument("--no-disruption", action="store_true",
                     help="Ignore the goal file's disruption block")


def _emit(command: str, data, *, ok: bool = True) -> None:
    key = "data" if ok else "error"
    print(json.dumps({"ok": ok, "command": command, key: data}, indent=2))


def _read_deck_text(args, command: str) -> str | None:
    """Return the decklist text, or None after emitting a user error."""
    if args.file:
        return _read_text_file(args.file, command)
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


def _read_text_file(path: str, command: str) -> str | None:
    """Return a UTF-8 file's text, or None after emitting a user error."""
    try:
        with open(path, encoding="utf-8") as handle:
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
                    f"{path} is not valid UTF-8 ({exc.reason} at byte "
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


_FAILED = object()  # a reader emitted its error envelope; None is never a value


def _read_json(path: str, command: str):
    """Return a JSON object from a file, or _FAILED after emitting a user error."""
    text = _read_text_file(path, command)
    return _FAILED if text is None else _parse_json(text, command, path)


def _parse_json(text: str, command: str, where: str):
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        _emit(command, {"type": "JSONDecodeError", "field": where,
                        "message": f"{where} is not valid JSON: {exc}"}, ok=False)
        return _FAILED
    if not isinstance(value, dict):
        _emit(command, {"type": "JSONDecodeError", "field": where,
                        "message": f"{where} must be a JSON object"}, ok=False)
        return _FAILED
    return value


def _write_json(path: str, data, command: str) -> bool:
    """Write atomically: a failure mid-write must not corrupt an existing file."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        _emit(command, {"type": "OSError", "message": str(exc)}, ok=False)
        return False
    return True


def _goldfish(args, command: str, client) -> int:
    """The four goldfish subcommands. Returns the exit code."""
    if command == "goldfish-step":
        state = _read_json(args.state, command)
        action = _FAILED if state is _FAILED else _parse_json(args.action, command, "--action")
        if action is _FAILED:
            return EXIT_USER_ERROR
        result = api.goldfish_step(state, action)
        if not _write_json(args.state, result["state"], command):
            return EXIT_USER_ERROR
        _emit(command, result["view"])
        return EXIT_OK

    goal = _read_json(args.goal, command)
    if goal is _FAILED:
        return EXIT_USER_ERROR
    options = {"turns": args.turns, "seed": args.seed,
               "disruption": not args.no_disruption, "client": client}
    if command == "goldfish-new":
        options["game"] = args.game

    if command == "goldfish-compare":
        if len(args.file) != 2:
            _emit(command, {"type": "MissingInput",
                            "message": "Pass --file twice: the deck before, then after."},
                  ok=False)
            return EXIT_USER_ERROR
        texts = [_read_text_file(path, command) for path in args.file]
        if None in texts:
            return EXIT_USER_ERROR
        _emit(command, api.goldfish_compare(*texts, goal, games=args.games, **options))
        return EXIT_OK

    text = _read_deck_text(args, command)
    if text is None:
        return EXIT_USER_ERROR
    if command == "goldfish":
        _emit(command, api.goldfish(text, goal, games=args.games, **options))
        return EXIT_OK
    result = api.goldfish_new(text, goal, **options)
    if not _write_json(args.out, result["state"], command):
        return EXIT_USER_ERROR
    _emit(command, result["view"])
    return EXIT_OK


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
        elif command.startswith("goldfish"):
            return _goldfish(args, command, client)
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
            elif command == "suggest":
                _emit(command, api.suggest_additions(
                    text, target=args.bracket, variant=args.variant,
                    limit=args.limit, client=client,
                ))
    except (UnresolvedCards, DeckStructureError, SourceUnavailable) as exc:
        _emit(command, api.error_payload(exc), ok=False)
        return EXIT_USER_ERROR
    except MtgptError as exc:
        _emit(command, api.error_payload(exc), ok=False)
        return EXIT_USER_ERROR

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
