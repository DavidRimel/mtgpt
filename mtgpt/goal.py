# mtgpt/goal.py
"""The per-deck goal file: what the commander's "thing" is, and what winning means.

Code cannot infer a commander's plan and Claude must not guess it, so the skill
asks the user and writes this file. An archetype supplies a default `thing` and
`win`; the file overrides either, and names the deck's engine cards with the
exact behavior the sim should give them.

Everything is validated against the deck before a game starts. A card name the
deck does not contain is a hard stop, never a silently inert override.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, fields
from functools import cached_property

from .errors import MtgptError
from .models import Function

ARCHETYPES = ("voltron", "go_wide", "aristocrats", "spellslinger", "combo", "big_mana", "custom")
TRIGGER_EVENTS = (
    "creature_dies", "creature_etb", "spell_cast", "instant_sorcery_cast", "upkeep", "attack",
)
ENGINE_TAGS = ("sac_outlet", "drain", "payoff", "finisher")
COUNT_TAGS = ENGINE_TAGS + ("creature", "equipment_aura") + tuple(f.value for f in Function)
NUMERIC_CONDITIONS = (
    "mana_available", "board_power", "cards_in_hand", "opponent_life_lost", "commander_damage",
)
PRIORITIES = ("engine", "hold")
#: Opponents in a Commander pod: 40 life each is 120 to win by damage or drain.
OPPONENTS = 3

#: (thing, win) per archetype. None means the goal file must supply it.
_DEFAULTS: dict[str, tuple[object, object]] = {
    "voltron": ({"all": ["commander", {"count": "equipment_aura", "min": 2}]},
                {"commander_damage": 21 * OPPONENTS}),
    "go_wide": ({"count": "creature", "min": 5}, {"opponent_life_lost": 40 * OPPONENTS}),
    "aristocrats": ({"all": ["commander", {"count": "sac_outlet", "min": 1},
                             {"count": "drain", "min": 1}]},
                    {"opponent_life_lost": 40 * OPPONENTS}),
    "spellslinger": ({"all": ["commander", {"count": "payoff", "min": 1}]},
                     {"opponent_life_lost": 40 * OPPONENTS}),
    "combo": (None, None),
    "big_mana": ({"mana_available": 10}, None),
    "custom": (None, None),
}
_GOAL_FIELDS = ("archetype", "commander_turn", "engine", "thing", "win", "disruption",
                "opponent_win")
#: What can stop an opponent's win attempt.
ANSWER_KINDS = ("removal", "counterspell", "stax")
_INT_SPEC_FIELDS = ("drain", "draw", "treasure", "tokens", "anthem", "mana")
#: A mana cost written in symbols: {3}, {W}, {C}, {X}, hybrid {W/U}, {2/W}, Phyrexian {B/P}.
_MANA_COST = re.compile(r"(?:\{(?:\d+|[WUBRGCXS]|[WUBRG2]/[WUBRGP])\})+")
_MANA_COLORS = frozenset("WUBRGC")
_BOOL_SPEC_FIELDS = ("sac_outlet", "payoff", "finisher", "stax")


class GoalError(MtgptError):
    """The goal file is malformed. `field` says where; `values` echoes what."""

    def __init__(self, field: str, message: str, values: Iterable = ()):
        self.field = field
        self.values = tuple(values)
        super().__init__(f"goal file {field}: {message}")


@dataclass(frozen=True)
class Condition:
    """One node of a `thing` or `win` condition tree."""

    kind: str
    children: tuple[Condition, ...] = ()
    #: The tag a `count` condition counts.
    key: str | None = None
    #: The threshold of a `count` or numeric condition.
    n: float = 0.0
    #: The cards a `cast` or `assembled` condition names.
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class EngineSpec:
    """Exact sim behavior for one engine card. Replaces its parsed effect."""

    on: str | None = None
    drain: int = 0
    draw: int = 0
    treasure: int = 0
    tokens: int = 0
    token_power: float = 1.0
    sac_outlet: bool = False
    payoff: bool = False
    finisher: bool = False
    #: A static hoser on the battlefield stops opponents' win attempts.
    stax: bool = False
    anthem: int = 0
    priority: str | None = None
    #: While this permanent is on the battlefield, any spell may be cast for
    #: this cost instead of its own (Jodah, Fist of Suns). Commander tax still
    #: applies on top.
    alt_cost: str | None = None
    #: Mana this permanent taps for each turn, and its colors (Bloom Tender).
    mana: int = 0
    mana_colors: frozenset[str] = frozenset()

    def has_tag(self, tag: str) -> bool:
        if tag == "drain":
            return self.drain > 0
        return tag in ENGINE_TAGS and bool(getattr(self, tag))


@dataclass(frozen=True)
class Disruption:
    """Per-turn chances, from `from_turn` on, of each disruption event."""

    commander_removal: float = 0.0
    board_wipe: float = 0.0
    from_turn: int = 4


@dataclass(frozen=True)
class OpponentWin:
    """A fast table: from `from_turn` on, each round an opponent tries to win,
    and you lose unless you hold (or have out) one of `answers`."""

    from_turn: int = 5
    answers: tuple[str, ...] = ANSWER_KINDS
    #: (fewest, most) rounds between attempts after the first; None is every round.
    every: tuple[int, int] | None = None


@dataclass(frozen=True)
class Goal:
    archetype: str
    commander_turn: int
    thing: Condition
    win: Condition
    engine: tuple[tuple[str, EngineSpec], ...] = ()
    disruption: Disruption | None = None
    opponent_win: OpponentWin | None = None

    @cached_property
    def _engine_map(self) -> dict[str, EngineSpec]:
        return dict(self.engine)

    def engine_for(self, name: str) -> EngineSpec | None:
        return self._engine_map.get(name)


def load_goal(data, *, deck_names: Iterable[str], commander_mv: float = 0.0) -> Goal:
    """Validate a goal file's parsed JSON against the deck it is for.

    `commander_mv` sets the default `commander_turn`: a 4-drop commander is on
    curve on turn 4.
    """
    if not isinstance(data, dict):
        raise GoalError("(root)", "must be a JSON object")
    unknown = sorted(set(data) - set(_GOAL_FIELDS))
    if unknown:
        raise GoalError("(root)", f"unknown field(s): {', '.join(unknown)}", unknown)

    archetype = data.get("archetype")
    if archetype not in ARCHETYPES:
        raise GoalError(
            "archetype", f"{archetype!r} is not one of {', '.join(ARCHETYPES)}", [archetype]
        )
    canon = _canonicalizer(deck_names)
    default_thing, default_win = _DEFAULTS[archetype]
    thing_raw = data.get("thing", default_thing)
    win_raw = data.get("win", default_win)
    if thing_raw is None:
        raise GoalError("thing", f"archetype {archetype!r} has no default; say what the commander's thing is")
    if win_raw is None:
        raise GoalError("win", f"archetype {archetype!r} has no default; say what winning looks like")

    turn = data.get("commander_turn", max(1, int(commander_mv)))
    if not isinstance(turn, int) or isinstance(turn, bool) or turn < 1:
        raise GoalError("commander_turn", "must be a whole number of turns, 1 or more", [turn])

    return Goal(
        archetype=archetype,
        commander_turn=turn,
        thing=_condition(thing_raw, "thing", canon),
        win=_condition(win_raw, "win", canon),
        engine=_engine(data.get("engine", {}), canon),
        disruption=_disruption(data["disruption"]) if "disruption" in data else None,
        opponent_win=_opponent_win(data["opponent_win"]) if "opponent_win" in data else None,
    )


def condition_names(cond: Condition) -> tuple[str, ...]:
    """Every card a condition names, in order, without repeats."""
    out: list[str] = []
    for name in cond.names:
        if name not in out:
            out.append(name)
    for child in cond.children:
        for name in condition_names(child):
            if name not in out:
                out.append(name)
    return tuple(out)


def describe(cond: Condition) -> str:
    """A short stable label, used to report which win condition fired."""
    if cond.kind == "commander":
        return "commander"
    if cond.kind == "count":
        return f"count:{cond.key}>={cond.n:g}"
    if cond.kind in NUMERIC_CONDITIONS:
        return f"{cond.kind}>={cond.n:g}"
    if cond.kind == "cast":
        return f"cast:{cond.names[0]}"
    if cond.kind == "assembled":
        return "assembled:" + "+".join(cond.names)
    return f"{cond.kind}(" + ", ".join(describe(c) for c in cond.children) + ")"


def _canonicalizer(deck_names: Iterable[str]):
    """Match names case-insensitively, and an MDFC by its front face."""
    index: dict[str, str] = {}
    for name in deck_names:
        index.setdefault(name.casefold(), name)
        index.setdefault(name.partition("//")[0].strip().casefold(), name)

    def canon(names, field: str) -> tuple[str, ...]:
        missing = [n for n in names if not isinstance(n, str) or n.casefold() not in index]
        if missing:
            raise GoalError(field, f"not in the deck: {', '.join(map(str, missing))}", missing)
        return tuple(index[n.casefold()] for n in names)

    return canon


def _condition(raw, field: str, canon) -> Condition:
    if raw == "commander":
        return Condition("commander")
    if not isinstance(raw, dict) or not raw:
        raise GoalError(field, f"{raw!r} is not a condition", [raw])
    if "count" in raw:
        extra = sorted(set(raw) - {"count", "min"})
        if extra:
            raise GoalError(field, f"unknown field(s) in count: {', '.join(extra)}", extra)
        tag = raw["count"]
        if tag not in COUNT_TAGS:
            raise GoalError(f"{field}.count", f"{tag!r} is not one of {', '.join(COUNT_TAGS)}", [tag])
        return Condition("count", key=tag, n=_number(raw.get("min", 1), f"{field}.min"))
    if len(raw) != 1:
        raise GoalError(field, f"a condition has exactly one key, got {sorted(raw)}", sorted(raw))

    ((kind, value),) = raw.items()
    where = f"{field}.{kind}"
    if kind in ("all", "any"):
        if not isinstance(value, list) or not value:
            raise GoalError(where, "must be a non-empty list")
        return Condition(kind, children=tuple(
            _condition(child, f"{where}[{i}]", canon) for i, child in enumerate(value)
        ))
    if kind in NUMERIC_CONDITIONS:
        return Condition(kind, n=_number(value, where))
    if kind == "cast":
        if not isinstance(value, str):
            raise GoalError(where, "must be one card name", [value])
        return Condition("cast", names=canon([value], where))
    if kind == "assembled":
        if not isinstance(value, list) or not value:
            raise GoalError(where, "must be a non-empty list of card names")
        return Condition("assembled", names=canon(value, where))
    raise GoalError(field, f"unknown condition {kind!r}", [kind])


def _engine(raw, canon) -> tuple[tuple[str, EngineSpec], ...]:
    if not isinstance(raw, dict):
        raise GoalError("engine", "must be an object keyed by card name")
    allowed = {f.name for f in fields(EngineSpec)}
    out = []
    for name, spec in raw.items():
        (card,) = canon([name], "engine")
        where = f"engine.{card}"
        if not isinstance(spec, dict):
            raise GoalError(where, "must be an object")
        unknown = sorted(set(spec) - allowed)
        if unknown:
            raise GoalError(where, f"unknown field(s): {', '.join(unknown)}", unknown)
        on = spec.get("on")
        if on is not None and on not in TRIGGER_EVENTS:
            raise GoalError(f"{where}.on", f"{on!r} is not one of {', '.join(TRIGGER_EVENTS)}", [on])
        priority = spec.get("priority")
        if priority is not None and priority not in PRIORITIES:
            raise GoalError(f"{where}.priority", f"{priority!r} is not one of {', '.join(PRIORITIES)}", [priority])
        ints = {}
        for key in _INT_SPEC_FIELDS:
            value = spec.get(key, 0)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise GoalError(f"{where}.{key}", "must be a whole number, 0 or more", [value])
            ints[key] = value
        bools = {}
        for key in _BOOL_SPEC_FIELDS:
            value = spec.get(key, False)
            if not isinstance(value, bool):
                raise GoalError(f"{where}.{key}", "must be true or false", [value])
            bools[key] = value
        if on is None and any(ints[k] for k in ("drain", "draw", "treasure", "tokens")):
            raise GoalError(f"{where}.on", "a trigger effect needs an `on` event")
        alt_cost = spec.get("alt_cost")
        if alt_cost is not None and not (isinstance(alt_cost, str) and _MANA_COST.fullmatch(alt_cost)):
            raise GoalError(f"{where}.alt_cost", "must be a mana cost in symbols, e.g. \"{W}{U}{B}{R}{G}\"",
                            [alt_cost])
        mana_colors = _mana_colors(spec.get("mana_colors", ""), f"{where}.mana_colors")
        if ints["mana"] and not mana_colors:
            raise GoalError(f"{where}.mana_colors", "`mana` needs the colors it makes, e.g. \"WUBRG\"")
        out.append((card, EngineSpec(
            on=on, priority=priority, alt_cost=alt_cost, mana_colors=mana_colors,
            token_power=_number(spec.get("token_power", 1), f"{where}.token_power"),
            **ints, **bools,
        )))
    return tuple(out)


def _mana_colors(value, field: str) -> frozenset[str]:
    if not isinstance(value, str) or not set(value.upper()) <= _MANA_COLORS:
        raise GoalError(field, "must be a string of mana symbols from WUBRGC, e.g. \"WUBRG\"", [value])
    return frozenset(value.upper())


def _opponent_win(raw) -> OpponentWin:
    if not isinstance(raw, dict):
        raise GoalError("opponent_win", "must be an object")
    unknown = sorted(set(raw) - {"from_turn", "answers", "every"})
    if unknown:
        raise GoalError("opponent_win", f"unknown field(s): {', '.join(unknown)}", unknown)
    from_turn = raw.get("from_turn", 5)
    if not isinstance(from_turn, int) or isinstance(from_turn, bool) or from_turn < 1:
        raise GoalError("opponent_win.from_turn", "must be a whole number of turns, 1 or more",
                        [from_turn])
    answers = raw.get("answers", list(ANSWER_KINDS))
    bad = [a for a in answers if a not in ANSWER_KINDS] if isinstance(answers, list) else [answers]
    if bad or not answers:
        raise GoalError("opponent_win.answers", f"must be a list drawn from {', '.join(ANSWER_KINDS)}",
                        bad)
    every = raw.get("every")
    if every is not None:
        if isinstance(every, int) and not isinstance(every, bool):
            every = [every, every]
        ok = (isinstance(every, list) and len(every) == 2
              and all(isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in every)
              and every[0] <= every[1])
        if not ok:
            raise GoalError("opponent_win.every", "must be a number of rounds, or [fewest, most]", [every])
        every = (every[0], every[1])
    return OpponentWin(from_turn=from_turn, answers=tuple(answers), every=every)


def _disruption(raw) -> Disruption:
    if not isinstance(raw, dict):
        raise GoalError("disruption", "must be an object")
    unknown = sorted(set(raw) - {"commander_removal", "board_wipe", "from_turn"})
    if unknown:
        raise GoalError("disruption", f"unknown field(s): {', '.join(unknown)}", unknown)
    chances = {}
    for key in ("commander_removal", "board_wipe"):
        value = _number(raw.get(key, 0), f"disruption.{key}")
        if value > 1:
            raise GoalError(f"disruption.{key}", "is a chance per turn, between 0 and 1", [value])
        chances[key] = value
    from_turn = raw.get("from_turn", 4)
    if not isinstance(from_turn, int) or isinstance(from_turn, bool) or from_turn < 1:
        raise GoalError("disruption.from_turn", "must be a whole number of turns, 1 or more", [from_turn])
    return Disruption(from_turn=from_turn, **chances)


def _number(value, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise GoalError(field, "must be a number, 0 or more", [value])
    return float(value)
