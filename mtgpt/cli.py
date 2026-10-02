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
    python3 -m mtgpt.cli goldfish         --file deck.txt --goal deck.goal.json
    python3 -m mtgpt.cli goldfish-compare --file old.txt --file new.txt --goal deck.goal.json
    python3 -m mtgpt.cli scorecard        --file best.txt [--file candidate.txt] --goal goal.json --bracket 3
    python3 -m mtgpt.cli goldfish-new     --file deck.txt --goal deck.goal.json --out game.json
    python3 -m mtgpt.cli goldfish-step    --state game.json --action '{"cast": "Sol Ring"}'
    python3 -m mtgpt.cli project          list | new | status | save | best | stage | note | log

Every deck operation accepts --file, --stdin, or --url (an Archidekt link).

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
from pathlib import Path

from . import api, projects, tagger
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

    score_cmd = sub.add_parser(
        "scorecard", help="Tuning targets for a deck, or keep/revert for a candidate")
    score_cmd.add_argument(
        "--file", action="append", required=True,
        help="Once to score a deck; twice to judge the second against the first")
    score_cmd.add_argument("--bracket", type=int, required=True, choices=[1, 2, 3, 4, 5])
    score_cmd.add_argument("--combos", help="The commander's cached card-combos JSON")
    _add_goldfish_options(score_cmd, games=True)

    scan = sub.add_parser("goldfish-scan", help="How the sim models every card; what needs review")
    src = scan.add_mutually_exclusive_group()
    src.add_argument("--file")
    src.add_argument("--stdin", action="store_true")
    scan.add_argument("--goal", help="The deck's goal file, so its overrides are shown")

    rule = sub.add_parser("card-rule", help="Show or record a card's reviewed goldfish rule")
    rule.add_argument("name")
    rule.add_argument("--status", choices=["parsed", "override", "ignored"],
                      help="Record an entry; omit to show the current one")
    rule.add_argument("--rule", help='Engine override JSON, for --status override')
    rule.add_argument("--note", default="", help="Why: what the card does in a goldfish")

    step = sub.add_parser("goldfish-step", help="Apply one action to a piloted game")
    step.add_argument("--state", required=True, help="The game file; rewritten in place")
    step.add_argument("--action", required=True, help='JSON, e.g. {"cast": "Sol Ring"}')

    project = sub.add_parser("project", help="Local deck projects (decks/, never committed)")
    psub = project.add_subparsers(dest="project_command", required=True)
    root_opt = argparse.ArgumentParser(add_help=False)
    root_opt.add_argument("--root", default=str(projects.DEFAULT_ROOT),
                          help="Folder holding deck projects (default: the repo's decks/)")
    psub.add_parser("list", parents=[root_opt], help="Every deck project")
    new = psub.add_parser("new", parents=[root_opt], help="Start a project from a decklist")
    new.add_argument("--name", required=True)
    new.add_argument("--bracket", type=int, required=True, choices=[1, 2, 3, 4, 5])
    new.add_argument("--source", help="Where the list came from (a URL)")
    src = new.add_mutually_exclusive_group()
    src.add_argument("--file")
    src.add_argument("--stdin", action="store_true")
    status_cmd = psub.add_parser("status", parents=[root_opt], help="Stage, versions, log tail")
    status_cmd.add_argument("slug")
    save = psub.add_parser("save", parents=[root_opt], help="Write the next version")
    save.add_argument("slug")
    save.add_argument("--note", default="", help="What changed, for the log")
    src = save.add_mutually_exclusive_group()
    src.add_argument("--file")
    src.add_argument("--stdin", action="store_true")
    best = psub.add_parser("best", parents=[root_opt], help="Mark a version as the best so far")
    best.add_argument("slug")
    best.add_argument("version")
    best.add_argument("--primary", type=float, help="Its wins-by-target-round, for `list`")
    stage = psub.add_parser("stage", parents=[root_opt], help="Record the tuning stage")
    stage.add_argument("slug")
    stage.add_argument("stage", choices=projects.STAGES)
    for name, help_text in (("note", "Record how a real game went"),
                            ("log", "Append markdown to the tuning log")):
        cmd = psub.add_parser(name, parents=[root_opt], help=help_text)
        cmd.add_argument("slug")
        cmd.add_argument("text")

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
    """Return the decklist text, or None after emitting a user error.

    `--url` fetches from Archidekt and then behaves exactly as `--file` does, so
    every deck operation works on a link without a second code path. A fetch
    failure raises SourceUnavailable, which `main`'s handler turns into the error
    envelope.
    """
    if getattr(args, "url", None):
        return api.import_deck(args.url)["decklist"]
    if args.file:
        return _read_text_file(args.file, command)
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


def _scorecard(args, command: str, client) -> int:
    if len(args.file) not in (1, 2):
        _emit(command, {"type": "MissingInput",
                        "message": "Pass --file once (score) or twice (best, then candidate)."},
              ok=False)
        return EXIT_USER_ERROR
    goal = _read_json(args.goal, command)
    if goal is _FAILED:
        return EXIT_USER_ERROR
    combos = None
    if args.combos:
        cached = _read_json(args.combos, command)
        if cached is _FAILED:
            return EXIT_USER_ERROR
        # `card-combos ... > combos.json` saves the whole envelope; accept that
        # or a bare {"combos": [...]}.
        body = cached.get("data", cached) if isinstance(cached, dict) else None
        combos = body.get("combos") if isinstance(body, dict) else None
        if not isinstance(combos, list):
            _emit(command, {"type": "MissingInput", "field": "--combos",
                            "message": f'{args.combos} is not card-combos output: '
                                       'expected a "combos" list'}, ok=False)
            return EXIT_USER_ERROR
    texts = [_read_text_file(path, command) for path in args.file]
    if None in texts:
        return EXIT_USER_ERROR
    _emit(command, api.scorecard(texts, goal, bracket=args.bracket, combos=combos,
                                 games=args.games, turns=args.turns, seed=args.seed,
                                 disruption=not args.no_disruption, client=client))
    return EXIT_OK


def _card_rule(args, command: str, client) -> int:
    if args.status is None:
        _emit(command, api.card_rule_show(args.name))
        return EXIT_OK
    rule = None
    if args.rule is not None:
        rule = _parse_json(args.rule, command, "--rule")
        if rule is None:
            return EXIT_USER_ERROR
    _emit(command, api.card_rule_set(args.name, status=args.status, rule=rule,
                                     note=args.note, client=client))
    return EXIT_OK


def _project(args, command: str) -> int:
    root = Path(args.root)
    action = args.project_command
    if action == "list":
        _emit(command, projects.list_projects(root))
    elif action in ("new", "save"):
        text = _read_deck_text(args, command)
        if text is None:
            return EXIT_USER_ERROR
        if action == "new":
            _emit(command, projects.create(root, args.name, text, bracket=args.bracket,
                                           source=args.source))
        else:
            _emit(command, projects.save(root, args.slug, text, note=args.note))
    elif action == "status":
        _emit(command, projects.status(root, args.slug))
    elif action == "best":
        _emit(command, projects.set_best(root, args.slug, args.version, primary=args.primary))
    elif action == "stage":
        _emit(command, projects.set_stage(root, args.slug, args.stage))
    else:
        (projects.note if action == "note" else projects.log)(root, args.slug, args.text)
        _emit(command, {"slug": args.slug, "logged": True})
    return EXIT_OK


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
        elif command == "card-rule":
            return _card_rule(args, command, client)
        elif command == "project":
            return _project(args, command)
        elif command == "goldfish-scan":
            goal = None
            if args.goal:
                goal = _read_json(args.goal, command)
                if goal is None:
                    return EXIT_USER_ERROR
            text = _read_deck_text(args, command)
            if text is None:
                return EXIT_USER_ERROR
            _emit(command, api.goldfish_scan(text, goal, client=client))
        elif command == "scorecard":
            return _scorecard(args, command, client)
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
