# tests/test_cli.py
import io
import json
import pathlib

from mtgpt import cli

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_every_subcommand_is_registered():
    parser = cli.build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    assert actions, "expected a subcommand dest named 'command'"
    assert set(actions[0].choices) == {
        "card", "search", "find", "classify", "import", "read", "validate", "audit",
        "bracket", "report", "compare", "synergy", "themes", "combos", "card-combos",
        "suggest",
    }


def test_card_emits_a_success_envelope(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "lookup_card",
                        lambda name, client=None: {"name": name, "functions": ["ramp"]})
    assert cli.main(["card", "Sol Ring"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "card"
    assert payload["data"]["name"] == "Sol Ring"


def test_unresolved_card_emits_a_machine_readable_error(monkeypatch, capsys):
    from mtgpt.errors import UnresolvedCards

    def boom(name, client=None):
        raise UnresolvedCards(["Blatantly Fake Card"])

    monkeypatch.setattr(cli.api, "lookup_card", boom)
    code = cli.main(["card", "Blatantly Fake Card"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["ok"] is False
    assert payload["error"]["type"] == "UnresolvedCards"
    # The offending names are data, so the agent can act on them.
    assert payload["error"]["names"] == ["Blatantly Fake Card"]


def test_search_passes_limit_through(monkeypatch):
    seen = {}

    def fake(query, limit=25, client=None):
        seen["query"], seen["limit"] = query, limit
        return {"query": query, "count": 0, "cards": []}

    monkeypatch.setattr(cli.api, "search_cards", fake)
    assert cli.main(["search", "c:g t:sorcery", "--limit", "5"]) == 0
    assert seen == {"query": "c:g t:sorcery", "limit": 5}


def test_classify_accepts_several_names(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "classify_cards",
                        lambda names, client=None: {n: ["ramp"] for n in names})
    assert cli.main(["classify", "Sol Ring", "Arcane Signet"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["data"]) == {"Sol Ring", "Arcane Signet"}


def test_read_needs_no_network(capsys):
    assert cli.main(["read", "--file", str(FIXTURES / "sample_deck.txt")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["total_cards"] == 41


def test_deck_subcommands_accept_stdin(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "audit_deck", lambda text, client=None: {"land_count": 36})
    monkeypatch.setattr("sys.stdin", io.StringIO("1 Sol Ring\n36 Forest\n"))
    assert cli.main(["audit", "--stdin"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["land_count"] == 36


def test_bracket_passes_target_through(monkeypatch):
    seen = {}

    def fake(text, target=3, client=None):
        seen["target"] = target
        return {"target": target, "target_name": "Core"}

    monkeypatch.setattr(cli.api, "bracket_check", fake)
    cli.main(["bracket", "--file", str(FIXTURES / "sample_deck.txt"), "--target", "2"])
    assert seen["target"] == 2


def test_report_text_mode_renders_instead_of_json(monkeypatch, capsys):
    monkeypatch.setattr(cli.api, "full_report",
                        lambda text, target=3, client=None, combos=False: {"stub": True})
    monkeypatch.setattr(cli.api, "render_report", lambda report: "RENDERED REPORT")
    assert cli.main(["report", "--file", str(FIXTURES / "sample_deck.txt"), "--text"]) == 0
    out = capsys.readouterr().out
    assert "RENDERED REPORT" in out
    assert "{" not in out


def test_missing_deck_input_is_a_user_error(capsys):
    assert cli.main(["audit"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "Export" in payload["error"]["message"]


def test_unparseable_deck_is_reported_as_data(capsys, tmp_path):
    path = tmp_path / "deck.txt"
    path.write_text("not a decklist\n")
    assert cli.main(["read", "--file", str(path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == "DeckStructureError"


# --- Non-UTF-8 decklists stay inside the JSON envelope (Important 4) ---------


def test_a_cp1252_decklist_is_a_user_error_not_a_traceback(capsys, tmp_path):
    """A Windows editor's "ANSI" encoding turns Urza's curly apostrophe into a
    byte that is not valid UTF-8. `UnicodeDecodeError` is a `ValueError`, so the
    old `except OSError` let it escape as a raw traceback with exit 1 — outside
    the envelope every other failure respects. Card names are full of apostrophes.
    """
    path = tmp_path / "deck.txt"
    path.write_bytes("Deck\n1 Urza’s Tower\n".encode("cp1252"))
    assert cli.main(["read", "--file", str(path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "UnicodeDecodeError"
    # The message has to tell the user what to do about it.
    assert "UTF-8" in payload["error"]["message"]


def test_a_utf16_decklist_is_a_user_error_not_a_traceback(capsys, tmp_path):
    """UTF-16 is what Windows PowerShell 5 produces from `... > deck.txt`."""
    path = tmp_path / "deck.txt"
    path.write_bytes("Deck\n1 Sol Ring\n".encode("utf-16"))
    assert cli.main(["read", "--file", str(path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "UnicodeDecodeError"


def test_a_utf8_bom_decklist_still_parses(capsys, tmp_path):
    """A UTF-8 BOM is valid UTF-8, so it must not be caught by the above:
    deckparse already strips it."""
    path = tmp_path / "deck.txt"
    path.write_bytes("﻿Deck\n1 Sol Ring\n36 Forest\n".encode("utf-8"))
    assert cli.main(["read", "--file", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["total_cards"] == 37


# --- find -------------------------------------------------------------------


def test_find_passes_every_option_through(monkeypatch):
    seen = {}

    def fake(function, identity=None, limit=25, extra_query=None,
             cross_check=True, client=None):
        seen.update(function=function, identity=identity, limit=limit,
                    extra_query=extra_query, cross_check=cross_check)
        return {"function": function, "count": 0, "cards": []}

    monkeypatch.setattr(cli.api, "find_cards", fake)
    assert cli.main(["find", "ramp", "--identity", "wubg", "--limit", "10",
                     "--query", "cmc<=2"]) == 0
    assert seen == {"function": "ramp", "identity": "wubg", "limit": 10,
                    "extra_query": "cmc<=2", "cross_check": True}


def test_find_cross_check_is_on_by_default_and_can_be_turned_off(monkeypatch):
    seen = {}

    def fake(function, identity=None, limit=25, extra_query=None,
             cross_check=True, client=None):
        seen["cross_check"] = cross_check
        return {"function": function, "count": 0, "cards": []}

    monkeypatch.setattr(cli.api, "find_cards", fake)
    cli.main(["find", "ramp"])
    assert seen["cross_check"] is True
    cli.main(["find", "ramp", "--no-cross-check"])
    assert seen["cross_check"] is False


def test_find_rejects_an_unverified_tag_at_the_parser(capsys):
    # argparse lists the valid choices, so a typo is answered with the
    # vocabulary rather than an empty result from Scryfall.
    import pytest

    with pytest.raises(SystemExit):
        cli.main(["find", "stax"])
    assert "ramp" in capsys.readouterr().err


def test_a_bad_colour_identity_lands_in_the_error_envelope(monkeypatch, capsys):
    def boom(function, **kwargs):
        raise ValueError("Unknown colour identity letter(s) 'z'")

    monkeypatch.setattr(cli.api, "find_cards", boom)
    assert cli.main(["find", "ramp", "--identity", "wubz"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "ValueError"


# --- import -----------------------------------------------------------------


def test_import_emits_a_success_envelope(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.api, "import_deck",
        lambda url, client=None: {"source": "archidekt", "deck_id": "2000000",
                                  "decklist": "Deck\n1 Sol Ring\n"},
    )
    assert cli.main(["import", "https://archidekt.com/decks/2000000/"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "import"
    assert payload["data"]["deck_id"] == "2000000"


def test_an_archidekt_outage_emits_the_error_envelope(monkeypatch, capsys):
    from mtgpt.errors import SourceUnavailable

    def boom(url, client=None):
        raise SourceUnavailable("Archidekt", "connection refused")

    monkeypatch.setattr(cli.api, "import_deck", boom)
    assert cli.main(["import", "2000000"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "SourceUnavailable"
    assert payload["error"]["source"] == "Archidekt"


# --- --url on the deck operations -------------------------------------------


def test_every_deck_operation_accepts_a_url():
    parser = cli.build_parser()
    subparsers = [a for a in parser._actions if a.dest == "command"][0].choices
    for command in ("read", "validate", "audit", "bracket", "report", "compare",
                    "suggest", "combos"):
        options = {s for action in subparsers[command]._actions for s in action.option_strings}
        assert "--url" in options, command


def test_url_is_mutually_exclusive_with_file():
    import pytest

    with pytest.raises(SystemExit):
        cli.main(["audit", "--file", "deck.txt", "--url", "2000000"])


def test_url_fetches_the_list_then_proceeds_exactly_as_file_does(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli.api, "import_deck",
        lambda url, client=None: {"decklist": "Deck\n1 Sol Ring\n"},
    )
    monkeypatch.setattr(cli.api, "audit_deck",
                        lambda text, client=None: seen.setdefault("text", text) and {})
    assert cli.main(["audit", "--url", "https://archidekt.com/decks/2000000/"]) == 0
    assert seen["text"] == "Deck\n1 Sol Ring\n"


def test_the_missing_input_message_mentions_url(capsys):
    assert cli.main(["audit"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == "MissingInput"
    assert "--url" in payload["error"]["message"]


# --- compare ----------------------------------------------------------------


def test_compare_reads_the_deck_and_emits_an_envelope(monkeypatch, capsys, tmp_path):
    path = tmp_path / "deck.txt"
    path.write_text("Deck\n1 Sol Ring\n", encoding="utf-8")
    monkeypatch.setattr(
        cli.api, "compare_to_average",
        lambda text, client=None: {"commander": "Atraxa", "overlap_pct": 42.0},
    )
    assert cli.main(["compare", "--file", str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "compare"
    assert payload["data"]["overlap_pct"] == 42.0


def test_an_edhrec_outage_during_compare_emits_the_error_envelope(
    monkeypatch, capsys, tmp_path
):
    from mtgpt.errors import SourceUnavailable

    path = tmp_path / "deck.txt"
    path.write_text("Deck\n1 Sol Ring\n", encoding="utf-8")

    def boom(text, client=None):
        raise SourceUnavailable("EDHREC", "connection refused")

    monkeypatch.setattr(cli.api, "compare_to_average", boom)
    assert cli.main(["compare", "--file", str(path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["source"] == "EDHREC"
