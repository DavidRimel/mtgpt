# mtgpt/goldfish/policy.py
"""The heuristic pilot: a fixed priority order, so runs are repeatable.

The order is the user's: ramp, then the commander, then the commander's thing,
then card flow, then everything else. Instant and sorcery interaction is never
cast; it is held and counted, and disruption spends protection when it lands.
Interaction permanents (Equipment, Lightning Greaves, Mother of Runes) are cast,
and protect the commander from the battlefield. An MDFC whose spell face the
sim cannot model is kept as a land drop unless the goal names it. Because the order is
fixed, two versions of a deck played on the same seeds differ only by their
cards, which is what makes a before/after comparison mean something.
"""

from __future__ import annotations

from ..goal import condition_names
from .engine import GameState, apply, available_mana, find_card, legal_actions, win_label

RAMP, COMMANDER, ENGINE, VALUE, OTHER = range(5)
#: Ranks below every tier: a `hold` card cast because it wins this turn.
_WINS_NOW = -1


def choose(state: GameState) -> dict:
    """The action the heuristic takes in this state."""
    legal = legal_actions(state)
    if state.pending_tutor is not None:
        return _tutor_choice(state, legal)
    lands = [a for a in legal if "play_land" in a]
    if lands:
        return _pick_land(state, lands)

    options = []
    for action in legal:
        if "cast" not in action:
            continue
        idx = _castable_index(state, action["cast"])
        rank = tier(state, idx)
        if rank == "hold":
            if _wins_if_cast(state, action):
                options.append((_WINS_NOW, 0.0, action["cast"], action))
            continue
        if rank is not None:
            options.append((rank, state.cards[idx].mana_value, action["cast"], action))
    if options:
        return min(options)[-1]
    return {"pass": True}


def tier(state: GameState, idx: int):
    """The card's cast priority: an int tier, "hold", or None (never cast)."""
    card = state.cards[idx]
    spec = state.goal.engine_for(card.name)
    if spec is not None and spec.priority == "hold":
        return "hold"
    if card.is_commander:
        return COMMANDER
    named = spec is not None or card.name in _plan_names(state)
    if not named and card.effect.held and not card.is_permanent:
        return None
    if not named and card.is_mdfc_land and card.unmodeled:
        return None
    if card.effect.is_ramp:
        return RAMP
    if named:
        return ENGINE
    if card.effect.held:
        is_attachment = any(t in card.type_line for t in ("Equipment", "Aura"))
        return ENGINE if is_attachment and state.goal.archetype == "voltron" else OTHER
    if card.effect.draw_once or card.effect.draw_per_turn or card.effect.tutor:
        return VALUE
    return OTHER


def _plan_names(state: GameState) -> tuple[str, ...]:
    return condition_names(state.goal.thing) + condition_names(state.goal.win)


def _castable_index(state: GameState, name: str) -> int:
    idx = find_card(state, name, state.hand)
    return idx if idx is not None else find_card(state, name, state.command_zone)


def _wins_if_cast(state: GameState, action: dict) -> bool:
    """Look ahead: would casting this, then ending the turn, win?"""
    after = apply(state, action)
    while after.pending_tutor is not None:
        after = apply(after, _tutor_choice(after, legal_actions(after)), in_place=True)
    if win_label(after) is not None:
        return True
    after = apply(after, {"pass": True}, in_place=True)
    return after.checkpoints["win"] == state.turn


def _pick_land(state: GameState, lands: list[dict]) -> dict:
    """A tapped land on a turn it costs nothing, otherwise an untapped one.

    When the hand holds both, each is tried on a copy of the game and the turn
    is played out: the tapped land goes down whenever it spends as much mana as
    the untapped one would, so the untapped land is saved for a turn that needs
    it. Within each kind, a land adding a missing color comes first. An MDFC is
    played as a land only when nothing else is. Holding up instant-speed mana
    does not count, because the sim's disruption answers cost nothing.
    """
    have = {c for p in state.battlefield if p.is_land and p.card is not None
            for c in state.cards[p.card].effect.land_colors}

    def card_of(action):
        return state.cards[find_card(state, action["play_land"], state.hand)]

    def score(action):
        card = card_of(action)
        new_colors = len(card.effect.land_colors - have)
        return (card.is_mdfc_land, card.effect.enters_tapped, -new_colors, card.name)

    ranked = sorted(lands, key=score)
    plain = [a for a in ranked if not card_of(a).is_mdfc_land]
    untapped = [a for a in plain if not card_of(a).effect.enters_tapped]
    tapped = [a for a in plain if card_of(a).effect.enters_tapped]
    if untapped and tapped and (
            not _anything_affordable(state)
            or _spend_after(state, tapped[0]) >= _spend_after(state, untapped[0])):
        return tapped[0]
    return ranked[0]


def _anything_affordable(state: GameState) -> bool:
    """Could one more untapped mana pay for anything? A shortcut past the
    look-ahead: when no card costs that little, the tapped land is free."""
    budget = available_mana(state) + 1
    return any(not state.cards[i].is_land and state.cards[i].mana_value + state.tax.get(i, 0) <= budget
               for i in state.hand + state.command_zone)


def _spend_after(state: GameState, land: dict) -> int:
    """Mana the policy spends this turn if it plays `land` now (on a copy)."""
    after = apply(state, land)
    while True:
        action = choose(after)
        if "pass" in action:
            return sum(after.spent_this_turn.values())
        apply(after, action, in_place=True)


def _tutor_choice(state: GameState, legal: list[dict]) -> dict:
    """The first missing piece of the thing, then of the win; otherwise the
    highest-priority, most expensive card available."""
    offered = {a["tutor"] for a in legal}
    present = {p.name for p in state.battlefield} | {state.cards[i].name for i in state.hand}
    wanted = list(_plan_names(state)) + [name for name, _ in state.goal.engine]
    for name in wanted:
        if name in offered and name not in present:
            return {"tutor": name}

    def score(name):
        idx = find_card(state, name, state.library)
        rank = tier(state, idx)
        rank = OTHER + 1 if rank in (None, "hold") else rank
        return (rank, -state.cards[idx].mana_value, name)

    candidates = [name for name in offered if name is not None]
    if not candidates:
        return {"tutor": None}
    return {"tutor": min(candidates, key=score)}
