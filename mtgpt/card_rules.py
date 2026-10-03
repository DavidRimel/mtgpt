# mtgpt/card_rules.py
"""The card rules library: reviewed goldfish rules that carry across decks.

Every card a deck scan reviews is recorded once, with what the sim should do
with it, so the next deck that plays it starts with that knowledge instead of
re-deriving it. An entry has a status:

* ``parsed``   — the effect parser already models the card correctly.
* ``override`` — the card needs an explicit rule: an engine-override object,
  validated exactly like a goal file's ``engine`` entry.
* ``ignored``  — the card does nothing in a goldfish (removal, counters,
  effects on opponents); the note says why.

At game setup, ``override`` rules apply to any card in the deck the goal file
does not override itself: the goal file wins, then the library, then the
parsed card text.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from .goal import _engine
from .models import ResolvedDeck

#: The library shipped with mtgpt. Tests point DEFAULT_PATH elsewhere.
SHIPPED_PATH = Path(__file__).parent / "data" / "card_rules.json"
DEFAULT_PATH = SHIPPED_PATH
STATUSES = ("parsed", "override", "ignored")


def load(path: Path | None = None) -> dict:
    """The library as {"version": 1, "cards": {name: entry}}; empty if absent."""
    path = Path(path or DEFAULT_PATH)
    if not path.exists():
        return {"version": 1, "cards": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def validate_rule(name: str, rule) -> None:
    """Raise GoalError unless `rule` is a valid engine override for `name`."""
    _engine({name: rule}, lambda names, field: tuple(names))


def _validate_entry(name: str, entry: dict) -> None:
    """Validate a single card entry. Raises ValueError or GoalError on any issue."""
    if not isinstance(entry, dict):
        raise ValueError(f"{name}: entry must be a dict")
    status = entry.get("status")
    if status not in STATUSES:
        raise ValueError(f"{name}: status must be one of {', '.join(STATUSES)}, got {status!r}")
    if status == "override":
        if "rule" not in entry:
            raise ValueError(f"{name}: override requires a rule")
        if not isinstance(entry["rule"], dict):
            raise ValueError(f"{name}: rule must be a dict")
        validate_rule(name, entry["rule"])
    else:  # parsed or ignored
        if "rule" in entry:
            raise ValueError(f"{name}: {status!r} entry must not have a rule")


def record(name: str, *, status: str, rule, note: str, path: Path | None = None) -> dict:
    """Add or replace one card's entry, validated, and save the library."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}, got {status!r}")
    if status == "override":
        if not isinstance(rule, dict):
            raise ValueError("an override needs a rule object")
        validate_rule(name, rule)
    elif rule is not None:
        raise ValueError(f"a {status!r} entry takes no rule")
    path = Path(path or DEFAULT_PATH)
    data = load(path)
    entry = {"status": status, "note": note, "reviewed": datetime.date.today().isoformat()}
    if rule is not None:
        entry["rule"] = rule
    data["cards"][name] = entry
    _save(path, data)
    return entry


def merge(other_path: Path, path: Path | None = None) -> dict:
    """Add another library's rules that this one lacks. A card both libraries
    know but judge differently is listed as a conflict and left untouched."""
    other_path = Path(other_path)
    if not other_path.exists():
        raise ValueError(f"{other_path} does not exist")
    if not other_path.is_file():
        raise ValueError(f"{other_path} is not a file")

    path = Path(path or DEFAULT_PATH)
    mine = load(path)
    theirs_raw = json.loads(other_path.read_text(encoding="utf-8"))

    # Validate incoming library structure before mutating anything
    if not isinstance(theirs_raw, dict):
        raise ValueError("incoming file must contain a JSON object")
    if "cards" not in theirs_raw:
        raise ValueError("incoming file must have a 'cards' key")
    if not isinstance(theirs_raw["cards"], dict):
        raise ValueError("'cards' must be a dict")

    # Validate all entries before any modifications
    for name, entry in theirs_raw["cards"].items():
        _validate_entry(name, entry)

    # Now that validation passed, merge the entries
    theirs = theirs_raw
    added, unchanged, conflicts = [], 0, []
    for name, entry in sorted(theirs["cards"].items()):
        current = mine["cards"].get(name)
        if current is None:
            mine["cards"][name] = entry
            added.append(name)
        elif _same(current, entry):
            unchanged += 1
        else:
            conflicts.append({"name": name, "mine": current, "theirs": entry})
    if added:
        _save(path, mine)
    return {"added": added, "unchanged": unchanged, "conflicts": conflicts}


def _same(a: dict, b: dict) -> bool:
    return a.get("status") == b.get("status") and a.get("rule") == b.get("rule")


def _save(path: Path, data: dict) -> None:
    data["cards"] = dict(sorted(data["cards"].items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def engine_rules(names, path: Path | None = None) -> dict:
    """{name: rule} for the library's overrides among `names`."""
    cards = load(path)["cards"]
    return {name: cards[name]["rule"] for name in names
            if name in cards and cards[name]["status"] == "override"}


def scan(deck: ResolvedDeck, goal_raw: dict | None = None, path: Path | None = None) -> dict:
    """Every distinct card: what the sim does with it now, and its library entry.

    `needs_review` lists the cards a person must look at before the goldfish
    can be trusted: everything without a library entry, except basic lands and
    lands that only tap for mana.
    """
    from .goldfish.engine import prepare

    setup = prepare(deck, goal_raw or {"archetype": "custom", "thing": "commander",
                                       "win": {"opponent_life_lost": 10 ** 6}})
    library = load(path)["cards"]
    goal_engine = (goal_raw or {}).get("engine", {})
    texts = {c.name: c.oracle_text for c in deck.commanders}
    texts.update({c.name: c.oracle_text for _, c in deck.cards})
    rows, needs_review, seen = [], [], set()
    for info in setup.cards:
        if info.name in seen:
            continue
        seen.add(info.name)
        effect = {k: (sorted(v) if isinstance(v, frozenset) else v)
                  for k, v in info.effect.__dict__.items()
                  if v not in (0, 0.0, False, None, frozenset(), "") and k != "power"}
        entry = library.get(info.name)
        source = ("goal" if info.name in goal_engine
                  else "library" if entry and entry["status"] == "override" else "text")
        rows.append({
            "name": info.name,
            "type_line": info.type_line,
            "oracle_text": texts.get(info.name, ""),
            "model": effect,
            "model_from": source,
            "unmodeled": info.unmodeled,
            "library": entry["status"] if entry else None,
            "library_note": entry["note"] if entry else None,
        })
        if entry is None and not _plain_land(info):
            needs_review.append(info.name)
    return {"cards": rows, "needs_review": needs_review,
            "reviewed": len(rows) - len(needs_review), "total": len(rows)}


def _plain_land(info) -> bool:
    """A basic, or any land the sim gives mana (duals, triomes, fetchlands as
    the basic they find). Extra land abilities (The World Tree's sacrifice,
    Cavern of Souls) are not modeled and not flagged: they rarely change a
    goldfish."""
    return info.is_land and (info.is_basic or bool(info.effect.land_colors))
