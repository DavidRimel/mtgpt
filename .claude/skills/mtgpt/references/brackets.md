# Commander brackets

WotC's bracket system describes what a deck is trying to do, so players can
match expectations before shuffling. The machine-readable rules are in
`mtgpt/brackets.py` (`RULES`); this table mirrors it exactly.

| Bracket | Name | Game Changers max | Mass land denial | Extra turns | Tutors | Two-card combos |
|---|---|---|---|---|---|---|
| 1 | Exhibition | 0 | excluded | watched | minimal | excluded |
| 2 | Core | 0 | excluded | watched | sparse | excluded |
| 3 | Upgraded | 3 | excluded | watched | unrestricted | late-game only (warned, not blocked) |
| 4 | Optimized | unrestricted | allowed | unrestricted | unrestricted | allowed |
| 5 | cEDH | unrestricted | allowed | unrestricted | unrestricted | allowed |

"Watched" means mtgpt raises a warning once extra-turn spell count reaches
`EXTRA_TURN_WARN_AT` (3); it does not mean extra turns are banned outright at
brackets 1-3, only that chaining them is against the bracket's intent.
"Minimal"/"sparse" tutor guidance triggers a warning once `tutor_count`
reaches `TUTOR_WARN_AT` (4). A Game Changers count over the bracket's max, or
mass land denial present where it isn't allowed, is an **error** (fails
`compliant`); extra-turn density and tutor density are **warnings** (do not
fail `compliant` on their own).

Two-card infinite combos are not uniform across brackets 1-3. WotC's rule
bans them outright at brackets 1 and 2 (`two_card_combos = "banned"` on
`BracketRule`, an **error**), but bracket 3 permits one only as a *late-game*
finish, not an early-game line (`two_card_combos = "late_only"`). mtgpt has no
reliable signal for combo speed — that depends on the deck's tutors, ramp,
and the pilot — so guessing in either direction would be worse than saying
nothing: guessing "banned" wrongly fails a legal bracket-3 deck, and guessing
"fine" wrongly passes an illegal one. So at bracket 3 a detected two-card
combo is reported as a **warning** (`code: "two_card_combo"`, same code as
the bracket 1-2 error) that states the rule and hands the judgment to the
pilot, rather than failing `compliant`.

## The Game Changers list

Maintained by WotC and revised over time, so mtgpt fetches it from Scryfall's
`is:gamechanger` rather than hardcoding it. A deck that goes over its
bracket's allowance is reported as an error (`code: "game_changers"`) naming
the offending cards.

## What mtgpt checks, and what it does not

Checked from card data alone (via `mtgpt bracket` / `mtgpt report`):

- Game Changer count (`game_changers`)
- Mass land denial (`mass_land_denial`), from the `MASS_LAND_DENIAL` function tag
- Extra-turn spell density (`extra_turns`) — a count, which approximates
  "not chained," not a detection of chaining
- Tutor density (`tutor_count`), with land fetches excluded (they are tagged
  `ramp`, not `tutor`, in `mtgpt/classify.py`)

Not checked unless `report --combos` is passed:

- **Two-card infinite combos.** This needs Commander Spellbook data. Without
  `--combos`, every `bracket`/`report` call lists
  `"Two-card infinite combo detection requires Commander Spellbook (not
  supplied)."` in `deferred_checks`. A deck that passes at bracket 1 or 2
  without `--combos` may still be running an infinite combo. Even with
  `--combos` supplied, bracket 3 only ever warns on a detected two-card
  combo, never fails `compliant` — mtgpt cannot tell a permitted late-game
  finish from a banned early-game line, so it defers that one judgment to
  the pilot instead of guessing.
- **Repeatable extra turns.** Even with `--combos` supplied, counting
  extra-turn spells is always a proxy for chaining, not a detection of it —
  `deferred_checks` names this gap (`EXTRA_TURN_APPROXIMATION`)
  unconditionally, in every report.

Say so when reporting a verdict. A bracket claim that hides a skipped rule is
worse than no claim.
