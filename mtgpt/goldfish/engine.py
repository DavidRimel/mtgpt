# mtgpt/goldfish/engine.py
"""The goldfish engine: game state, legal actions, and what actions do.

One engine, two pilots. The heuristic policy (policy.py) and Claude in pilot
mode both choose from `legal_actions(state)` and hand the choice to `apply`.
Neither can do anything the engine would not allow, so a pilot game and an auto
game measure the same deck under the same rules.

The engine knows only what effects.py and the goal file tell it: mana, cards,
tutors, power, and the goal file's engine triggers. There are no opponents.
Combat is unblocked, and disruption is a dice roll at the start of a turn.

State is plain data and round-trips through JSON (`to_dict` / `from_dict`), RNG
included, so pilot mode stays stateless across CLI calls.
"""

from __future__ import annotations

import copy
import random
from collections import Counter
from dataclasses import asdict, dataclass, field, replace

from ..classify import classify
from ..effects import SimEffect, effect_from_dict, effect_of, effect_to_dict, is_unmodeled
from ..errors import DeckStructureError, MtgptError
from ..goal import OPPONENTS, Condition, Goal, condition_names, describe, load_goal
from ..models import ResolvedDeck
from .mana import Unit, parse_cost, plan_payment

DEFAULT_TURN_CAP = 10
#: Engine triggers can feed each other — a token maker on creature_etb makes a
#: creature, which enters. Past this many resolutions in one turn the engine
#: stops firing them and logs it, rather than looping forever.
TRIGGER_CAP = 200
_ANY = frozenset("WUBRG")
_PERMANENT_TYPES = ("Artifact", "Creature", "Enchantment", "Planeswalker", "Battle")
_SPEND_CATEGORIES = ("ramp", "engine", "other")


class InvalidGameState(MtgptError):
    """A saved game that cannot be read back."""


class IllegalAction(MtgptError):
    """An action that is not in `legal_actions(state)`. Carries both."""

    def __init__(self, action, legal):
        self.action = action
        self.legal = list(legal)
        super().__init__(f"illegal action {action!r}; legal actions are {self.legal!r}")


@dataclass(frozen=True)
class CardInfo:
    """One physical card, with everything the engine needs precomputed."""

    name: str
    mana_cost: str
    mana_value: float
    type_line: str
    is_land: bool
    is_mdfc_land: bool
    is_creature: bool
    is_permanent: bool
    is_commander: bool
    is_basic: bool
    functions: tuple[str, ...]
    effect: SimEffect
    unmodeled: bool = False


@dataclass
class Permanent:
    #: Index into GameState.cards; None for a token.
    card: int | None
    name: str
    power: float
    is_creature: bool
    #: Turn it entered. A creature cannot attack or tap for mana that turn.
    entered: int
    tapped: bool = False
    #: True for a land, including an MDFC played as its land face.
    is_land: bool = False


@dataclass(frozen=True)
class Setup:
    """A deck and goal prepared once and shared by every game played from it."""

    cards: tuple[CardInfo, ...]
    commanders: tuple[int, ...]
    goal: Goal
    #: The goal file as given, with `commander_turn` filled in, for serialization.
    goal_raw: dict


@dataclass
class GameState:
    cards: tuple[CardInfo, ...]
    goal: Goal
    goal_raw: dict
    turn_cap: int
    disruption: bool
    rng: random.Random
    #: Disruption's own dice, so matched seeds roll matched disruption whatever
    #: the shuffles did.
    dice: random.Random
    library: list[int] = field(default_factory=list)
    hand: list[int] = field(default_factory=list)
    battlefield: list[Permanent] = field(default_factory=list)
    graveyard: list[int] = field(default_factory=list)
    command_zone: list[int] = field(default_factory=list)
    #: Commander tax by card index: 2 per time it has left the battlefield.
    tax: dict[int, int] = field(default_factory=dict)
    turn: int = 0
    over: bool = False
    land_played: bool = False
    #: Mana floating this main phase, one entry per mana.
    pool: list[frozenset[str]] = field(default_factory=list)
    treasures: int = 0
    #: Set while a tutor waits for its choice: the tutor's restriction.
    pending_tutor: str | None = None
    triggers_this_turn: int = 0
    opponent_life_lost: float = 0.0
    commander_damage: float = 0.0
    cast_names: list[str] = field(default_factory=list)
    commander_cast_turn: int | None = None
    checkpoints: dict[str, int | None] = field(
        default_factory=lambda: {"commander": None, "thing": None, "win": None})
    win_by: str | None = None
    mulligans: int = 0
    spent_this_turn: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(_SPEND_CATEGORIES, 0))
    #: Mana spent and wasted on the turns before the commander was cast.
    pre_commander: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(_SPEND_CATEGORIES + ("unspent",), 0))
    #: One entry per finished turn: {"turn", "mana", "lands"}.
    per_turn: list[dict] = field(default_factory=list)
    #: One entry per turn the thing was online: interaction held in hand.
    thing_turns: list[dict] = field(default_factory=list)
    #: Disruption events: {"turn", "kind", "stopped", "by"}.
    events: list[dict] = field(default_factory=list)
    late_reason: str | None = None
    log: list[str] = field(default_factory=list)


# --- Setup -----------------------------------------------------------------


def prepare(deck: ResolvedDeck, goal_raw: dict) -> Setup:
    """Parse every card's effect and validate the goal against the deck, once."""
    if not deck.commanders:
        raise DeckStructureError("goldfish needs a commander; the decklist has none")
    identity = deck.command_zone_identity
    names = [c.name for c in deck.commanders] + [c.name for _, c in deck.cards]
    goal = load_goal(goal_raw, deck_names=names,
                     commander_mv=min(c.mana_value for c in deck.commanders))
    cards = [_info(c, identity, True) for c in deck.commanders]
    cards += [_info(c, identity, False) for c in deck.iter_cards()]
    cards = [_overridden(c) if goal.engine_for(c.name) is not None else c for c in cards]
    return Setup(
        cards=tuple(cards),
        commanders=tuple(range(len(deck.commanders))),
        goal=goal,
        goal_raw={**goal_raw, "commander_turn": goal.commander_turn},
    )


def new_game(setup: Setup, *, seed, turn_cap: int = DEFAULT_TURN_CAP,
             disruption: bool = True) -> GameState:
    """Shuffle, mulligan, and begin turn 1. `seed` may be an int or a string."""
    state = GameState(
        cards=setup.cards,
        goal=setup.goal,
        goal_raw=setup.goal_raw,
        turn_cap=turn_cap,
        disruption=disruption,
        rng=random.Random(seed),
        dice=random.Random(f"{seed}-disruption"),
        library=[i for i in range(len(setup.cards)) if i not in setup.commanders],
        command_zone=list(setup.commanders),
    )
    _mulligan(state)
    _begin_turn(state)
    return state


def _overridden(info: CardInfo) -> CardInfo:
    """An engine override replaces the parsed effect: only the card's body
    (power, equipment bonus) and land face survive, so its text's draw, mana,
    and interaction no longer apply on top of the override."""
    e = info.effect
    return replace(info, effect=SimEffect(power=e.power, power_bonus=e.power_bonus,
                                          land_colors=e.land_colors,
                                          enters_tapped=e.enters_tapped))


def _info(card, identity, is_commander: bool) -> CardInfo:
    effect = effect_of(card, identity)
    front = card.front_type_line
    return CardInfo(
        name=card.name,
        mana_cost=card.mana_cost,
        mana_value=card.mana_value,
        type_line=card.type_line,
        is_land=card.is_land,
        is_mdfc_land=card.is_mdfc_land,
        is_creature="Creature" in front,
        is_permanent=not card.is_land and any(t in front for t in _PERMANENT_TYPES),
        is_commander=is_commander,
        is_basic=card.is_basic_land,
        functions=tuple(sorted(f.value for f in classify(card))),
        effect=effect,
        unmodeled=is_unmodeled(card, effect),
    )


# --- Reading the state -----------------------------------------------------


def legal_actions(state: GameState) -> list[dict]:
    """Every action `apply` accepts right now. Always ends with pass, unless a
    tutor is waiting for its choice or the game is over."""
    if state.over:
        return []
    if state.pending_tutor is not None:
        names = sorted({state.cards[i].name for i in state.library
                        if _tutor_matches(state.cards[i], state.pending_tutor)})
        return [{"tutor": name} for name in names] or [{"tutor": None}]

    actions: list[dict] = []
    if not state.land_played:
        for name in _distinct(state, state.hand, lambda c: c.is_land or c.is_mdfc_land):
            actions.append({"play_land": name})
    units = _units(state)
    for idx in _first_of_each(state, state.hand + state.command_zone):
        if not state.cards[idx].is_land and _payment(state, idx, units) is not None:
            actions.append({"cast": state.cards[idx].name})
    if _has_tag(state, "sac_outlet"):
        for name in sorted({p.name for p in state.battlefield if p.is_creature}):
            actions.append({"sacrifice": name})
    actions.append({"pass": True})
    return actions


def available_mana(state: GameState) -> int:
    """Mana that could be spent right now, Treasures included."""
    return len(_units(state))


def production(state: GameState) -> int:
    """Mana the board makes per turn, tapped or not, summoning sickness aside."""
    return sum(len(_produces(state, p, ignore_sickness=True)) for p in state.battlefield)


def evaluate(state: GameState, cond: Condition) -> bool:
    kind = cond.kind
    if kind == "all":
        return all(evaluate(state, c) for c in cond.children)
    if kind == "any":
        return any(evaluate(state, c) for c in cond.children)
    if kind == "commander":
        return _commanders_on_board(state) != []
    if kind == "count":
        return _count_tag(state, cond.key) >= cond.n
    if kind == "mana_available":
        return production(state) + state.treasures >= cond.n
    if kind == "board_power":
        return board_power(state) >= cond.n
    if kind == "cards_in_hand":
        return len(state.hand) >= cond.n
    if kind == "opponent_life_lost":
        return state.opponent_life_lost >= cond.n
    if kind == "commander_damage":
        return state.commander_damage >= cond.n
    if kind == "cast":
        return cond.names[0] in state.cast_names
    if kind == "assembled":
        present = {p.name for p in state.battlefield} | {state.cards[i].name for i in state.hand}
        return all(name in present for name in cond.names)
    raise ValueError(f"unknown condition kind {kind!r}")


def win_label(state: GameState) -> str | None:
    """Which win condition holds, as a `describe` label; None if none does."""
    win = state.goal.win
    if win.kind == "any":
        for child in win.children:
            if evaluate(state, child):
                return describe(child)
        return None
    return describe(win) if evaluate(state, win) else None


def board_power(state: GameState) -> float:
    return sum(_attack_power(state, p) for p in state.battlefield if p.is_creature)


def held_counts(state: GameState) -> dict[str, int]:
    """Interaction in hand: removal (spot removal and sweepers), protection,
    counterspells."""
    counts = {"removal": 0, "protection": 0, "counterspell": 0}
    for idx in state.hand:
        held = state.cards[idx].effect.held
        if held & {"removal", "sweeper"}:
            counts["removal"] += 1
        if "protection" in held:
            counts["protection"] += 1
        if "counterspell" in held:
            counts["counterspell"] += 1
    return counts


def find_card(state: GameState, name: str, zone: list[int]) -> int | None:
    for idx in zone:
        if state.cards[idx].name == name:
            return idx
    return None


# --- Changing the state ----------------------------------------------------


def apply(state: GameState, action: dict, *, in_place: bool = False) -> GameState:
    """Apply one legal action. Returns a new state unless `in_place`.

    The auto runner plays in place for speed; pilot mode and the policy's
    look-ahead work on copies.
    """
    legal = legal_actions(state)
    if action not in legal:
        raise IllegalAction(action, legal)
    # The card table and goal are frozen, so the copy shares them: copying a
    # hundred CardInfos per look-ahead is most of a copy's cost.
    s = state if in_place else copy.deepcopy(
        state, {id(state.cards): state.cards, id(state.goal): state.goal})
    if "play_land" in action:
        _play_land(s, action["play_land"])
    elif "cast" in action:
        _cast(s, action["cast"])
    elif "tutor" in action:
        _tutor(s, action["tutor"])
    elif "sacrifice" in action:
        _sacrifice(s, action["sacrifice"])
    else:
        _end_turn(s)
    return s


def _play_land(s: GameState, name: str) -> None:
    idx = find_card(s, name, s.hand)
    s.hand.remove(idx)
    card = s.cards[idx]
    tapped = card.effect.enters_tapped if card.is_land else False
    s.battlefield.append(Permanent(card=idx, name=name, power=0.0, is_creature=False,
                                   entered=s.turn, tapped=tapped, is_land=True))
    s.land_played = True
    s.log.append(f"T{s.turn}: play {name}" + (" (tapped)" if tapped else ""))


def _cast(s: GameState, name: str) -> None:
    from_hand = find_card(s, name, s.hand)
    idx = from_hand if from_hand is not None else find_card(s, name, s.command_zone)
    units = _units(s)
    plan = _payment(s, idx, units)
    _spend(s, plan, units)
    (s.hand if from_hand is not None else s.command_zone).remove(idx)
    card = s.cards[idx]
    s.spent_this_turn[_spend_category(s, idx)] += len(plan)
    s.cast_names.append(name)
    if card.is_commander and s.commander_cast_turn is None:
        s.commander_cast_turn = s.turn
    s.log.append(f"T{s.turn}: cast {name} ({len(plan)} mana)")
    _fire(s, "spell_cast")
    if "Instant" in card.type_line or "Sorcery" in card.type_line:
        _fire(s, "instant_sorcery_cast")
    _resolve(s, idx)


def _resolve(s: GameState, idx: int) -> None:
    card = s.cards[idx]
    effect = card.effect
    if card.is_permanent:
        perm = Permanent(card=idx, name=card.name, power=effect.power,
                         is_creature=card.is_creature, entered=s.turn)
        s.battlefield.append(perm)
        if card.is_creature:
            _fire(s, "creature_etb", exclude=perm)
    else:
        s.graveyard.append(idx)
    s.pool.extend([_ANY] * effect.mana_once)
    s.treasures += effect.treasure_once
    for _ in range(effect.fetch_battlefield):
        _fetch_land(s, to_battlefield=True, tapped=effect.fetch_tapped)
    for _ in range(effect.fetch_hand):
        _fetch_land(s, to_battlefield=False, tapped=False)
    _draw(s, effect.draw_once)
    if effect.tutor and any(_tutor_matches(s.cards[i], effect.tutor) for i in s.library):
        s.pending_tutor = effect.tutor


def _tutor(s: GameState, name: str | None) -> None:
    s.pending_tutor = None
    if name is not None:
        idx = find_card(s, name, s.library)
        s.library.remove(idx)
        s.hand.append(idx)
        s.log.append(f"T{s.turn}: tutor {name}")
    s.rng.shuffle(s.library)


def _sacrifice(s: GameState, name: str) -> None:
    perm = next(p for p in s.battlefield if p.is_creature and p.name == name)
    s.log.append(f"T{s.turn}: sacrifice {name}")
    _kill(s, [perm])


def _fetch_land(s: GameState, *, to_battlefield: bool, tapped: bool) -> None:
    """Take the basic land that adds a color the deck has least of."""
    basics = [i for i in s.library if s.cards[i].is_basic]
    if not basics:
        return
    have = Counter(c for p in s.battlefield if p.is_land and p.card is not None
                   for c in s.cards[p.card].effect.land_colors)
    idx = min(basics, key=lambda i: min((have[c] for c in s.cards[i].effect.land_colors), default=0))
    s.library.remove(idx)
    if to_battlefield:
        s.battlefield.append(Permanent(card=idx, name=s.cards[idx].name, power=0.0,
                                       is_creature=False, entered=s.turn, tapped=tapped,
                                       is_land=True))
    else:
        s.hand.append(idx)


def _draw(s: GameState, n: int) -> None:
    for _ in range(n):
        if s.library:
            s.hand.append(s.library.pop(0))


def _kill(s: GameState, perms: list[Permanent]) -> None:
    """Move permanents to the graveyard (a commander to the command zone, with
    tax), then fire one creature_dies per creature. Payoffs dying in the same
    event still see it, as Blood Artist does."""
    for perm in perms:
        s.battlefield.remove(perm)
        if perm.card is None:
            continue
        if s.cards[perm.card].is_commander:
            s.command_zone.append(perm.card)
            s.tax[perm.card] = s.tax.get(perm.card, 0) + 2
        else:
            s.graveyard.append(perm.card)
    for perm in perms:
        if perm.is_creature:
            _fire(s, "creature_dies", also=perms)


def _fire(s: GameState, event: str, *, exclude: Permanent | None = None,
          also: list[Permanent] = ()) -> None:
    watchers = [p for p in list(s.battlefield) + list(also)
                if p is not exclude and p.card is not None]
    for perm in watchers:
        spec = s.goal.engine_for(s.cards[perm.card].name)
        if spec is None or spec.on != event:
            continue
        if s.triggers_this_turn >= TRIGGER_CAP:
            if s.triggers_this_turn == TRIGGER_CAP:
                s.log.append(f"T{s.turn}: trigger cap ({TRIGGER_CAP}) reached; stopped firing")
                s.triggers_this_turn += 1
            return
        s.triggers_this_turn += 1
        s.opponent_life_lost += spec.drain * OPPONENTS
        _draw(s, spec.draw)
        s.treasures += spec.treasure
        for _ in range(spec.tokens):
            token = Permanent(card=None, name="Token", power=spec.token_power,
                              is_creature=True, entered=s.turn)
            s.battlefield.append(token)
            _fire(s, "creature_etb", exclude=token)


# --- Turn structure --------------------------------------------------------


def _mulligan(s: GameState) -> None:
    """London mulligan; the first is free. Keep 3-5 lands, or 2 lands with a
    ramp spell of mana value 2 or less. Never below 5 cards."""
    s.rng.shuffle(s.library)
    _draw(s, 7)
    while not _keepable(s) and 7 - s.mulligans >= 5:
        s.mulligans += 1
        s.library.extend(s.hand)
        s.hand = []
        s.rng.shuffle(s.library)
        _draw(s, 7)
    for _ in range(max(0, s.mulligans - 1)):
        _bottom_one(s)


def _keepable(s: GameState) -> bool:
    lands = sum(1 for i in s.hand if s.cards[i].is_land or s.cards[i].is_mdfc_land)
    if 3 <= lands <= 5:
        return True
    return lands == 2 and any(
        s.cards[i].effect.is_ramp and s.cards[i].mana_value <= 2 and not s.cards[i].is_land
        for i in s.hand)


def _bottom_one(s: GameState) -> None:
    def is_land(i):
        return s.cards[i].is_land or s.cards[i].is_mdfc_land

    if not s.hand:
        return
    lands = [i for i in s.hand if is_land(i)]
    spells = [i for i in s.hand if not is_land(i)]
    if len(lands) > 4 or not spells:
        idx = lands[0]
    else:
        idx = max(spells, key=lambda i: s.cards[i].mana_value)
    s.hand.remove(idx)
    s.library.append(idx)


def _begin_turn(s: GameState) -> None:
    s.turn += 1
    s.land_played = False
    s.pool = []
    s.triggers_this_turn = 0
    s.spent_this_turn = dict.fromkeys(_SPEND_CATEGORIES, 0)
    for perm in s.battlefield:
        perm.tapped = False
    for perm in list(s.battlefield):
        if perm.card is not None:
            _draw(s, s.cards[perm.card].effect.draw_per_turn)
    _fire(s, "upkeep")
    if s.turn > 1:
        _draw(s, 1)
    _disrupt(s)


def _disrupt(s: GameState) -> None:
    """Roll both events every eligible turn, whether or not they can land, so
    two decks played on the same seed see the same dice."""
    d = s.goal.disruption
    if not s.disruption or d is None or s.turn < d.from_turn:
        return
    removal = s.dice.random() < d.commander_removal
    wipe = s.dice.random() < d.board_wipe
    if removal and _commanders_on_board(s):
        _event(s, "commander_removal")
    if wipe and any(not p.is_land for p in s.battlefield):
        _event(s, "board_wipe")


def _event(s: GameState, kind: str) -> None:
    if kind == "commander_removal":
        guard = _protection_on_board(s)
        if guard is not None:
            s.events.append({"turn": s.turn, "kind": kind, "stopped": True, "by": guard.name})
            s.log.append(f"T{s.turn}: {kind} stopped by {guard.name} on the battlefield")
            return
    answer = _answer_in_hand(s, kind)
    if answer is not None:
        s.hand.remove(answer)
        s.graveyard.append(answer)
        s.events.append({"turn": s.turn, "kind": kind, "stopped": True,
                         "by": s.cards[answer].name})
        s.log.append(f"T{s.turn}: {kind} stopped by {s.cards[answer].name}")
        return
    s.events.append({"turn": s.turn, "kind": kind, "stopped": False, "by": None})
    s.log.append(f"T{s.turn}: {kind}")
    if kind == "commander_removal":
        _kill(s, _commanders_on_board(s)[:1])
    else:
        # Lands and noncreature mana rocks survive; everything else goes.
        _kill(s, [p for p in s.battlefield if not p.is_land and not (
            not p.is_creature and p.card is not None and s.cards[p.card].effect.mana)])


def _protection_on_board(s: GameState) -> Permanent | None:
    """A protection permanent (Lightning Greaves, Mother of Runes) guards the
    commander from removal without being used up. It does not stop a wipe."""
    for perm in s.battlefield:
        if not perm.is_land and perm.card is not None and "protection" in s.cards[perm.card].effect.held:
            return perm
    return None


def _answer_in_hand(s: GameState, kind: str) -> int | None:
    """Protection first, then a counterspell. A wipe needs protection that
    survives it: indestructible or phasing."""
    for idx in s.hand:
        effect = s.cards[idx].effect
        if "protection" in effect.held and (kind == "commander_removal" or effect.wipe_proof):
            return idx
    for idx in s.hand:
        if "counterspell" in s.cards[idx].effect.held:
            return idx
    return None


def _end_turn(s: GameState) -> None:
    _combat(s)
    available = sum(s.spent_this_turn.values()) + sum(1 for u in _units(s) if u.kind != "treasure")
    if s.commander_cast_turn is None:
        for key in _SPEND_CATEGORIES:
            s.pre_commander[key] += s.spent_this_turn[key]
        s.pre_commander["unspent"] += max(0, available - sum(s.spent_this_turn.values()))
    s.per_turn.append({"turn": s.turn, "mana": production(s),
                       "lands": sum(1 for p in s.battlefield if p.is_land)})

    if s.checkpoints["commander"] is None and _commanders_on_board(s):
        s.checkpoints["commander"] = s.turn
    if s.turn == s.goal.commander_turn and s.commander_cast_turn is None:
        s.late_reason = _late_reason(s)
    if evaluate(s, s.goal.thing):
        if s.checkpoints["thing"] is None:
            s.checkpoints["thing"] = s.turn
        s.thing_turns.append({"turn": s.turn, **held_counts(s)})
    label = win_label(s)
    if label is not None:
        s.checkpoints["win"] = s.turn
        s.win_by = label
        s.over = True
        s.log.append(f"T{s.turn}: win ({label})")
    elif s.turn >= s.turn_cap:
        s.over = True
    else:
        _begin_turn(s)


def _combat(s: GameState) -> None:
    """Unblocked combat. An `attack` trigger fires once per combat, not once
    per attacking creature."""
    attackers = [p for p in s.battlefield if p.is_creature and p.entered < s.turn]
    if not attackers:
        return
    _fire(s, "attack")
    damage = 0.0
    for perm in attackers:
        hit = _attack_power(s, perm)
        damage += hit
        if perm.card is not None and s.cards[perm.card].is_commander:
            s.commander_damage += hit
    s.opponent_life_lost += damage
    s.log.append(f"T{s.turn}: attack for {damage:g}")
    # A sac outlet with a death payoff out turns spare tokens into triggers.
    if _has_tag(s, "sac_outlet") and any(
            p.card is not None and (spec := s.goal.engine_for(s.cards[p.card].name))
            and spec.on == "creature_dies" for p in s.battlefield):
        tokens = [p for p in s.battlefield if p.card is None and p.is_creature]
        if tokens:
            _kill(s, tokens)


def _late_reason(s: GameState) -> str:
    """Why the commander was not cast by its target turn."""
    idx = min(s.command_zone, default=None, key=lambda i: s.cards[i].mana_value)
    if idx is None:
        return "spent_elsewhere"
    generic, pips = parse_cost(s.cards[idx].mana_cost)
    generic += s.tax.get(idx, 0)
    every_source = [Unit(colors, "land", i) for i, p in enumerate(s.battlefield)
                    for colors in _produces(s, p, ignore_sickness=True)]
    if len(every_source) < generic + len(pips):
        lands = sum(1 for p in s.battlefield if p.is_land)
        return "land_light" if lands < s.goal.commander_turn else "no_mana"
    if plan_payment(every_source, generic, pips) is None:
        return "color_screw"
    return "spent_elsewhere"


# --- Mana ------------------------------------------------------------------


def _produces(s: GameState, perm: Permanent, *, ignore_sickness: bool = False) -> list[frozenset[str]]:
    if perm.card is None:
        return []
    effect = s.cards[perm.card].effect
    if perm.is_land:
        return [effect.land_colors] if effect.land_colors else []
    if perm.is_creature and perm.entered >= s.turn and not ignore_sickness:
        return []
    return [effect.mana_colors] * effect.mana


def _units(s: GameState) -> list[Unit]:
    units = [Unit(colors, "pool", i) for i, colors in enumerate(s.pool)]
    for i, perm in enumerate(s.battlefield):
        if not perm.tapped:
            kind = "land" if perm.is_land else "rock"
            units.extend(Unit(colors, kind, i) for colors in _produces(s, perm))
    units.extend(Unit(_ANY, "treasure", -1) for _ in range(s.treasures))
    return units


def _payment(s: GameState, idx: int, units: list[Unit]) -> list[int] | None:
    generic, pips = parse_cost(s.cards[idx].mana_cost)
    return plan_payment(units, generic + s.tax.get(idx, 0), pips)


def _spend(s: GameState, plan: list[int], units: list[Unit]) -> None:
    """Tap what the plan uses. A source tapped for part of its mana floats the
    rest: Sol Ring tapped for one leaves one in the pool."""
    chosen = [units[i] for i in plan]
    used_pool = {u.ref for u in chosen if u.kind == "pool"}
    used_from = Counter(u.ref for u in chosen if u.kind in ("land", "rock"))
    leftovers: list[frozenset[str]] = []
    for ref, used in used_from.items():
        perm = s.battlefield[ref]
        leftovers.extend(_produces(s, perm)[used:])
        perm.tapped = True
    s.pool = [c for i, c in enumerate(s.pool) if i not in used_pool] + leftovers
    s.treasures -= sum(1 for u in chosen if u.kind == "treasure")


# --- Helpers ---------------------------------------------------------------


def _spend_category(s: GameState, idx: int) -> str:
    card = s.cards[idx]
    if card.effect.is_ramp:
        return "ramp"
    goal = s.goal
    if goal.engine_for(card.name) is not None or card.name in (
            condition_names(goal.thing) + condition_names(goal.win)):
        return "engine"
    return "other"


def _commanders_on_board(s: GameState) -> list[Permanent]:
    return [p for p in s.battlefield if p.card is not None and s.cards[p.card].is_commander]


def _attack_power(s: GameState, perm: Permanent) -> float:
    anthem = sum(spec.anthem for p in s.battlefield if p.card is not None
                 and (spec := s.goal.engine_for(s.cards[p.card].name)))
    power = perm.power + anthem
    if perm.card is not None and s.cards[perm.card].is_commander:
        power += sum(s.cards[p.card].effect.power_bonus for p in s.battlefield
                     if p.card is not None)
    return max(0.0, power)


def _has_tag(s: GameState, tag: str) -> bool:
    return _count_tag(s, tag) > 0


def _count_tag(s: GameState, tag: str) -> int:
    if tag == "creature":
        return sum(1 for p in s.battlefield if p.is_creature)
    count = 0
    for perm in s.battlefield:
        if perm.card is None:
            continue
        card = s.cards[perm.card]
        if tag == "equipment_aura":
            count += any(t in card.type_line for t in ("Equipment", "Aura"))
            continue
        spec = s.goal.engine_for(card.name)
        if spec is not None and spec.has_tag(tag):
            count += 1
        elif tag in card.functions:
            count += 1
    return count


def _tutor_matches(card: CardInfo, restriction: str) -> bool:
    if restriction == "any":
        return True
    line = card.type_line.lower()
    return any(word in line for word in restriction.split("|"))


def _distinct(s: GameState, zone: list[int], keep) -> list[str]:
    return sorted({s.cards[i].name for i in zone if keep(s.cards[i])})


def _first_of_each(s: GameState, zone: list[int]) -> list[int]:
    seen: dict[str, int] = {}
    for idx in zone:
        seen.setdefault(s.cards[idx].name, idx)
    return [seen[name] for name in sorted(seen)]


# --- Serialization ---------------------------------------------------------


def to_dict(s: GameState) -> dict:
    """The whole state as JSON-ready data, RNG included."""
    version, internal, gauss = s.rng.getstate()
    return {
        "cards": [{**{k: v for k, v in asdict(c).items() if k != "effect"},
                   "functions": list(c.functions), "effect": effect_to_dict(c.effect)}
                  for c in s.cards],
        "goal": s.goal_raw,
        "turn_cap": s.turn_cap,
        "disruption": s.disruption,
        "rng": [version, list(internal), gauss],
        "dice": _dump_rng(s.dice),
        "library": list(s.library),
        "hand": list(s.hand),
        "battlefield": [asdict(p) for p in s.battlefield],
        "graveyard": list(s.graveyard),
        "command_zone": list(s.command_zone),
        "tax": {str(k): v for k, v in s.tax.items()},
        "turn": s.turn,
        "over": s.over,
        "land_played": s.land_played,
        "pool": [sorted(c) for c in s.pool],
        "treasures": s.treasures,
        "pending_tutor": s.pending_tutor,
        "triggers_this_turn": s.triggers_this_turn,
        "opponent_life_lost": s.opponent_life_lost,
        "commander_damage": s.commander_damage,
        "cast_names": list(s.cast_names),
        "commander_cast_turn": s.commander_cast_turn,
        "checkpoints": dict(s.checkpoints),
        "win_by": s.win_by,
        "mulligans": s.mulligans,
        "spent_this_turn": dict(s.spent_this_turn),
        "pre_commander": dict(s.pre_commander),
        "per_turn": [dict(t) for t in s.per_turn],
        "thing_turns": [dict(t) for t in s.thing_turns],
        "events": [dict(e) for e in s.events],
        "late_reason": s.late_reason,
        "log": list(s.log),
    }


def _dump_rng(rng: random.Random) -> list:
    version, internal, gauss = rng.getstate()
    return [version, list(internal), gauss]


def _load_rng(data) -> random.Random:
    rng = random.Random()
    version, internal, gauss = data
    rng.setstate((version, tuple(internal), gauss))
    return rng


def from_dict(data: dict) -> GameState:
    try:
        return _from_dict(data)
    except (KeyError, TypeError, ValueError, AttributeError) as err:
        raise InvalidGameState(
            f"the game file is malformed ({type(err).__name__}: {err}); "
            "start a new game with goldfish-new") from err


def _from_dict(data: dict) -> GameState:
    cards = tuple(
        CardInfo(**{**{k: v for k, v in c.items() if k != "effect"},
                    "functions": tuple(c["functions"]),
                    "effect": effect_from_dict(c["effect"])})
        for c in data["cards"])
    goal = load_goal(data["goal"], deck_names=[c.name for c in cards])
    plain = {k: data[k] for k in (
        "library", "hand", "graveyard", "command_zone", "turn", "over", "land_played",
        "treasures", "pending_tutor", "triggers_this_turn", "opponent_life_lost",
        "commander_damage", "cast_names", "commander_cast_turn", "checkpoints", "win_by",
        "mulligans", "spent_this_turn", "pre_commander", "per_turn", "thing_turns",
        "events", "late_reason", "log")}
    return GameState(
        cards=cards, goal=goal, goal_raw=data["goal"], turn_cap=data["turn_cap"],
        disruption=data["disruption"], rng=_load_rng(data["rng"]),
        dice=_load_rng(data["dice"]),
        battlefield=[Permanent(**p) for p in data["battlefield"]],
        tax={int(k): v for k, v in data["tax"].items()},
        pool=[frozenset(c) for c in data["pool"]],
        **plain,
    )
