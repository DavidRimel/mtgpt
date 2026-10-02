# mtgpt/classify.py
"""Tag each card with the functions it performs.

Classification is heuristic and deliberately visible: the report prints tags
per card so a wrong tag can be spotted and corrected, rather than silently
skewing the ratios it feeds.

Two rules deserve their reasoning stated:

* Ramp needs a mana-adding pattern in the oracle text. Scryfall's
  `produced_mana` is populated for cards that merely make Treasure or similar,
  so the field alone produces false positives.
* A search restricted to lands is ramp, not a tutor. Bracket rules count
  tutors as combo assembly and answer-finding; counting Cultivate would
  misreport tutor density.
"""

from __future__ import annotations

import re

from .models import Card, Function, ResolvedDeck

F = Function

_ADDS_MANA = re.compile(
    r"(\{T\}\s*:\s*Add|\badd one mana\b|\badd two mana\b|\badd \{|"
    r"\badds? .{0,20}mana\b|create .{0,20}\bTreasure\b)",
    re.IGNORECASE,
)
_SEARCH_LIBRARY = re.compile(r"search your library", re.IGNORECASE)
#: A land fetch may name a basic land TYPE and may quantify indirectly
#: ("that many land cards", Scapeshift). Sentence-bounded so a tutor for a
#: nonland card cannot match a later mention of lands.
_LAND_SEARCH = re.compile(
    r"search your library for [^.]{0,40}?"
    r"\b(?:lands?|Plains|Island|Swamp|Mountain|Forest|Wastes)\b",
    re.IGNORECASE,
)
_DRAW = re.compile(
    r"\bdraws?\s+(?:a\s+card|one|two|three|four|five|six|seven|eight|nine|ten|X|\d+)\b",
    re.IGNORECASE,
)
#: "Whenever an opponent draws a card" is not card draw for us. Masked out before
#: _DRAW runs, so Smothering Tithe counts as ramp only.
_OPPONENT_DRAW = re.compile(
    r"\bopponents?\s+draws?\s+(?:a\s+card|\w+\s+cards?)", re.IGNORECASE
)
#: Blink of your OWN creature is protection, not removal: Ephemerate and
#: Restoration Angel both exile a creature you control and return it.
_SELF_BLINK = re.compile(
    r"exile\s+(?:\w+\s+){0,3}?target\s+[^.]{0,45}?you control,?\s+(?:then\s+)?return",
    re.IGNORECASE,
)
#: Spot removal. Three things this must get right:
#:  - "you control" excludes blink effects (Ephemerate, Restoration Angel).
#:  - "gets" needs a MINUS sign, or Giant Growth's +3/+3 reads as removal.
#:  - damage is removal too (Lightning Bolt, Flame Slash), but "to each
#:    creature" is a sweeper and is excluded by requiring a single target.
_SPOT_REMOVAL = re.compile(
    r"(?:destroy|exile)\s+(?:up to \w+ )?target\b(?![^.]{0,45}?you control)|"
    r"target\s+(?:creature|permanent|player)\s+(?:gets\s+-|sacrifices)|"
    r"deals\s+\S+\s+damage to (?:any target|target \w+)|"
    r"return target [^.]{0,60}?to (?:its|their) owner'?s hand",
    re.IGNORECASE,
)
#: A sweeper must name a NON-LAND permanent type. Both branches use the same
#: affirmative test: an earlier draft used a negative "no land mentioned" test on
#: the sacrifice branch, which wrongly suppressed Catch // Release ("Each player
#: sacrifices an artifact, a creature, an enchantment, a land, and a
#: planeswalker") because a land appears alongside four non-land types.
_SWEEPER = re.compile(
    r"(?:destroy|exile)\s+(?:all|each|every)\s+[^.]{0,30}?"
    r"\b(?:creature|permanent|artifact|enchantment|planeswalker|token|battle)s?\b|"
    r"each player sacrifices[^.]{0,40}?"
    r"\b(?:creature|permanent|artifact|enchantment|planeswalker|token|battle)s?\b|"
    r"deals \S+ damage to each (?:creature|other creature)|"
    r"all creatures get -|"
    # Mass bounce is a pseudo-wrath: Evacuation outright, and Cyclonic Rift once
    # overload has substituted "each" for "target".
    r"return (?:all|each|every)\s+[^.]{0,40}?"
    r"\b(?:creature|permanent|artifact|enchantment|planeswalker|token)s?\b"
    r"[^.]{0,40}?to (?:its|their) owner",
    re.IGNORECASE,
)
#: Mass land denial, including the sacrifice form (Bust).
_MASS_LAND_DENIAL = re.compile(
    r"(?:destroy|exile)\s+(?:all|each)\b[^.]{0,60}?\blands?\b|"
    r"each player sacrifices[^.]{0,40}?\blands?\b",
    re.IGNORECASE,
)
#: Real counterspells rarely read "counter target spell": Swan Song says
#: "Counter target enchantment, instant, or sorcery spell", Dovin's Veto says
#: "noncreature spell". A window between "target" and "spell" catches them.
_COUNTERSPELL = re.compile(r"counter target\b.{0,60}?\b(?:spell|ability)\b", re.IGNORECASE)
#: Protection means protection GRANTED, not protection possessed.
#:
#: Scryfall's `keywords` is populated for self-granted keywords, and the oracle
#: text spells them out the same way, so a bare "Hexproof" or "<name> is
#: indestructible" used to tag Blightsteel Colossus, Carnage Tyrant and Toski as
#: the deck's protection package. The band is 3-5, so three such fatties filled
#: it and the user was told to add no protection — then lost the commander to the
#: next Swords to Plowshares. Hence the grant verb: "creatures you control gain
#: hexproof", "target creature gains indestructible", "your permanents have
#: hexproof". A keyword a card merely has is not a protection effect.
_PROTECTION = re.compile(
    r"protection from|"
    r"\b(?:gain|gains|have|has)\b[^.]{0,40}?"
    r"\b(?:hexproof|indestructible|shroud|ward)\b|"
    r"\bphases? out\b|can't be countered|sacrifice .{0,30}\binstead\b",
    re.IGNORECASE,
)
#: A counterspell or creature saying it can't be countered tells us nothing about
#: the deck's resilience package. Masked before _PROTECTION runs.
_SELF_UNCOUNTERABLE = re.compile(
    r"\bthis (?:spell|card|creature|permanent) can't be countered", re.IGNORECASE
)
#: Verbs that make the preceding noun the clause's SUBJECT. Combined with the
#: card's own name, these find the clauses a card applies only to itself:
#: "Blightsteel Colossus is indestructible", "Tromokratis has hexproof unless
#: it's attacking", "Carnage Tyrant can't be countered".
#:
#: Only the clause is dropped, never the whole line. An oracle line can name the
#: card as a COST and still grant to others — "Sacrifice Zack Fair: Target
#: creature you control gains indestructible" — and blanking the line would also
#: lose Archangel Avacyn's board-wide indestructible and Spectacular Spider-Man's.
#: Measured against 400 real Commander-legal cards that grant hexproof or
#: indestructible; blanking whole lines dropped four of them.
_SELF_SUBJECT_VERBS = r"(?:is|are|has|have|gains?|becomes?|can't be countered)"
#: Overload replaces every "target" with "each", turning a spot-removal spell
#: into a pseudo-wrath. Cyclonic Rift and Vandalblast are the format's two
#: most-played sweepers and were tagged `spot_removal` only; the Sweeper band is
#: 2-3, so missing one is a third of the band.
_OVERLOAD_KEYWORD = "Overload"
#: "takes an extra turn", but also Time Stretch's "takes two extra turns".
_EXTRA_TURNS = re.compile(r"takes?\s+\w+\s+extra\s+turns?", re.IGNORECASE)
#: "you lose the game" is a drawback (Demonic Pact, Pact of Negation), not a
#: win condition. "Target player loses the game" still counts.
_WINCON = re.compile(r"\bwins? the game\b|(?<!you )\bloses? the game\b", re.IGNORECASE)
#: Parenthetical reminder text.
#:
#: Stripped for two of the recursion branches and deliberately NOT for the rest,
#: because it cuts both ways and the corpus says which way per branch.
#:
#: Against it: a keyword's reminder restates the keyword in sentences that read
#: exactly like real recursion. Pteramander's adapt reminder says "put four
#: +1/+1 counters on it", Varolz's scavenge reminder says "exile a creature card
#: from your graveyard", Relentless Skaabs' undying reminder says "return it to
#: the battlefield", and every flashback card's reminder says "you may cast this
#: card from your graveyard". All of it was being read as recursion.
#:
#: For it: unearth and disturb state their ENTIRE effect in reminder text. Royal
#: Warden's only recursion sentence is inside "({3}{B}: Return this card from
#: your graveyard to the battlefield...)". Stripping reminders everywhere cost
#: 170 true positives across the corpus.
#:
#: So branches 1 and 4-7 read reminder text and branches 2-3 do not. The split is
#: measured, not aesthetic: every reminder-text false positive was in 2 or 3, and
#: every reminder-text true positive was in the others. It is also why plain
#: flashback (Lingering Souls, Call of the Herd, Deep Analysis) stays out —
#: its only graveyard-cast sentence is a reminder, and branch 3 cannot see it.
_REMINDER = re.compile(r"\([^()]*\)")

#: What a recovery verb must be acting ON for branch 2 to fire.
#:
#: Branch 2 crosses a sentence boundary, so without this it only needed a
#: graveyard and a later "put" inside the window. That let through every
#: graveyard-count payoff that puts +1/+1 COUNTERS (Gixian Skullflayer, Grave
#: Strength, Obsessive Skinner, Pyromancer Ascension's quest counter) and, worst,
#: Cry of the Carnarium — "exile all creature cards in all graveyards that were
#: PUT THERE from the battlefield", a graveyard-hate card reading as a
#: reanimation spell. The verb now has to take the graveyard card as its object.
_GRAVEYARD_OBJECT = (
    r"(?:it|them|those\s+\w*\s?cards?|that\s+card|each\s+card|"
    r"the\s+(?:chosen|exiled|voted|revealed)\s+cards?|"
    r"enchanted\s+creature(?:\s+card)?)"
)

#: Recursion: getting a card out of a graveyard and using it again.
#:
#: Seven templatings, because Magic writes it seven ways and the first version of
#: this pattern read only one — `return ... from ... graveyard`. That missed the
#: entire reanimation archetype: Reanimate, Animate Dead, Victimize, Necromancy
#: and Rise of the Dark Realms all came back `synergy`.
#:
#: The failure in the other direction is graveyard HATE and graveyard COST, both
#: of which read almost identically to a regex and mean the opposite thing.
#:
#: Every guard here was chosen by scoring against all 32,116 commander-legal
#: cards — Scryfall publishes the whole Tagger vocabulary and its taggings as
#: bulk data, so this is measurable rather than sampleable. The unguarded version
#: scored recall 0.73 at precision 0.92: 135 false positives, including the whole
#: Skaab class ("as an additional cost to cast this spell, exile a creature card
#: from your graveyard", which CONSUMES the graveyard), the Increasing cycle, and
#: Cry of the Carnarium.
#:
#: Two candidate guards were measured and REJECTED rather than shipped, which is
#: the part worth keeping in mind before adding a third:
#:
#: * Excluding self-return ("Return this card from your graveyard") would have
#:   cost 263 true positives to remove 6 false ones. Unearth, disturb and the
#:   whole "{cost}: Return this card from your graveyard" class are recursion in
#:   any deck that plays them, and Scryfall Tagger counts all of it. An earlier
#:   commit message claimed this exclusion as the design; the measurement says
#:   the claim was wrong, not the code.
#: * Excluding an opponent's graveyard wholesale would have cost 11 true
#:   positives (Puppeteer Clique, Geth, Gruesome Encore, Macabre Mockery, Ashen
#:   Powder) to remove 3. Narrowed to the Advocate templating instead.
_RECURSION_BRANCHES = (
    # 1. "Return/Put <card> FROM a graveyard TO/ONTO <zone>". Regrowth, Eternal
    #    Witness, Reanimate, Persist, Sun Titan, Karmic Guide, Reveillark,
    #    Unburial Rites, Goryo's Vengeance, Rise of the Dark Realms, Noxious
    #    Revival, Twilight's Call, Patriarch's Bidding, and every unearth or
    #    disturb card through its reminder text. 1286 of the corpus's true
    #    positives come from this branch alone.
    #
    #    GUARD: "from" must PRECEDE the graveyard. That ordering is the whole
    #    defence against the hate templating "put into a graveyard from
    #    anywhere, exile it instead" — Rest in Peace, Leyline of the Void,
    #    Planar Void, Anafenza, Syr Konrad — where the graveyard comes first and
    #    nothing is recovered. Pinned by `test_guard_...` below.
    #    GUARD: not out of an opponent's graveyard back into their own hand.
    #    That is the Advocate cycle handing a card back, not recursion. On top of
    #    their library (Misinformation) or onto the battlefield under your
    #    control (Puppeteer Clique) still counts.
    r"(?:returns?|puts?)\s[^.]{0,80}?\bfrom\s[^.]{0,30}?graveyards?\b"
    r"[^.]{0,60}?\b(?:to|onto|on top of)\b(?![^.]{0,20}?\btheir hand\b)",
    # 5. Exile from a graveyard and then put what was exiled onto the
    #    battlefield, in one sentence: Living Death.
    #
    #    GUARD: the second `exiled` is required, so the thing entering the
    #    battlefield is the thing that left the graveyard. Without it, "Exile
    #    this card from your graveyard: Search your library for a Desert card,
    #    put it onto the battlefield" (Colossal Rattlewurm, Everbark Shaman)
    #    read as recursion while actually paying the graveyard as a cost.
    r"\bexiles?\b[^.]{0,80}?\bfrom\s[^.]{0,30}?graveyards?\b"
    r"[^.]{0,140}?\bexiled\b[^.]{0,60}?\bonto the battlefield\b",
    # 4. Granting castability to cards already in a graveyard rather than moving
    #    them: Past in Flames ("gains flashback"), Underworld Breach ("has
    #    escape"), Snapcaster Mage ("gains flash"). `has` matters: Underworld
    #    Breach states the grant that way, and its only "may cast" is a reminder.
    r"\b(?:cards?|spells?)\b[^.]{0,40}?\bin\s+(?:your|a|all|their)\s*graveyards?\b"
    r"[^.]{0,80}?\b(?:gains?|has|have)\b[^.]{0,40}?"
    r"\b(?:flashback|flash|escape|retrace)\b",
    # 6. Searching a graveyard and putting what you find into play or hand:
    #    Finale of Devastation, Ecological Appreciation, Vraska's Scorn.
    #
    #    GUARD: the graveyard must precede the put, which excludes "search your
    #    library ... and put them INTO your graveyard" (Buried Alive, Entomb).
    #    Filling a graveyard is not emptying one.
    r"\bsearch\b[^.]{0,60}?\bgraveyards?\b[^.]{0,80}?\bput\b"
    r"[^.]{0,40}?\b(?:onto the battlefield|into your hand)\b",
    # 7. The same movement as branch 1 with the clauses in the other order:
    #    "Return to the battlefield tapped all artifact cards in your graveyard"
    #    (Gerrard's Hourglass Pendant), "return to your hand target creature card
    #    in your graveyard" (Storrev). Destination named before the source.
    r"\b(?:returns?|puts?)\s+(?:to|onto)\s+(?:the\s+battlefield|your\s+hand)"
    r"[^.]{0,80}?\bin\s+(?:your|a|all|their)\s*graveyards?\b",
)

#: Branches that must not read reminder text. See `_REMINDER`.
_RECURSION_BRANCHES_NO_REMINDERS = (
    # 2. "<card> IN a graveyard ... return/put/cast <that card>". The
    #    choose-then-act family, whose two halves sit in different sentences:
    #    Victimize, Meren, Command the Dreadhorde, Breach the Multiverse,
    #    Snapcaster Mage, Emry, and the Aura reanimators (Animate Dead, Dance of
    #    the Dead).
    #
    #    GUARD: `in\s`, not `in`. "put INTO a graveyard" is how every hate card
    #    and every self-mill trigger is worded, and `\bin\s` refuses it. Pinned
    #    by `test_guard_...` below.
    #    GUARD: the recovery verb must take the graveyard card as its object.
    #    `that\s` is in the determiner list for Breach the Multiverse, which says
    #    "that player's graveyard" — the same incomplete-alternation bug branch 1
    #    already had with "their graveyard".
    r"\bcards?\b[^.]{0,40}?\bin\s+"
    r"(?:your\s|a\s|an\s|their\s|all\s|each\s|target\s|that\s)?"
    r"(?:opponent'?s?\s|player'?s?\s)?graveyards?\b"
    r".{0,160}?\b(?:(?:returns?|puts?)\s+" + _GRAVEYARD_OBJECT +
    r"[^.]{0,40}?\b(?:to|onto|on top of)\b|cast\s+" + _GRAVEYARD_OBJECT + r")",
    # 3. "you MAY cast/play <something> from your graveyard". The
    #    graveyard-as-second-hand engines: Yawgmoth's Will, Crucible of Worlds,
    #    Ramunap Excavator, Muldrotha, Chainer, and the permanents that recast
    #    themselves (Marang River Prowler, Gravecrawler).
    #
    #    GUARD: `may` is required, and it is what separates an ENABLER from the
    #    two things that are not one. "As an additional cost to cast this spell,
    #    exile a creature card from your graveyard" CONSUMES the graveyard — the
    #    entire Skaab class, Skeletal Scrying, Corpse Lunge, Harvest Pyre, Chill
    #    Haunting. "If this spell WAS CAST from a graveyard" is a flashback rider
    #    — the whole Increasing cycle, and Sevinne's Reclamation's second
    #    sentence. Neither says "may cast". Pinned by `test_guard_...` below.
    #
    #    There is deliberately no "not this card" guard here. One was tried: it
    #    cost 31 true positives (unearth, disturb, escape, Gravecrawler) to
    #    remove 4, because stripping reminder text already excludes plain
    #    flashback, which was the only class it was wanted for.
    r"\bmay\b[^.]{0,40}?\b(?:cast|play)\b[^.]{0,60}?"
    r"\bfrom\s(?:among\s+cards\s+in\s+)?(?:your|a|their|the|that player's)\s+graveyard",
)

_RECURSION = tuple(re.compile(b, re.IGNORECASE) for b in _RECURSION_BRANCHES)
_RECURSION_NO_REMINDERS = tuple(
    re.compile(b, re.IGNORECASE) for b in _RECURSION_BRANCHES_NO_REMINDERS
)


def classify(card: Card) -> frozenset[Function]:
    """Return every function this card performs.

    Lands short-circuit: a land is tagged LAND and nothing else, so a utility
    land's tap-for-mana text does not also register it as ramp.
    """
    if card.is_land:
        return frozenset({F.LAND})

    text = card.oracle_text or ""
    tags: set[Function] = set()

    if _is_ramp(card, text):
        tags.add(F.RAMP)
    if _SEARCH_LIBRARY.search(text) and not _LAND_SEARCH.search(text):
        tags.add(F.TUTOR)
    if _DRAW.search(_OPPONENT_DRAW.sub(" ", text)):
        tags.add(F.DRAW)
    if _COUNTERSPELL.search(text):
        tags.add(F.COUNTERSPELL)
    if _MASS_LAND_DENIAL.search(text):
        tags.add(F.MASS_LAND_DENIAL)
    if _SWEEPER.search(text):
        tags.add(F.SWEEPER)
    if (
        _SPOT_REMOVAL.search(text)
        and F.SWEEPER not in tags
        and F.COUNTERSPELL not in tags
    ):
        tags.add(F.SPOT_REMOVAL)
    # Blink of your own creature is protection; make sure it is not removal.
    if _SELF_BLINK.search(text):
        tags.add(F.PROTECTION)
        tags.discard(F.SPOT_REMOVAL)
    if _PROTECTION.search(_without_self_clauses(card, text)):
        tags.add(F.PROTECTION)
    if _is_overload_sweeper(card, text):
        # Added after the spot-removal decision, not before: an overload card is
        # genuinely both modes, and the `F.SWEEPER not in tags` guard above would
        # otherwise strip the targeted mode it still has.
        tags.add(F.SWEEPER)
    if _EXTRA_TURNS.search(text):
        tags.add(F.EXTRA_TURNS)
    if _WINCON.search(text):
        tags.add(F.WINCON)
    if _is_recursion(card, text):
        tags.add(F.RECURSION)

    return frozenset(tags) if tags else frozenset({F.SYNERGY})


def _is_ramp(card: Card, text: str) -> bool:
    """Ramp is either an explicit mana-adding effect or a land fetch.

    Requiring the text pattern is what keeps `produced_mana` noise out.
    """
    if _LAND_SEARCH.search(text):
        return True
    if not _ADDS_MANA.search(text):
        return False
    # A creature or artifact that adds mana is ramp; a land was excluded above.
    return True


def _without_self_clauses(card: Card, text: str) -> str:
    """Drop the clauses in which the card itself is the subject.

    What is left is what the card does for the rest of the deck, which is what
    PROTECTION is meant to measure. Scryfall writes self-reference by the card's
    own name, so the name plus a linking verb is the span to remove — and only
    that span, because the same line may name the card as a cost and then grant
    a keyword to other permanents.
    """
    masked = _SELF_UNCOUNTERABLE.sub(" ", text)
    front = card.name.partition("//")[0].strip()
    if not front:
        return masked
    self_clause = re.compile(
        re.escape(front) + r"\s+" + _SELF_SUBJECT_VERBS + r"\b[^.\n]{0,80}",
        re.IGNORECASE,
    )
    return self_clause.sub(" ", masked)


def _is_recursion(card: Card, text: str) -> bool:
    """True when the card gets something out of a graveyard and uses it again.

    A clause whose object is this card BY NAME is dropped first. Rekindling
    Phoenix says "return target card named Rekindling Phoenix from your graveyard
    to the battlefield" — a token bringing the Phoenix back, which Tagger does
    not count and which the `this card` wording would not have caught.

    Reminder text is then stripped for branches 2 and 3 only; `_REMINDER`
    explains why the other branches must keep reading it.
    """
    body = text
    front = card.name.partition("//")[0].strip()
    if front:
        # The whole clause goes, not just the name: blanking the name alone
        # leaves "return target  from your graveyard to the battlefield", which
        # still matches branch 1.
        names_self = re.compile(
            r"\bcards?\s+named\s+" + re.escape(front) + r"\b", re.IGNORECASE
        )
        body = ". ".join(
            clause for clause in body.split(".") if not names_self.search(clause)
        )
    if any(branch.search(body) for branch in _RECURSION):
        return True
    without_reminders = _REMINDER.sub(" ", body)
    return any(branch.search(without_reminders) for branch in _RECURSION_NO_REMINDERS)


def _is_overload_sweeper(card: Card, text: str) -> bool:
    """True when overloading this spell turns it into a sweeper.

    The oracle reminder text states the substitution verbatim — 'change "target"
    in its text to "each"' — so applying it and re-running the sweeper test is
    reading the card rather than guessing at it.
    """
    if _OVERLOAD_KEYWORD not in card.keywords:
        return False
    return bool(_SWEEPER.search(text.replace("target", "each")))


def classify_deck(deck: ResolvedDeck) -> dict[str, frozenset[Function]]:
    """Classify every distinct card in the deck, keyed by card name."""
    tags = {card.name: classify(card) for _, card in deck.cards}
    for commander in deck.commanders:
        tags[commander.name] = classify(commander)
    return tags
