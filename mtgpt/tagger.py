# mtgpt/tagger.py
"""Scryfall Tagger: find cards by the function a human said they perform.

`classify.py` reads oracle text with regexes to tag cards the user *already
has*. This module does the opposite job: it discovers cards that fill a gap,
using the community-curated oracle tags Scryfall exposes on its normal search
endpoint as `otag:<tag>`.

Two things make that worth having:

* Human curation beats a regex on judgement calls. `otag:ramp` knows that
  Smothering Tithe is ramp and that Gaea's Cradle is a land, without anyone
  writing a pattern for either.
* Disagreement is a signal. When a card carries `otag:removal` and our regex
  does not tag it `spot_removal`, one of the two is wrong, and the caller
  should be told rather than quietly trusting whichever came first. That is
  what `api.find_cards(..., cross_check=True)` reports.

**The tag vocabulary is not arbitrary and not guessable.** Scryfall answers an
unknown tag with HTTP 404 "your query didn't match any cards" — the same reply
a real tag with an over-narrow filter gets — so a tag we never probed would
ship as a silent empty result. Every entry in `TAGS` below was requested live
and its card count recorded; `REJECTED` records the plausible-sounding names
that did not resolve, so nobody re-adds them on the strength of the name.
"""

from __future__ import annotations

from .models import Function

F = Function

#: Verified `otag:` values, keyed by the snake_case label this toolkit uses.
#:
#: The comment on each line is the `total_cards` Scryfall returned for a bare
#: `otag:<value> unique=cards` query, probed 2026-10-01. The number is there so
#: a future drift is visible: a tag that silently stops resolving returns 404,
#: and a tag that was renamed returns a wildly different count.
TAGS: dict[str, str] = {
    # --- the Function enum's own vocabulary --------------------------------
    "ramp": "ramp",                              # 2286
    "draw": "draw",                              # 4250
    "spot_removal": "spot-removal",              # 5401
    "sweeper": "sweeper",                        # 943
    "tutor": "tutor",                            # 1163
    "counterspell": "counterspell",              # 550
    "protection": "protection",                  # 1322
    "recursion": "recursion",                    # 2245
    "extra_turns": "extra-turn",                 # 58  (singular; "extra-turns" 404s)
    "wincon": "win-condition",                   # 69  (alias of alternate-win-condition)
    # The spelling is the Function member's own value. Four other spellings
    # 404 — see REJECTED — and shipping the claim that no tag existed was wrong.
    "mass_land_denial": "mass-land-denial",      # 106 commander-legal
    # --- broader or adjacent categories worth searching --------------------
    "removal": "removal",                        # 6449  (spot + mass, both)
    "card_advantage": "card-advantage",          # 6206  (wider than our `draw`)
    "creature_removal": "creature-removal",      # 5511
    "artifact_removal": "artifact-removal",      # 1190
    "enchantment_removal": "enchantment-removal",  # 1038
    "mass_removal": "mass-removal",              # 943  (same set as sweeper)
    "board_wipe": "board-wipe",                  # 943  (same set as sweeper)
    # --- the mana base ----------------------------------------------------
    "mana_rock": "mana-rock",                    # 384
    "mana_dork": "mana-dork",                    # 441
    "land_ramp": "land-ramp",                    # 621
    "mana_filter": "mana-filter",                # 220
    "mana_sink": "mana-sink",                    # 1866
    "ritual": "ritual",                          # 69
    # --- engine and package pieces ----------------------------------------
    "wheel": "wheel",                            # 149
    "theft": "theft",                            # 724
    "sacrifice_outlet": "sacrifice-outlet",      # 1484
    "discard_outlet": "discard-outlet",          # 1326
    "untapper": "untapper",                      # 763
    "blink": "blink",                            # 198
    "clone": "clone",                            # 71
    "bounce": "bounce",                          # 928
    "self_mill": "self-mill",                    # 1061
    "mill": "mill",                              # 1294
    "lifegain": "lifegain",                      # 2598
    "counters_matter": "counters-matter",        # 1270
    "landfall": "landfall",                      # 286
    "evasion": "evasion",                        # 5354
    "anthem": "anthem",                          # 536
    "extra_combat": "extra-combat",              # 45
    # --- interaction and hate ---------------------------------------------
    "graveyard_hate": "graveyard-hate",          # 419
    "hate": "hate",                              # 4511
    "hatebear": "hatebear",                      # 66
    "tax": "tax",                                # 466
    "pillowfort": "pillowfort",                  # 62
    "group_hug": "group-hug",                    # 413
    "fog": "fog",                                # 93
}

#: Plausible tag names that do NOT resolve — probed 2026-10-01, every one of
#: them HTTP 404. Kept as data, not as a comment, because the failure mode is
#: a silent empty result: shipping any of these would look like "no cards match
#: your colours" rather than "that tag does not exist".
REJECTED: tuple[str, ...] = (
    "token-generation", "cost-reduction", "stax", "land-destruction",
    "mass-land-destruction", "haste-enabler", "card-selection", "creature-tutor",
    "free-spell", "mld", "land-hate", "resource-denial", "mana-denial", "taxing",
    "infinite-combo", "combo", "wincon", "mana-ritual", "treasure", "reanimation",
    "tokens", "token", "proliferate", "land-tutor", "extra-turns",
    "stack-interaction", "indestructible", "hexproof-granter",
    "counterspell-protection", "fixing", "mana-fixing", "color-fixing",
)

#: The `otag:` value to search for each of our own Function tags.
#:
#: Two Function members are deliberately absent:
#:
#: * `LAND` — Scryfall answers this better with `t:land` than any oracle tag.
#: * `SYNERGY` — our catch-all for "performs no named function", which is a
#:   property of our own classifier, not a thing a human would tag a card with.
#:
#: `MASS_LAND_DENIAL` was listed here as having no equivalent. That was wrong:
#: `otag:mass-land-denial` resolves to 106 commander-legal cards (Armageddon,
#: Apocalypse, Acid Rain, Ajani Vengeant). Four other spellings do 404, which is
#: how the mistake happened, and the one that works is the Function value itself.
#:
#: **The tag is broader than our regex, and the two are not interchangeable.**
#: Tagger includes land LOCKS — Winter Orb, Blood Moon, Back to Basics — which
#: deny land use without destroying anything, while `classify.py` matches only
#: destruction and mass sacrifice. Scored over the corpus, our regex is recall
#: 0.21 at precision 0.69 against this tag. So `find mass_land_denial` is the
#: right way to DISCOVER these cards, and `classify` remains the right thing for
#: the bracket rule, which is written about destruction. Do not treat agreement
#: between them as expected.
FUNCTION_TAGS: dict[Function, str] = {
    F.RAMP: "ramp",
    F.DRAW: "draw",
    F.SPOT_REMOVAL: "spot_removal",
    F.SWEEPER: "sweeper",
    F.TUTOR: "tutor",
    F.COUNTERSPELL: "counterspell",
    F.PROTECTION: "protection",
    F.RECURSION: "recursion",
    F.EXTRA_TURNS: "extra_turns",
    F.WINCON: "wincon",
    F.MASS_LAND_DENIAL: "mass_land_denial",
}

#: Functions with no verified `otag:` equivalent. See `FUNCTION_TAGS`.
UNMAPPED_FUNCTIONS: frozenset[Function] = frozenset(set(Function) - set(FUNCTION_TAGS))

#: Which of our own Function tags should appear on a card the community gave
#: this otag. Agreement is a non-empty intersection, not equality: a card may
#: do several things, and `otag:removal` covers both of our removal tags.
#:
#: A label absent from this mapping is searchable but not cross-checkable —
#: `classify.py` has no notion of "wheel" or "theft", so claiming agreement or
#: disagreement there would be inventing a verdict.
CROSS_CHECK: dict[str, frozenset[Function]] = {
    "ramp": frozenset({F.RAMP}),
    "mana_rock": frozenset({F.RAMP}),
    "mana_dork": frozenset({F.RAMP}),
    "land_ramp": frozenset({F.RAMP}),
    "draw": frozenset({F.DRAW}),
    "card_advantage": frozenset({F.DRAW}),
    "wheel": frozenset({F.DRAW}),
    "spot_removal": frozenset({F.SPOT_REMOVAL}),
    "creature_removal": frozenset({F.SPOT_REMOVAL, F.SWEEPER}),
    "removal": frozenset({F.SPOT_REMOVAL, F.SWEEPER}),
    "sweeper": frozenset({F.SWEEPER}),
    "mass_removal": frozenset({F.SWEEPER}),
    "board_wipe": frozenset({F.SWEEPER}),
    "tutor": frozenset({F.TUTOR}),
    "counterspell": frozenset({F.COUNTERSPELL}),
    "protection": frozenset({F.PROTECTION}),
    "recursion": frozenset({F.RECURSION}),
    "extra_turns": frozenset({F.EXTRA_TURNS}),
    "wincon": frozenset({F.WINCON}),
    # Present but expected to disagree: the tag covers land locks and our regex
    # covers destruction. See FUNCTION_TAGS. Reported rather than hidden.
    "mass_land_denial": frozenset({F.MASS_LAND_DENIAL}),
}

#: Label for each otag value, so the hyphenated form resolves even where it
#: differs from our label. `function_tag("extra-turn")` and
#: `function_tag("win-condition")` used to raise despite the docstring promising
#: the hyphenated otag works: normalising "extra-turn" gives "extra_turn", and
#: the label is "extra_turns".
_BY_OTAG = {value: label for label, value in TAGS.items()}

#: Colour-identity letters Scryfall's `ci:` accepts. `c` means colourless.
_IDENTITY_LETTERS = frozenset("wubrgc")


def vocabulary() -> tuple[str, ...]:
    """Every label `function_tag` accepts, sorted."""
    return tuple(sorted(TAGS))


def function_tag(label: str) -> str:
    """The verified `otag:` value for a label. Raises ValueError if unknown.

    Accepts the snake_case label, the hyphenated otag itself, and a `Function`
    value (which is the same string as the label wherever one exists). Raising
    rather than passing the name through is deliberate: an unverified tag comes
    back from Scryfall as a 404, indistinguishable from "no cards in those
    colours", so a typo would read as an empty answer instead of a mistake.
    """
    if isinstance(label, Function):
        label = label.value
    raw = str(label).strip().casefold()
    if raw in _BY_OTAG:
        return raw
    key = raw.replace("-", "_")
    if key in TAGS:
        return TAGS[key]
    raise ValueError(
        f"Unknown function tag {label!r}. Scryfall answers an unrecognised "
        f"otag: with 404, which would read as an empty result rather than an "
        f"error, so only probed tags are allowed. Expected one of: "
        f"{', '.join(vocabulary())}."
    )


def canonical_label(label: str) -> str:
    """The canonical snake_case label for `label`. Raises ValueError if unknown."""
    if isinstance(label, Function):
        label = label.value
    raw = str(label).strip().casefold()
    if raw in _BY_OTAG:
        return _BY_OTAG[raw]
    key = raw.replace("-", "_")
    if key not in TAGS:
        function_tag(label)  # raises with the full vocabulary in the message
    return key


def cross_check_functions(label: str) -> frozenset[Function]:
    """Our Function tags that should agree with this otag, or empty if none."""
    return CROSS_CHECK.get(canonical_label(label), frozenset())


def identity_filter(identity: str | None) -> str:
    """Normalize a colour identity into a `ci:` clause, or "" for None.

    Accepts "wubg", "WUBG", "w,u,b,g" and "{W}{U}". Raises ValueError on a
    letter that is not a colour, so a typo'd identity fails loudly instead of
    silently searching the whole format.
    """
    if identity is None:
        return ""
    letters = [c for c in str(identity).casefold() if c.isalnum()]
    if not letters:
        return ""
    unknown = sorted(set(letters) - _IDENTITY_LETTERS)
    if unknown:
        raise ValueError(
            f"Unknown colour identity letter(s) {''.join(unknown)!r} in "
            f"{identity!r}. Use w, u, b, r, g, or c for colourless."
        )
    # Order is normalized so two spellings of the same identity produce one
    # query string, which keeps a cached or logged query comparable.
    ordered = "".join(c for c in "wubrgc" if c in set(letters))
    return f"ci:{ordered}"


def build_query(
    label: str, *, identity: str | None = None, extra: str | None = None
) -> str:
    """The Scryfall query for a function search.

    `legal:commander` is always applied: every operation in this toolkit is
    about Commander, and an illegal card is not a candidate. Ordering is left to
    `ScryfallClient.search`, which already asks for `order=edhrec` so the
    most-played candidates arrive first.
    """
    parts = [f"otag:{function_tag(label)}", "legal:commander"]
    ci = identity_filter(identity)
    if ci:
        parts.append(ci)
    if extra:
        parts.append(f"({extra.strip()})")
    return " ".join(parts)
