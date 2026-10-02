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

from .. import card_rules
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
#: Extra turns are taken one after another inside a table turn, so a loop such
#: as Nexus of Fate under Omniscience would never end. Past this many in one
#: table turn the rest are dropped and the log says so.
EXTRA_TURN_CAP = 20
#: Consecrated Sphinx stops drawing once the library would fall below this,
#: so its triggers never deck you (the user's rule).
SPHINX_LIBRARY_FLOOR = 15
#: Smothering Tithe Treasures per round: opponents usually pay (the user's rule).
TITHE_TREASURES_PER_ROUND = 1
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
    #: The card's colors, for "each color among permanents you control".
    colors: tuple[str, ...] = ()


@dataclass
class Permanent:
    #: Index into GameState.cards; None for a token.
    card: int | None
    name: str
    power: float
    is_creature: bool
    #: `turn_index` when it entered. A creature cannot attack or tap for mana
    #: during the turn it entered, extra turns included.
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
    #: The table turn: a full round of the table. Extra turns do not advance it,
    #: so checkpoints, the turn cap, and win turns all count rounds.
    turn: int = 0
    #: Every turn taken, extra turns included. Summoning sickness counts these.
    turn_index: int = 0
    #: True while the current turn is an extra one.
    extra_turn: bool = False
    extra_turns_pending: int = 0
    #: Extra turns taken so far in this table turn, for EXTRA_TURN_CAP.
    extra_turns_taken: int = 0
    over: bool = False
    #: Lands played this turn; extra land drops raise the limit above one.
    lands_played: int = 0
    #: Spells cast free this turn through One with the Multiverse.
    free_spells_used: int = 0
    #: Set when a card wins the game outright (Approach of the Second Sun).
    alt_win: str | None = None
    #: Searches left on a multi-card tutor (Conflux) while pending_tutor is set.
    tutors_left: int = 0
    #: Mana floating this main phase, one entry per mana.
    pool: list[frozenset[str]] = field(default_factory=list)
    treasures: int = 0
    #: Set while a tutor waits for its choice: the tutor's restriction.
    pending_tutor: str | None = None
    #: Cards still to put from hand on top of the library (Enter the Infinite).
    pending_put_back: int = 0
    triggers_this_turn: int = 0
    opponent_life_lost: float = 0.0
    commander_damage: float = 0.0
    cast_names: list[str] = field(default_factory=list)
    commander_cast_turn: int | None = None
    checkpoints: dict[str, int | None] = field(
        default_factory=lambda: {"commander": None, "thing": None, "win": None, "loss": None})
    win_by: str | None = None
    #: Why the game was lost ("decked"); None while it is not.
    loss_by: str | None = None
    #: True on the copy a policy look-ahead plays out. Not serialized.
    looking_ahead: bool = False
    #: Look-ahead results already computed this turn. Not serialized.
    lookahead_cache: dict = field(default_factory=dict)
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
    #: Opponents' win attempts: {"turn", "stopped", "by"}.
    win_attempts: list[dict] = field(default_factory=list)
    #: Rounds an opponent attempts to win (from the goal's opponent_win rule).
    attempt_rounds: list[int] = field(default_factory=list)
    #: Pact costs owed: [cost, turn_index of the upkeep it is due].
    pacts_due: list[list] = field(default_factory=list)
    #: Chrome Mox and kin: card index -> colors of the card exiled with it.
    imprints: dict[int, list[str]] = field(default_factory=dict)
    #: True while the pending tutor puts its card on top (Vampiric Tutor).
    pending_tutor_top: bool = False
    late_reason: str | None = None
    log: list[str] = field(default_factory=list)


# --- Setup -----------------------------------------------------------------


def prepare(deck: ResolvedDeck, goal_raw: dict) -> Setup:
    """Parse every card's effect and validate the goal against the deck, once."""
    if not deck.commanders:
        raise DeckStructureError("goldfish needs a commander; the decklist has none")
    identity = deck.command_zone_identity
    names = [c.name for c in deck.commanders] + [c.name for _, c in deck.cards]
    library = card_rules.engine_rules(names)
    if library:
        # The goal file's own overrides come last, so they win over the library.
        goal_raw = {**goal_raw, "engine": {**library, **goal_raw.get("engine", {})}}
    goal = load_goal(goal_raw, deck_names=names,
                     commander_mv=min(c.mana_value for c in deck.commanders))
    cards = [_info(c, identity, True) for c in deck.commanders]
    cards += [_info(c, identity, False) for c in deck.iter_cards()]
    cards = [_overridden(c, spec) if (spec := goal.engine_for(c.name)) is not None else c
             for c in cards]
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
    state.attempt_rounds = _attempt_schedule(setup.goal, seed, turn_cap)
    _mulligan(state)
    for idx in [i for i in state.hand if state.cards[i].effect.leyline]:
        state.hand.remove(idx)
        state.battlefield.append(Permanent(card=idx, name=state.cards[idx].name, power=0.0,
                                           is_creature=False, entered=0))
        state.log.append(f"T1: {state.cards[idx].name} begins on the battlefield")
    _begin_turn(state)
    return state


def _attempt_schedule(goal: Goal, seed, turn_cap: int) -> list[int]:
    """Rounds the opponents try to win: from `from_turn`, then every round or
    every 2-3 rounds. Its own random stream, so matched seeds share it."""
    rule = goal.opponent_win
    if rule is None:
        return []
    if rule.every is None:
        return list(range(rule.from_turn, turn_cap + 1))
    rng = random.Random(f"{seed}-attempts")
    rounds, r = [], rule.from_turn
    while r <= turn_cap:
        rounds.append(r)
        r += rng.randint(*rule.every)
    return rounds


def _overridden(info: CardInfo, spec) -> CardInfo:
    """An engine override replaces the parsed effect: only the card's body
    (power, equipment bonus) and land face survive, so its text's draw, mana,
    and interaction no longer apply on top of the override. The override's own
    `mana` becomes the card's mana."""
    e = info.effect
    return replace(info, effect=SimEffect(power=e.power, power_bonus=e.power_bonus,
                                          land_colors=e.land_colors,
                                          enters_tapped=e.enters_tapped,
                                          mana=spec.mana, mana_colors=spec.mana_colors,
                                          extra_turns=e.extra_turns,
                                          shuffle_self=e.shuffle_self))


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
        colors=tuple(sorted(card.colors)),
    )


# --- Reading the state -----------------------------------------------------


def legal_actions(state: GameState) -> list[dict]:
    """Every action `apply` accepts right now. Always ends with pass, unless a
    tutor is waiting for its choice or the game is over."""
    if state.over:
        return []
    if state.pending_put_back:
        return [{"put_back": name} for name in _distinct(state, state.hand, lambda c: True)]
    if state.pending_tutor is not None:
        names = sorted({state.cards[i].name for i in state.library
                        if _tutor_matches(state.cards[i], state.pending_tutor)})
        return [{"tutor": name} for name in names] or [{"tutor": None}]

    actions: list[dict] = []
    if _land_drops_left(state):
        for name in _distinct(state, state.hand, lambda c: c.is_land or c.is_mdfc_land):
            actions.append({"play_land": name})
        if state.library and _lands_from_top(state) and state.cards[state.library[0]].is_land:
            actions.append({"play_land_top": state.cards[state.library[0]].name})
    units = _units(state)
    has_land = any(state.cards[i].is_land for i in state.hand)
    for idx in _first_of_each(state, state.hand + state.command_zone):
        if state.cards[idx].effect.discard_land and not has_land:
            continue
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
    statics = _statics(state)
    return sum(len(_produces(state, p, ignore_sickness=True, statics=statics))
               for p in state.battlefield)


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
    if kind == "battlefield":
        out = {p.name for p in state.battlefield}
        return all(any(name in out for name in slot) for slot in cond.slots)
    raise ValueError(f"unknown condition kind {kind!r}")


def win_label(state: GameState) -> str | None:
    """Which win condition holds, as a `describe` label; None if none does."""
    if state.alt_win:
        return f"won:{state.alt_win}"
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
    counterspells. A removal engine on the battlefield counts as standing removal."""
    counts = {"removal": 0, "protection": 0, "counterspell": 0}
    counts["removal"] += len(removal_engines(state))
    for idx in state.hand:
        held = state.cards[idx].effect.held
        if held & {"removal", "sweeper"}:
            counts["removal"] += 1
        if "protection" in held:
            counts["protection"] += 1
        if "counterspell" in held:
            counts["counterspell"] += 1
    return counts


def removal_engines(state: GameState) -> list[Permanent]:
    """Repeatable removal engines (goal `removal_engine`) on the battlefield."""
    out = []
    for perm in state.battlefield:
        if perm.card is None or perm.is_land:
            continue
        spec = state.goal.engine_for(perm.name)
        if spec is not None and spec.removal_engine:
            out.append(perm)
    return out


def devotion_to_blue(state: GameState) -> int:
    """Blue mana symbols among your permanents' mana costs."""
    total = 0
    for perm in state.battlefield:
        if perm.card is not None and not perm.is_land:
            total += sum(1 for pip in parse_cost(state.cards[perm.card].mana_cost)[1] if "U" in pip)
    return total


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
    elif "play_land_top" in action:
        idx = s.library.pop(0)
        _put_land(s, idx, tapped=s.cards[idx].effect.enters_tapped)
        s.lands_played += 1
    elif "cast" in action:
        _cast(s, action["cast"])
    elif "put_back" in action:
        _put_back(s, action["put_back"])
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
    _put_land(s, idx, tapped=card.effect.enters_tapped if card.is_land else False)
    s.lands_played += 1


def _put_land(s: GameState, idx: int, *, tapped: bool) -> None:
    """A land enters under your control, played or fetched: landfall fires."""
    name = s.cards[idx].name
    s.battlefield.append(Permanent(card=idx, name=name, power=0.0, is_creature=False,
                                   entered=s.turn_index, tapped=tapped, is_land=True))
    s.log.append(f"T{s.turn}: land {name}" + (" (tapped)" if tapped else ""))
    for perm in s.battlefield:
        if perm.card is not None and not perm.is_land:
            effect = s.cards[perm.card].effect
            s.pool.extend([_ANY] * effect.landfall_mana)
            s.treasures += effect.landfall_treasure


def _cast(s: GameState, name: str) -> None:
    from_hand = find_card(s, name, s.hand)
    idx = from_hand if from_hand is not None else find_card(s, name, s.command_zone)
    units = _units(s)
    plan, delved, free = _payment_full(s, idx, units)
    _spend(s, plan, units)
    (s.hand if from_hand is not None else s.command_zone).remove(idx)
    card = s.cards[idx]
    del s.graveyard[:delved]  # delve exiles them
    if card.effect.discard_land:
        land = next(i for i in s.hand if s.cards[i].is_land)
        s.hand.remove(land)
        s.graveyard.append(land)
    s.free_spells_used += free
    s.spent_this_turn[_spend_category(s, idx)] += len(plan)
    s.cast_names.append(name)
    if card.is_commander and s.commander_cast_turn is None:
        s.commander_cast_turn = s.turn
    s.log.append(f"T{s.turn}: cast {name} ({len(plan)} mana)" + (" free" if free else ""))
    _fire(s, "spell_cast")
    if "Instant" in card.type_line or "Sorcery" in card.type_line:
        _fire(s, "instant_sorcery_cast")
    _cascades(s, card)
    _resolve(s, idx, from_hand=from_hand is not None)


def _cast_free(s: GameState, idx: int) -> None:
    """Cast a card without paying (cascade, Emergent Ultimatum), not from hand."""
    card = s.cards[idx]
    s.cast_names.append(card.name)
    s.log.append(f"T{s.turn}: cast {card.name} free")
    _fire(s, "spell_cast")
    if "Instant" in card.type_line or "Sorcery" in card.type_line:
        _fire(s, "instant_sorcery_cast")
    _cascades(s, card)
    _resolve(s, idx)


def _cascades(s: GameState, card: CardInfo) -> None:
    count = card.effect.cascade
    if not card.is_land and any(
            p.card is not None and 0 < s.cards[p.card].effect.grants_cascade_min <= card.mana_value
            for p in s.battlefield):
        count += 1
    for _ in range(count):
        _cascade_once(s, card.mana_value)


def _cascade_once(s: GameState, mana_value: float) -> None:
    """Exile from the top until a nonland card of lower mana value; cast it free.
    A card that draws the library is declined: casting it blind risks decking."""
    exiled, hit = [], None
    while s.library and not s.over:
        idx = s.library.pop(0)
        card = s.cards[idx]
        if not card.is_land and card.mana_value < mana_value:
            if card.effect.draw_library:
                exiled.append(idx)
            else:
                hit = idx
            break
        exiled.append(idx)
    s.rng.shuffle(exiled)
    s.library.extend(exiled)
    if hit is not None:
        s.log.append(f"T{s.turn}: cascade into {s.cards[hit].name}")
        _cast_free(s, hit)


def _resolve(s: GameState, idx: int, *, from_hand: bool = False) -> None:
    card = s.cards[idx]
    effect = card.effect
    if card.is_permanent:
        perm = Permanent(card=idx, name=card.name, power=effect.power,
                         is_creature=card.is_creature, entered=s.turn_index,
                         tapped=effect.enters_tapped)
        s.battlefield.append(perm)
        if card.is_creature:
            _fire(s, "creature_etb", exclude=perm)
    elif effect.approach:
        if from_hand and s.cast_names.count(card.name) >= 2:
            s.alt_win = card.name
            s.graveyard.append(idx)
        else:
            s.library.insert(min(6, len(s.library)), idx)  # seventh from the top
    elif effect.shuffle_self:
        s.library.append(idx)
        s.rng.shuffle(s.library)
    else:
        s.graveyard.append(idx)
    if effect.thoracle and devotion_to_blue(s) >= len(s.library):
        s.alt_win = card.name
    if effect.exile_library:
        s.log.append(f"T{s.turn}: exile the library ({len(s.library)} cards)")
        s.library = []
    s.extra_turns_pending += effect.extra_turns
    s.pool.extend([_ANY] * effect.mana_once)
    s.treasures += effect.treasure_once
    for _ in range(effect.fetch_battlefield):
        _fetch_land(s, to_battlefield=True, tapped=effect.fetch_tapped, types=effect.fetch_types)
    for _ in range(effect.fetch_hand):
        _fetch_land(s, to_battlefield=False, tapped=False, types=effect.fetch_types)
    _draw(s, effect.draw_once)
    if effect.draw_library:
        _draw(s, len(s.library))
        s.pending_put_back = min(effect.put_back, len(s.hand))
    if effect.dig_permanents:
        _dig_permanents(s, effect.dig_permanents)
    if effect.dig_look:
        top = s.library[:effect.dig_look]
        del s.library[:effect.dig_look]
        chosen = _best_cards(s, top, effect.dig_take)
        s.hand.extend(chosen)
        s.library.extend(i for i in top if i not in chosen)
    if effect.emergent:
        _emergent(s, effect.emergent)
    if effect.tutor and any(_tutor_matches(s.cards[i], effect.tutor) for i in s.library):
        s.pending_tutor = effect.tutor
        s.tutors_left = max(1, effect.tutor_count)
        s.pending_tutor_top = effect.tutor_to_top
    if effect.imprint:
        _imprint(s, idx)


def _imprint(s: GameState, mox: int) -> None:
    """Chrome Mox: exile the least needed colored nonartifact, nonland card from
    hand; the mox taps for its colors. With nothing to exile it makes no mana."""
    options = [i for i in s.hand if not s.cards[i].is_land and s.cards[i].colors
               and "Artifact" not in s.cards[i].type_line and not s.cards[i].effect.held]
    if not options:
        return
    worst = _best_cards(s, options, len(options))[-1]
    s.hand.remove(worst)
    s.imprints[mox] = [c for c in s.cards[worst].colors if c in _ANY]
    s.log.append(f"T{s.turn}: imprint {s.cards[worst].name}")


def _dig_permanents(s: GameState, n: int) -> None:
    """Genesis Ultimatum: permanents from the top N onto the battlefield, the
    rest into hand."""
    top = s.library[:n]
    del s.library[:n]
    for idx in top:
        card = s.cards[idx]
        if card.is_land:
            _put_land(s, idx, tapped=card.effect.enters_tapped)
        elif card.is_permanent:
            _resolve(s, idx)
        else:
            s.hand.append(idx)


def _emergent(s: GameState, n: int) -> None:
    """Emergent Ultimatum: find N monocolored cards with different names; the
    opponent shuffles the best one back; cast the rest free."""
    seen, pool = set(), []
    for idx in s.library:
        card = s.cards[idx]
        if not card.is_land and len(card.colors) == 1 and card.name not in seen:
            seen.add(card.name)
            pool.append(idx)
    found = _best_cards(s, pool, n)
    for idx in found:
        s.library.remove(idx)
    if found:
        s.library.append(found[0])  # the opponent's pick goes back
        s.rng.shuffle(s.library)
    for idx in found[1:]:
        _cast_free(s, idx)


def _best_cards(s: GameState, idxs: list[int], k: int) -> list[int]:
    """The k cards a pilot would keep: pieces the goal names, then the most
    expensive spells, then lands."""
    wanted = condition_names(s.goal.thing) + condition_names(s.goal.win)

    def score(idx):
        card = s.cards[idx]
        return (card.name not in wanted, card.is_land, -card.mana_value, card.name)

    return sorted(idxs, key=score)[:k]


def _tutor(s: GameState, name: str | None) -> None:
    restriction = s.pending_tutor
    s.pending_tutor = None
    if name is not None and s.pending_tutor_top:
        idx = find_card(s, name, s.library)
        s.library.remove(idx)
        s.rng.shuffle(s.library)
        s.library.insert(0, idx)
        s.pending_tutor_top = False
        s.tutors_left = 0
        s.log.append(f"T{s.turn}: tutor {name} to the top")
        return
    s.pending_tutor_top = False
    if name is not None:
        idx = find_card(s, name, s.library)
        s.library.remove(idx)
        s.hand.append(idx)
        s.log.append(f"T{s.turn}: tutor {name}")
    s.tutors_left = max(0, s.tutors_left - 1)
    if s.tutors_left and name is not None and any(
            _tutor_matches(s.cards[i], restriction) for i in s.library):
        s.pending_tutor = restriction
        return
    s.tutors_left = 0
    s.rng.shuffle(s.library)


def _sacrifice(s: GameState, name: str) -> None:
    perm = next(p for p in s.battlefield if p.is_creature and p.name == name)
    s.log.append(f"T{s.turn}: sacrifice {name}")
    _kill(s, [perm])


def _fetch_land(s: GameState, *, to_battlefield: bool, tapped: bool, types: str = "basic") -> None:
    """Take the land the search allows that adds a color the deck has least of.

    `types` is "basic", "land", or land types ("forest"), which a nonbasic dual
    or triome with that type also satisfies (Nature's Lore finds a Bayou).
    """
    types = types or "basic"

    def allowed(card):
        if not card.is_land:
            return False
        if types == "basic":
            return card.is_basic
        if types == "land":
            return True
        return any(t in card.type_line.lower() for t in types.split("|"))

    candidates = [i for i in s.library if allowed(s.cards[i])]
    if not candidates:
        return
    have = Counter(c for p in s.battlefield if p.is_land and p.card is not None
                   for c in s.cards[p.card].effect.land_colors)
    idx = min(candidates, key=lambda i: (
        min((have[c] for c in s.cards[i].effect.land_colors), default=0),
        -len(s.cards[i].effect.land_colors)))
    s.library.remove(idx)
    if to_battlefield:
        _put_land(s, idx, tapped=tapped)
    else:
        s.hand.append(idx)


def _draw(s: GameState, n: int) -> None:
    """Draw `n` cards. Drawing from an empty library loses the game."""
    for _ in range(n):
        if s.over:
            return
        if not s.library:
            _lose(s, "decked")
            return
        s.hand.append(s.library.pop(0))


def _lose(s: GameState, reason: str) -> None:
    s.over = True
    s.loss_by = reason
    s.checkpoints["loss"] = s.turn
    s.log.append(f"T{s.turn}: lose ({reason})")


def _put_back(s: GameState, name: str) -> None:
    idx = find_card(s, name, s.hand)
    s.hand.remove(idx)
    s.library.insert(0, idx)
    s.pending_put_back -= 1
    s.log.append(f"T{s.turn}: put {name} on top of the library")


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
                              is_creature=True, entered=s.turn_index)
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


def _begin_turn(s: GameState, *, extra: bool = False) -> None:
    """Untap, upkeep, draw, and (on a table turn) the disruption roll.

    An extra turn is a full turn — untap, draw, a land drop, main, combat — but
    the opponents get no turn before it, so it keeps the table-turn number and
    rolls no disruption.
    """
    s.turn_index += 1
    s.extra_turn = extra
    if not extra:
        s.turn += 1
        s.extra_turns_taken = 0
    s.lands_played = 0
    s.free_spells_used = 0
    s.pool = []
    s.triggers_this_turn = 0
    s.spent_this_turn = dict.fromkeys(_SPEND_CATEGORIES, 0)
    if not extra and s.turn_index > 1:
        _opponents_draws(s)
        _opponent_win_attempt(s)
        if s.over:
            return
    for perm in s.battlefield:
        if not (perm.card is not None and s.cards[perm.card].effect.no_untap):
            perm.tapped = False
    _pay_pacts(s)
    if s.over:
        return
    _sac_tutors(s)
    for perm in list(s.battlefield):
        if perm.card is not None:
            _draw(s, s.cards[perm.card].effect.draw_per_turn)
    _fire(s, "upkeep")
    if s.turn_index > 1:
        _draw(s, 1)
    if not extra and not s.over:
        _disrupt(s)


def _opponents_draws(s: GameState) -> None:
    """Each opponent drew once since your last turn: Consecrated Sphinx draws
    two per draw (stopping short of decking), Smothering Tithe makes Treasure."""
    for perm in list(s.battlefield):
        if perm.card is None or perm.is_land:
            continue
        effect = s.cards[perm.card].effect
        for _ in range(OPPONENTS if effect.opp_draw_cards else 0):
            if len(s.library) - effect.opp_draw_cards >= SPHINX_LIBRARY_FLOOR:
                _draw(s, effect.opp_draw_cards)
        if effect.opp_draw_treasure:
            s.treasures += TITHE_TREASURES_PER_ROUND


def _opponent_win_attempt(s: GameState) -> None:
    """On a fast table an opponent tries to win during the round. A stax piece
    you control stops it and stays; otherwise a held removal spell or
    counterspell stops it and is spent; otherwise you lose."""
    rule = s.goal.opponent_win
    if rule is None or s.turn not in s.attempt_rounds:
        return
    if "stax" in rule.answers:
        for perm in s.battlefield:
            if perm.card is None or perm.is_land:
                continue
            spec = s.goal.engine_for(perm.name)
            if s.cards[perm.card].effect.stax or (spec is not None and spec.stax):
                s.win_attempts.append({"turn": s.turn, "stopped": True, "by": perm.name})
                return
    if "removal" in rule.answers:
        engines = removal_engines(s)
        if engines:
            s.win_attempts.append({"turn": s.turn, "stopped": True, "by": engines[0].name})
            s.log.append(f"T{s.turn}: opponent's win attempt stopped by {engines[0].name}")
            return
    wanted = set()
    if "removal" in rule.answers:
        wanted |= {"removal", "sweeper"}
    if "counterspell" in rule.answers:
        wanted.add("counterspell")
    # A pact is spent last: its upkeep cost can lose the game.
    for idx in sorted(s.hand, key=lambda i: bool(s.cards[i].effect.pact_cost)):
        if s.cards[idx].effect.held & wanted:
            _spend_answer(s, idx, due_now=True)
            s.win_attempts.append({"turn": s.turn, "stopped": True, "by": s.cards[idx].name})
            s.log.append(f"T{s.turn}: opponent's win attempt stopped by {s.cards[idx].name}")
            return
    s.win_attempts.append({"turn": s.turn, "stopped": False, "by": None})
    _lose(s, "opponent_win")


def _spend_answer(s: GameState, idx: int, *, due_now: bool) -> None:
    """An answer leaves hand for the graveyard; a pact's cost comes due at
    your next upkeep — this turn's if it was spent during the opponents' turns."""
    s.hand.remove(idx)
    s.graveyard.append(idx)
    cost = s.cards[idx].effect.pact_cost
    if cost:
        s.pacts_due.append([cost, s.turn_index if due_now else s.turn_index + 1])


def _pay_pacts(s: GameState) -> None:
    due = [p for p in s.pacts_due if p[1] <= s.turn_index]
    s.pacts_due = [p for p in s.pacts_due if p[1] > s.turn_index]
    for cost, _ in due:
        generic, pips = parse_cost(cost)
        units = _units(s)
        plan = plan_payment(units, generic, pips)
        if plan is None:
            _lose(s, "pact")
            return
        _spend(s, plan, units)
        s.log.append(f"T{s.turn}: paid a pact ({cost})")


def _sac_tutors(s: GameState) -> None:
    """Sterling Grove: sacrifice it to put a missing goal piece of its type on top."""
    wanted = condition_names(s.goal.thing) + condition_names(s.goal.win)
    present = {p.name for p in s.battlefield} | {s.cards[i].name for i in s.hand}
    for perm in list(s.battlefield):
        if perm.card is None or not s.cards[perm.card].effect.sac_tutor_top:
            continue
        kind = s.cards[perm.card].effect.sac_tutor_top
        target = next((i for i in s.library if s.cards[i].name in wanted
                       and s.cards[i].name not in present
                       and kind in s.cards[i].type_line.lower()), None)
        if target is None:
            continue
        s.battlefield.remove(perm)
        s.graveyard.append(perm.card)
        s.library.remove(target)
        s.library.insert(0, target)
        s.log.append(f"T{s.turn}: sacrifice {perm.name}, {s.cards[target].name} on top")


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
        _spend_answer(s, answer, due_now=False)
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
        def rock(p):
            effect = s.cards[p.card].effect if p.card is not None else None
            return (not p.is_creature and effect is not None
                    and (effect.mana or effect.imprint or effect.mana_per_color))
        _kill(s, [p for p in s.battlefield if not p.is_land and not rock(p)])


def _protection_on_board(s: GameState) -> Permanent | None:
    """A protection permanent (Lightning Greaves, Mother of Runes) guards the
    commander from removal without being used up. It does not stop a wipe."""
    for perm in s.battlefield:
        if not perm.is_land and perm.card is not None and "protection" in s.cards[perm.card].effect.held:
            return perm
    return None


def _answer_in_hand(s: GameState, kind: str) -> int | None:
    """Protection first, then a counterspell. A wipe needs protection that
    survives it: indestructible or phasing. Pure protection goes before a card
    that could also stop an opponent's win (Boros Charm's removal mode), so
    the win-attempt answer is kept for the win attempt."""
    def keeps_win_answer(idx):
        return bool(s.cards[idx].effect.held & {"removal", "sweeper", "counterspell"})

    protection = [idx for idx in s.hand if "protection" in s.cards[idx].effect.held
                  and (kind == "commander_removal" or s.cards[idx].effect.wipe_proof)]
    if protection:
        return min(protection, key=keeps_win_answer)
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
    # A table turn is recorded once: an extra turn overwrites its row, so the
    # row holds the board at the end of the round's last turn.
    _record(s.per_turn, {"turn": s.turn, "mana": production(s),
                         "lands": sum(1 for p in s.battlefield if p.is_land)})

    if s.checkpoints["commander"] is None and _commanders_on_board(s):
        s.checkpoints["commander"] = s.turn
    if s.turn == s.goal.commander_turn and s.commander_cast_turn is None:
        s.late_reason = _late_reason(s)
    if evaluate(s, s.goal.thing):
        if s.checkpoints["thing"] is None:
            s.checkpoints["thing"] = s.turn
        _record(s.thing_turns, {"turn": s.turn, **held_counts(s)})
    label = win_label(s)
    if label is not None:
        s.checkpoints["win"] = s.turn
        s.win_by = label
        s.over = True
        s.log.append(f"T{s.turn}: win ({label})")
        return
    if s.extra_turns_pending and s.extra_turns_taken >= EXTRA_TURN_CAP:
        s.log.append(f"T{s.turn}: extra-turn cap ({EXTRA_TURN_CAP}) reached; "
                     f"dropped {s.extra_turns_pending} more")
        s.extra_turns_pending = 0
    if s.extra_turns_pending:
        s.extra_turns_pending -= 1
        s.extra_turns_taken += 1
        s.log.append(f"T{s.turn}: extra turn")
        _begin_turn(s, extra=True)
    elif s.turn >= s.turn_cap:
        s.over = True
    else:
        _begin_turn(s)


def _record(rows: list[dict], row: dict) -> None:
    if rows and rows[-1]["turn"] == row["turn"]:
        rows[-1] = row
    else:
        rows.append(row)


def _combat(s: GameState) -> None:
    """Unblocked combat. An `attack` trigger fires once per combat, not once
    per attacking creature."""
    attackers = [p for p in s.battlefield if p.is_creature and p.entered < s.turn_index]
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


def _statics(s: GameState) -> tuple[bool, bool, frozenset[str]]:
    """Board-wide mana rules: lands tap for any color (Lantern, Dryad); all mana
    is any color (Orrery); and the colors among your permanents (Bloom Tender)."""
    lands_any = mana_any = False
    colors: set[str] = set()
    for perm in s.battlefield:
        if perm.card is None or perm.is_land:
            continue
        card = s.cards[perm.card]
        lands_any = lands_any or card.effect.lands_any_color
        mana_any = mana_any or card.effect.mana_any_color
        colors.update(card.colors)
    return lands_any, mana_any, frozenset(colors) & _ANY


def _produces(s: GameState, perm: Permanent, *, ignore_sickness: bool = False,
              statics=None) -> list[frozenset[str]]:
    if perm.card is None:
        return []
    lands_any, mana_any, permanent_colors = statics or _statics(s)
    effect = s.cards[perm.card].effect
    if perm.is_land:
        if not effect.land_colors:
            return []
        colors = effect.land_colors | (_ANY if lands_any or mana_any else frozenset())
        return [colors]
    if perm.is_creature and perm.entered >= s.turn_index and not ignore_sickness:
        return []
    if effect.imprint:
        colors = s.imprints.get(perm.card)
        units = [frozenset(colors)] if colors else []
    elif effect.mana_per_color:
        units = [frozenset({c}) for c in sorted(permanent_colors)]
    else:
        units = [effect.mana_colors] * effect.mana
    if mana_any:
        units = [u | _ANY for u in units]
    if effect.colored_only:
        units = [u | {"*"} for u in units]  # "*": this mana can't pay generic costs
    return units


def _units(s: GameState) -> list[Unit]:
    units = [Unit(colors, "pool", i) for i, colors in enumerate(s.pool)]
    statics = _statics(s)
    for i, perm in enumerate(s.battlefield):
        if not perm.tapped:
            kind = "land" if perm.is_land else "rock"
            units.extend(Unit(colors, kind, i) for colors in _produces(s, perm, statics=statics))
    units.extend(Unit(_ANY, "treasure", -1) for _ in range(s.treasures))
    return units


def _payment(s: GameState, idx: int, units: list[Unit]) -> list[int] | None:
    return _payment_full(s, idx, units)[0]


def _payment_full(s: GameState, idx: int, units: list[Unit]) -> tuple[list[int] | None, int, int]:
    """The cheapest payable plan, with how many graveyard cards it delves and
    whether it uses a free cast.

    The options are the card's own cost and any alternative cost a permanent
    offers (Jodah's WUBRG; Omniscience's {0}, from hand only); tax applies to
    each, delve pays generic from the graveyard, and a tie goes to the card's
    own cost. One with the Multiverse's free cast is saved for a spell that
    would otherwise cost five or more, or could not be cast at all.
    """
    card = s.cards[idx]
    tax = s.tax.get(idx, 0)
    in_hand = idx in s.hand
    best, best_delve = None, 0
    for cost, hand_only in [(card.mana_cost, False), *_alt_cost_options(s)]:
        if hand_only and not in_hand:
            continue
        generic, pips = parse_cost(cost)
        generic += tax
        delve = min(generic, len(s.graveyard)) if card.effect.delve else 0
        plan = plan_payment(units, generic - delve, pips)
        if plan is not None and (best is None or len(plan) < len(best)):
            best, best_delve = plan, delve
    frees = sum(s.cards[p.card].effect.free_spell_per_turn for p in s.battlefield
                if p.card is not None)
    if frees > s.free_spells_used and (best is None or len(best) >= 5):
        return [], 0, 1
    return best, best_delve, 0


def _alt_cost_options(s: GameState) -> list[tuple[str, bool]]:
    """(cost, from-hand-only) for every alternative cost on the battlefield,
    from card text or from a goal-file override."""
    options: list[tuple[str, bool]] = []
    for perm in s.battlefield:
        if perm.card is None:
            continue
        card = s.cards[perm.card]
        spec = s.goal.engine_for(card.name)
        if spec is not None and spec.alt_cost:
            option = (spec.alt_cost, False)
        elif card.effect.alt_cost:
            option = (card.effect.alt_cost, card.effect.alt_cost_hand_only)
        else:
            continue
        if option not in options:
            options.append(option)
    return options


def _alt_costs(s: GameState) -> list[str]:
    return [cost for cost, _ in _alt_cost_options(s)]


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


def _land_drops_left(s: GameState) -> int:
    extra = sum(s.cards[p.card].effect.extra_land_drops for p in s.battlefield
                if p.card is not None and not p.is_land)
    return max(0, 1 + extra - s.lands_played)


def _lands_from_top(s: GameState) -> bool:
    return any(p.card is not None and not p.is_land and s.cards[p.card].effect.lands_from_top
               for p in s.battlefield)


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
                   "functions": list(c.functions), "colors": list(c.colors), "effect": effect_to_dict(c.effect)}
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
        "turn_index": s.turn_index,
        "extra_turn": s.extra_turn,
        "extra_turns_pending": s.extra_turns_pending,
        "extra_turns_taken": s.extra_turns_taken,
        "over": s.over,
        "lands_played": s.lands_played,
        "free_spells_used": s.free_spells_used,
        "alt_win": s.alt_win,
        "tutors_left": s.tutors_left,
        "pool": [sorted(c) for c in s.pool],
        "treasures": s.treasures,
        "pending_tutor": s.pending_tutor,
        "pending_put_back": s.pending_put_back,
        "loss_by": s.loss_by,
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
        "win_attempts": [dict(a) for a in s.win_attempts],
        "pacts_due": [list(p) for p in s.pacts_due],
        "attempt_rounds": list(s.attempt_rounds),
        "imprints": {str(k): list(v) for k, v in s.imprints.items()},
        "pending_tutor_top": s.pending_tutor_top,
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
                    "colors": tuple(c.get("colors", ())),
                    "effect": effect_from_dict(c["effect"])})
        for c in data["cards"])
    goal = load_goal(data["goal"], deck_names=[c.name for c in cards])
    plain = {k: data[k] for k in (
        "library", "hand", "graveyard", "command_zone", "turn", "over",
        "treasures", "pending_tutor", "triggers_this_turn", "opponent_life_lost",
        "commander_damage", "cast_names", "commander_cast_turn", "checkpoints", "win_by",
        "mulligans", "spent_this_turn", "pre_commander", "per_turn", "thing_turns",
        "events", "late_reason", "log")}
    # Game files written before extra turns existed have no turn_index; their
    # turns were all table turns, so the two counters were equal.
    plain["turn_index"] = data.get("turn_index", data["turn"])
    plain["extra_turn"] = data.get("extra_turn", False)
    plain["extra_turns_pending"] = data.get("extra_turns_pending", 0)
    plain["extra_turns_taken"] = data.get("extra_turns_taken", 0)
    plain["pending_put_back"] = data.get("pending_put_back", 0)
    plain["loss_by"] = data.get("loss_by")
    plain["checkpoints"] = {"loss": None, **plain["checkpoints"]}
    plain["lands_played"] = data.get("lands_played", int(bool(data.get("land_played", False))))
    plain["free_spells_used"] = data.get("free_spells_used", 0)
    plain["alt_win"] = data.get("alt_win")
    plain["tutors_left"] = data.get("tutors_left", 0)
    plain["win_attempts"] = data.get("win_attempts", [])
    plain["pacts_due"] = data.get("pacts_due", [])
    goal_rule = goal.opponent_win
    plain["attempt_rounds"] = data.get("attempt_rounds", list(range(
        goal_rule.from_turn, data["turn_cap"] + 1)) if goal_rule else [])
    plain["imprints"] = {int(k): v for k, v in data.get("imprints", {}).items()}
    plain["pending_tutor_top"] = data.get("pending_tutor_top", False)
    return GameState(
        cards=cards, goal=goal, goal_raw=data["goal"], turn_cap=data["turn_cap"],
        disruption=data["disruption"], rng=_load_rng(data["rng"]),
        dice=_load_rng(data["dice"]),
        battlefield=[Permanent(**p) for p in data["battlefield"]],
        tax={int(k): v for k, v in data["tax"].items()},
        pool=[frozenset(c) for c in data["pool"]],
        **plain,
    )
