# mtgpt — decisions made during implementation

Recorded 2026-10-01. Every judgment call made on the user's behalf while executing
`docs/superpowers/plans/2026-09-30-mtgpt-layer1.md`, preserved here because the
execution ledger it came from is scratch and gets deleted.

Each entry states what was decided, why, and what it costs if the decision was wrong.

Total decisions recorded: 53

---

Ruling: T10's Interfaces line claiming it consumes `parse` from T2 is a documentation
error — `moxfield.fetch_decklist` returns raw text and `cli` does the parsing. Implementer
told to ignore that line; the code block is authoritative. Cost if wrong: none, the code
block is unambiguous.

Ruling: T1 must omit the unused `field` import from `dataclasses` in models.py. The plan's
code block includes it but nothing uses it. Cost if wrong: none; a lint-only change.

Ruling: `render()` must print a per-card function-tag section, making `tags` a used
parameter. The spec's Risk 3 mitigation is "surfacing tags in the report so a bad tag is
visible and correctable", and the plan's text render omitted it — so the plan under-delivers
a spec requirement, and separately leaves a dead parameter a reviewer would flag. Spec is
the binding authority. T8 dispatch carries this plus a test asserting a known tag appears in
the output. Cost if wrong: a longer report; the `--json` path already exposed tags, so the
risk is verbosity, not correctness.

Ruling: T10's optional `browser` extra does not violate the Global Constraint "Zero
third-party runtime dependencies". `moxfield.py` imports playwright lazily inside
`_default_page_factory`, so no core path imports it at module load, and the constraint
names runtime deps, not optional extras. Cost if wrong: a reviewer re-flags it; the
constraint would then need rewording rather than the code changing.

Ruling: Task 1's implementer rewrote the commit trailer to name its own model
("Claude Haiku 4.5") instead of the session-mandated `Co-Authored-By: Claude Opus 5
(1M context)`. Amended to the mandated line (508fdb6 -> 1830a6f, local unpushed branch so
the amend is cheap). All later dispatches instruct implementers to copy the trailer verbatim
and not substitute their own model name. Rationale: the session attribution instruction is
explicit, and ten commits with drifting trailers makes an inconsistent history. Cost if
wrong: the trailer under-credits which model wrote each commit; recoverable by rewriting
trailers later, and the ledger records who actually implemented each task.

Ruling: FIX the Important finding (_TAG_RE `\s+#(?P<tag>\S+)` mishandles a multi-word
  category). Controller verified independently: `1 Cultivate (M21) 177 #Ramp, Draw` yields
  name="Cultivate (M21) 177 Draw", set_code=None. The damage is not the wrong category —
  nothing in Layer 1 reads `category` — it is the corrupted NAME, which then fails Scryfall
  resolution and aborts the user's audit with a nonsense "not found". Spec is binding and
  requires unresolvable names to be real failures, not artifacts of our own parser. Fix is
  ~4 lines: strip the whole `#...` block to end-of-line, taking the first segment as the
  category. Cost if wrong: slightly more permissive tag parsing than Moxfield emits, which
  cannot corrupt a name.
Ruling: FOLD IN the BOM minor (1 line) despite minors normally staying out of the loop. A
  UTF-8 BOM silently drops exactly the first entry — often the commander — producing a
  99-card deck and a misleading deck_size error rather than a diagnosable one. Same function
  and same test file as the Important fix, so it is adjacent work, not scope creep. Cost if
  wrong: one redundant lstrip.
Ruling: DEFER the two speculative minors — `_SECTION_RE` not matching a "Commander 1"
  header, and "Token" singular not being excluded. Neither is a confirmed Moxfield shape and
  the reviewer rated both low-confidence. Cost if wrong: a header variant drops entries
  silently; the deck_size check catches the symptom, and a real export sample would settle it.
Task 2: fix round 1/5 (2 addressed pending re-review, 0 open; commits e652eda..0664a1b)
  Controller verified by direct execution: multi-word category now yields
  name='Cultivate' set='M21' cn='177' cat='Ramp, Draw'; multiple tags take first; BOM keeps
  both entries; all three regression guards (paren-in-name, MDFC //, single-word category)
  unchanged. Suite 26/26.
Task 2: re-review — Finding 1 ADDRESSED (deckparse.py:35,100-103), Finding 2 ADDRESSED
  (deckparse.py:56). New breakage: none functional.
Task 2: minor (deferred): test_multiple_tags_take_first_as_category does not pin the fix —
  it passes under the old `\S+` regex too, since both tags are single words. The real pinning
  test is test_multiword_category_preserves_name_and_set_code, which does fail pre-fix. A
  redundant test, not a missed fix. Flag to final review for triage.
Task 2: complete (commits 1830a6f..0664a1b, review clean after 1 fix round)

Ruling (PLAN AMENDMENT, pre-Task-5): the plan's Task 5 classification regexes were
  substantively wrong and I corrected the plan before dispatching the task. I pre-tested the
  planned regexes against 38 real Commander staples fetched from Scryfall: 11 of 36 with an
  expected tag were misclassified. The corrected set scores 0 misses with 0 regressions on
  the plan's own 21 test cases. Six defects fixed:
    1. _LAND_SEARCH only matched the literal word "land", so Nature's Lore / Three Visits /
       Farseek ("Forest card", "Plains ... card") were tagged TUTOR instead of RAMP. This one
       is the worst: it understates ramp AND inflates tutor density, and tutor density feeds
       the bracket verdict directly. Added basic land type names.
    2. _COUNTERSPELL required "counter target spell" literally, so Swan Song, Flusterstorm,
       and Dovin's Veto fell through to SPOT_REMOVAL. Broadened to allow a window between
       "target" and "spell", and SPOT_REMOVAL now never fires when COUNTERSPELL does.
    3. _SWEEPER missed damage-based and -X/-X wipes (Blasphemous Act, Toxic Deluge).
    4. _SPOT_REMOVAL missed bounce entirely (Cyclonic Rift got no tag at all). Added
       "return target ... to its owner's hand", where "owner's" is what distinguishes bounce
       from graveyard recursion to "your hand".
    5. _DRAW stopped at "four", missing Timetwister's "draws seven cards".
    6. _DRAW counted "Whenever an opponent draws a card" as our draw, making Smothering Tithe
       a draw spell. Masked opponent-draw clauses before matching; verified this changes only
       Smothering Tithe across the staple set.
  Added 12 parametrized real-staple regression tests carrying the oracle text verbatim, so
  these cannot regress silently. Cost if wrong: classification is heuristic by design and the
  report surfaces tags for correction, so a residual error is visible rather than hidden;
  the risk of the amendment itself is over-broad matching, which these 12 tests bound.

Ruling: FIX Important #1 — resolve()'s second guard (index miss after collection() succeeds)
  has zero test coverage. Only the success path is tested; delete the raise and every test
  still passes. This is the project's core guarantee, so an untested branch is unacceptable
  regardless of how correct it reads. Cost if wrong: none, adding a test cannot break code.
Ruling: FIX Important #2 — game_changers() pagination is never exercised (fixture has
  has_more:false), and the loop is unbounded. Load-bearing: the Game Changers list is 53
  cards today, under Scryfall's page size, so pagination never fires in practice — but if
  WotC expands it past one page, silent truncation under-reports Game Changers and produces
  a WRONG BRACKET VERDICT with no error. Adding a two-page test plus a page cap. Cost if
  wrong: a cap too low would truncate; set at 20 pages, ~70x current need.
Ruling: FIX Important #3 — the 100ms courtesy delay is enforced only within one method's
  batch loop, so resolve() fires collection() then game_changers() back-to-back with no gap.
  For any deck of 75 or fewer cards that is every run. Moving the throttle to a single
  _request() seam so the constraint holds per client, not per method. Cost if wrong: an
  extra 100ms per run, which is noise against network latency.
Ruling: FOLD IN 3 of 4 Minors as adjacent one-liners in the same module (consistent with the
  Task 2 BOM precedent): game_changers() indexing card["name"] directly would KeyError on
  malformed data inside a degrade path; _http_transport's json.loads sits outside the
  try/except so a non-JSON body raises JSONDecodeError instead of SourceUnavailable; and the
  weak sleep assertion is superseded by the throttle tests. DEFER the vestigial `missing`
  return value — it matches the brief's published interface signature and is harmless.
Task 3: fix round 1/5 (6 addressed pending re-review, 0 open; commits 9d45d4c..727d197)
  Controller ran MUTATION TESTS rather than trusting the pass count, since the findings were
  "tests that cannot fail". Broke each pinned behavior one at a time and confirmed the
  matching test fails: guard-2 raise removed -> killed; pagination loop reduced to a single
  fetch -> killed; throttle disabled -> killed; page cap removed -> killed. Repo restored,
  43/43 green.
Task 3: re-review — all 5 findings ADDRESSED, new breakage: none. Confirmed both hallucination
  guards unweakened by the throttle refactor; _made_request set before the transport call so a
  failed request still counts; page cap raises the same SourceUnavailable resolve() already
  degrades on, so runaway pagination costs an unflagged list, not the run.
Task 3: minor (deferred): vestigial always-empty `missing` return from collection();
  pagination cap message exceeds line-length convention (cosmetic).
Task 3: complete (commits 0664a1b..727d197, review clean after 1 fix round)

Ruling (PLAN AMENDMENT, pre-Task-6): the plan's pip-counting regex silently reported ZERO
  colored pips for monocolored hybrid costs like {2/W}. Pre-tested against 15 real mana-cost
  shapes: 14 correct, 1 wrong. _HYBRID_RE required a color on BOTH sides of the slash, so
  Spectral Procession ({2/W}{2/W}{2/W}) and Beseech the Queen ({2/B}x3) registered no colored
  requirement at all — the audit would claim the deck needs no white/black sources for a card
  that plainly does. Corrected to allow a generic left side, re-verified 18/18 including
  Reaper King's five-hybrid cost. Added 5 parametrized hybrid tests plus a colorless/snow
  negative test. Cost if wrong: hybrids now count toward both colors, overstating demand
  slightly, which is the safe direction — a deck gets told it wants more sources than strictly
  necessary rather than fewer.

Ruling: FIX Important #1 — the legendary check is a bare substring test, so any Legendary
  permanent passes as a commander. Confirmed live: "The One Ring" (Legendary Artifact) and
  "Jace, the Mind Sculptor" (Legendary Planeswalker, no commander text) would both be blessed
  as legal commanders, while "Daretti, Scrap Savant" (Legendary Planeswalker WITH "can be your
  commander") must be accepted. Correct rule: (Legendary AND Creature) OR Background OR oracle
  says "can be your commander". Cost if wrong: an over-strict rule rejects a legal commander,
  which is visible and arguable, versus the current silent blessing of an illegal one.
Ruling: FIX Important #2 — two tests pin nothing. test_errors_sort_before_warnings produces
  only ERRORs, so deleting the sorted() call still passes; and the commander-identity test is
  tautological because command_zone_identity is BY DEFINITION the union of the commanders'
  identities. Replacing with a genuine ERROR+WARNING fixture (which also covers the untested
  not-legal WARNING branch) and a colorless-commander test (which pins the {C} rendering).
Ruling: FIX the snow-basics Minor, and it touches models.py (a Task 1 file) — authorized.
  Card.is_basic_land uses startswith("Basic Land"), but Scryfall returns 'Basic Snow Land —
  Forest' for snow basics, confirmed live. A snow deck therefore gets a bogus singleton ERROR
  per duplicate basic — tens of them — which makes the tool useless for that archetype. This
  is a false positive in the legality verdict, the most damaging kind. Cost if wrong: the
  looser test would also accept a hypothetical future "Basic ... Land" type; no such card
  exists and the type line is WotC-controlled.
Ruling: FIX the any-number Minor. Relentless Rats, Dragon's Approach, Seven Dwarves, and
  Nazgul legitimately break singleton, and Scryfall exposes a reliable oracle marker ("A deck
  can have any number of cards named" / "up to <n> cards named"). Without this, a Rats or
  Shadowborn Apostle deck draws ~30 bogus ERRORs. Cost if wrong: an exempted card dodges a
  real singleton violation only if WotC writes that sentence on a card that is not actually
  exempt, which the rules forbid.
Task 4: minor (deferred): the numeric limits on Seven Dwarves (seven) and Nazgul (nine) are
  not enforced — those cards are exempted from singleton entirely rather than capped. Also
  deferred: noisy test fixtures whose generic card() helper trips unrelated codes.
Task 4: fix round 1/5 (5 addressed pending re-review, 0 open; commits 7b84126..a7147bd)
  Controller verified against the REAL type lines: Atraxa accepted, The One Ring rejected,
  Jace TMS rejected, Daretti accepted (oracle clause); Snow-Covered Forest x30, Relentless
  Rats x20, Seven Dwarves x7 all exempt from singleton while Sol Ring x2 is still flagged.
  8/8 behavioral checks pass, suite 62/62.

Ruling (PLAN AMENDMENT, pre-Task-5): two more bracket-corrupting defects found by pre-testing
  against real cards. (a) _MASS_LAND_DENIAL required "lands" immediately after "all", so
  Jokulhaups ("all artifacts, creatures, and lands") and Devastation ("all creatures and
  lands") were NOT detected as mass land denial. Those are the format's defining MLD cards,
  and MLD is the single rule brackets 1-3 care most about — a bracket-2 deck running Jokulhaups
  would have been reported compliant. Widened to match within one sentence; verified "nonland"
  does not false-positive. (b) _EXTRA_TURNS required the literal "an", so Time Stretch ("takes
  two extra turns") was missed. Also split lands-only denial from list-form wipes: Armageddon
  suppresses the SWEEPER tag, Jokulhaups correctly keeps it since it really is both. Verified
  13/13 across Armageddon, Ravages, Catastrophe, Jokulhaups, Devastation, Wrath, Austere
  Command, Cyclonic Rift, a nonland wipe, and three extra-turn spells. Added 10 parametrized
  tests. Cost if wrong: the wider MLD window could match a sentence mentioning lands
  incidentally after "destroy all"; the [^.] bound and the 7 regression cases hold that in check.
Task 4: re-review — all 5 findings ADDRESSED, new breakage: none. Confirmed a Legendary
  Artifact Creature is still accepted, a transforming card whose front lacks "Creature" is
  rules-correctly rejected, and no real type line starts with "Basic" + contains "Land" except
  actual basics, so the loosened is_basic_land has no false positives.
Task 4: minor (deferred): test_errors_sort_before_warnings draws its ERROR from an incidental
  deck_size mismatch (the extra card makes 101) rather than a deliberately chosen second
  violation. Works, slightly accidental construction.
Task 4: complete (commits 727d197..a7147bd, review clean after 1 fix round)

Ruling: FIX Critical — `target (creature|permanent|player) (gets|sacrifices)` has NO SIGN CHECK,
  so Giant Growth ("gets +3/+3") and Mutagenic Growth tag as SPOT_REMOVAL. Pump spells counted
  as removal makes the removal number meaningless; Giant Growth is about as common as green
  cards get. Require `gets\s+-`, mirroring _SWEEPER's existing "all creatures get -".
Ruling: FIX Critical — `(destroy|exile) target` matches blink of your OWN creature: Ephemerate
  and Restoration Angel tag as SPOT_REMOVAL. Doubly wrong (inflates removal, misses protection).
  Added a "you control" negative lookahead plus a _SELF_BLINK clause tagging PROTECTION.
  Verified the lookahead does not break Cyclonic Rift ("you don't control" has no "you control").
Ruling: FIX Critical — _LANDS_ONLY_DENIAL required "all/each" adjacent to "lands", so Ruination
  ("Destroy all nonbasic lands") and Bust ("Each player sacrifices all lands they control")
  kept the SWEEPER tag despite destroying no creatures. Rather than patch the adjacency I
  DELETED the suppression rule entirely and made _SWEEPER require a NON-LAND permanent type.
  Strictly better: Armageddon and Ruination are no longer sweepers for the right reason, while
  Jokulhaups and Devastation keep SWEEPER because they genuinely name creatures/artifacts.
Ruling: FIX Important — "This spell can't be countered" made Dovin's Veto and Pact of Negation
  count toward PROTECTION. A counterspell's self-referential clause says nothing about a deck's
  resilience package. Masked that exact phrase before _PROTECTION, which preserves genuine
  uncounterable-protection cards like Cavern of Souls.
Ruling: FIX Important — "you lose the game" is a DRAWBACK, not a win condition. Demonic Pact and
  Pact of Negation tagged WINCON. Added a `(?<!you )` lookbehind so "target player loses the
  game" still counts while "you lose the game" does not.
Ruling: FIX my finding (b) — Demonic Consultation tagged SWEEPER off "exile all other cards".
  Resolved by the same _SWEEPER permanent-type requirement.
Ruling: FIX my finding (c) — Scapeshift ("that many land cards") tagged TUTOR not RAMP. Widened
  _LAND_SEARCH; verified 0/20 wrong with all 10 real tutors still TUTOR.
Ruling: ALSO ADD damage-based spot removal (`deals N damage to any target|target <x>`). Found
  while verifying: Lightning Bolt and Flame Slash were detected as nothing at all. Burn is the
  most common removal in red, so omitting it understates the removal package. Verified it does
  not catch Blasphemous Act or Pyroclasm, which say "to each creature".
  Full regression: 38 cases, 0 mismatches, covering every previously-correct classification.
Task 5: minor (deferred): Demonic Consultation is functionally a tutor but says "name a card"
  rather than "search your library", so it now tags SYNERGY. Also deferred: mass bounce as
  sweeper (Aetherize), redirect as protection (Deflecting Swat), graveyard land recursion as
  ramp (Splendid Reclamation), and no test covers classify_deck with a commander absent from
  deck.cards.
Task 5: fix round 1/5 (8 addressed pending re-review; commits 35662c1..18d3d36)
  Controller verified against 16 REAL Scryfall cards: 0/16 failures. Giant Growth -> SYNERGY,
  Ephemerate + Restoration Angel -> PROTECTION, Ruination -> MLD only, Dovin's Veto + Pact of
  Negation -> COUNTERSPELL only, Demonic Consultation -> SYNERGY, Scapeshift -> RAMP, Lightning
  Bolt -> SPOT_REMOVAL, Pyroclasm -> SWEEPER, Jokulhaups -> MLD+SWEEPER. Suite 115/115.

Ruling (SCOPE CHANGE, user direction): mtgpt is an agent toolkit, not a pipeline. Spec amended
  (35662c1), Task 8 rewritten as an operation surface, and Tasks 11-13 added for external
  resources: EDHREC synergy/themes, Commander Spellbook combo detection, and a composed
  `suggest` operation. Execution order is now 1-8, 11-13, 10, with Task 9 (SKILL.md) LAST since
  it documents operations that must exist first. Task 12 also closes the two-card-combo gap every
  bracket report has had to declare. Cost if wrong: more surface area to maintain and more
  unofficial endpoints to depend on; mitigated by every external call degrading to
  SourceUnavailable with the toolkit still useful on Scryfall alone.
Task 6: implemented (commit 2a0c7d4, 21/21 task tests, suite green, tree clean, trailer correct)
  Controller acid-tested the math on a hand-constructed 99-card deck: land_count 36,
  mana_sources 47 (36 lands + 11 ramp), avg MV 3.4 correctly flagged high against the 2.8-3.2
  band, curve capped at bucket 7 with no bucket 9, DRAW 4 vs 8-12 flagged low with delta 4,
  PROTECTION 0 flagged low. Pip math counted 46 green sources, correctly EXCLUDING Sol Ring
  (produces {C}, not {G}) while still counting it in mana_sources — the two figures are
  distinct and both right.
Task 5: re-review — all 8 findings ADDRESSED, _LANDS_ONLY_DENIAL confirmed fully deleted (not
  merely unused). But the re-review found ONE new Important false negative introduced BY MY FIX.
Ruling: FIX round 2 — my fix left the two _SWEEPER branches asymmetric. destroy/exile requires an
  AFFIRMATIVE non-land permanent type; the sacrifice branch instead used a NEGATIVE "no land
  mentioned in the sentence" lookahead. So "Catch // Release" ("Each player sacrifices an
  artifact, a creature, an enchantment, a land, and a planeswalker") lost its SWEEPER tag because
  the word "land" appears, despite sacrificing four non-land types. The reviewer's diagnosis of
  the asymmetry as root cause is correct and better than my patch. Unifying both branches on the
  affirmative test. Verified: Catch // Release regains SWEEPER; Bust still correctly excluded
  (only lands); every other sweeper and MLD case unchanged.
  Controller correction: I initially expected Catch // Release to be MLD too. It is not —
  sacrificing ONE land is an edict, not mass land denial — so MLD=False is correct behavior and
  my expectation was the error.
Task 5: minor (deferred): Smallpox ("Each player loses 1 life, discards a card, sacrifices a
  creature, then sacrifices a land") never matches because the literal phrase "each player
  sacrifices" is broken up. Pre-existing — old and new patterns both return False — so NOT a
  regression from this fix. Also deferred: "you can't lose the game" (Platinum Angel) vs the
  "you lose the game" lookbehind; predates this diff.
Task 5: fix round 2/5 (1 addressed pending re-review; commits 2a0c7d4..053f6c0)
  Controller verified 0/8: Release regains SWEEPER, Bust stays MLD-only, Edict SWEEPER,
  Armageddon/Ruination MLD-only, Jokulhaups MLD+SWEEPER, Wrath SWEEPER, Demonic Consultation
  SYNERGY. Both requested test cases landed (the implementer reported 1, actually added 2).
Task 6: fix round 1/5 (4 addressed pending re-review; commits 053f6c0..d4d1497)
  Controller verified: delta +9 when low / -8 when high; 4-pip and 5-pip costs both clamp to
  required=26 with no KeyError; the both-ramp-and-MDFC card yields mana_sources=37 not 38, with
  land_count=36 and mdfc_land_count=1 — the overlap subtraction works.
Task 7: implemented (commit f05d3ba, 17/17 task tests, suite green, tree clean, trailer correct)
  Controller acid-tested against the LIVE 53-card Game Changers list from Scryfall, not fixtures.
  Bracket 2: 8 GC vs 0 allowed -> ERROR; Armageddon -> ERROR; 3 extra-turn spells -> WARNING;
  5 tutors -> WARNING; compliant=False. Bracket 3: same errors but the tutor warning correctly
  DISAPPEARS since bracket 3 permits unrestricted tutoring. Bracket 4: compliant=True with the
  2 deferred notes still present. Five of the test tutors are themselves on the live GC list and
  were detected as such, confirming the flag comes from Scryfall rather than a hardcoded guess.
Task 7: minor (deferred): deferred_checks text says "(Layer 2)", which is stale now that the plan
  is renumbered into tasks. Task 12 rewrites this string when combo detection lands.
Task 6: re-review — all 4 findings ADDRESSED, new breakage: none. Reviewer hand-verified the
  delta tests are non-vacuous (a `count - target_min` implementation fails both), that the pip
  test derives 26 from the table's max key rather than re-deriving it through the production
  expression, and that the overlap subtraction cannot UNDER-count since each term counts a
  qualifying card in full exactly once.
Task 6: complete (commits d144274..d4d1497, review clean after 1 fix round)

Ruling (PROCESS FAILURE, mine): I rewrote Task 8 in the plan but did NOT commit it, while
  concurrent subagents were running git operations in this shared worktree. The ~660-line edit
  was lost. Every other plan amendment I committed in the same bash call; this one I wrote and
  then was pulled away by user messages and an incoming review. The Task 8 implementer caught it
  correctly — it noticed Tasks 11-13 declare `Modify: mtgpt/api.py` for a file the stale Task 8
  never creates, concluded the brief was stale rather than that it had misread, reported BLOCKED,
  wrote no code, and left the tree clean. Restored and committed atomically as 8bc350d; brief
  regenerated at 939 lines and verified to contain api.py, render_report, the ok-envelope,
  ScryfallClient.search, the fixture spec, and CARD TAGS. RULE GOING FORWARD: every plan/spec
  edit commits in the same command that writes it. Cost if wrong: none — the restored content is
  verified present and the contradiction that exposed it is resolved.

Ruling: Task 8's brief contained a latent correctness bug I caught from a failing test rather
  than by review: `lookup_card` took `cards[0]`, trusting response POSITION over card IDENTITY.
  Invisible in production (Scryfall returns one card per identifier) but wrong for a modal DFC
  requested by front-face name, which comes back under its full "A // B" name. Instructed the
  implementer to match by name as `resolve()` already does, and to raise UnresolvedCards on a
  non-match — that is exactly the "200 response, wrong card" case the second guard exists for.
  Told it explicitly NOT to weaken the test. Cost if wrong: a stricter lookup could raise where
  a loose one returned something plausible; raising is the correct direction for this tool.
Task 8: implemented (commit f9f9319, 13/13 api + 11/11 cli, suite 191/191, trailer correct)
  Implementer DISCLOSED that it first masked the lookup_card bug by reordering the fixture, then
  applied the real name-matching fix and reverted the fixture. Honest disclosure of exactly the
  workaround my correction was sent to prevent.
  Controller verified the toolkit against LIVE APIs:
   - card "Sol Ring" -> functions ['ramp'], is_game_changer false
   - card "<invented name>" -> ok:false, type UnresolvedCards, names carried, exit 2
   - search "o:'search your library for a' t:sorcery c:g cmc<=3" --limit 5 -> Farseek/Nature's
     Lore/Rampant Growth/Three Visits all ['ramp'], Green Sun's Zenith ['tutor'], with prices
   - classify across 5 cards -> every land fetch ramp, Demonic Tutor tutor
   - validate / bracket callable in isolation, each returning only its own section
   - report --text renders LEGALITY / COMPOSITION / CURVE / COLORED SOURCES / BRACKET / CARD TAGS
   - the report correctly flags {W} sources SHORT: 0 white sources for a deck running Wrath of
     God ({2}{W}{W}), which is the mana-base failure that actually loses games
Task 7: re-review — all 3 findings ADDRESSED, new breakage: none. Guard confirmed unable to
  false-positive on the real caller: classify_deck populates exactly the same Card.name keys that
  `scanned` is built from, with no casefold in either path. tutor_count still qty-aware for the
  99; commanders hardcoded to 1, which is correct.
Task 7: minor (deferred): `if tags is not None:` at brackets.py:91 is dead code after the
  default-fill on line 83. Harmless.
Task 7: complete (commits d4d1497..9bccec0, review clean after 1 fix round)

Ruling: the Task 7 re-review surfaced an out-of-scope CRITICAL-class gap in validate.py (Task 4),
  which I confirmed myself: a card listed BOTH as commander and in the 99 passes validation with
  ZERO violations at exactly 100 cards. _check_singleton only inspects deck.cards for qty>1 and
  never cross-checks commander names, so two copies of one card read as legal. This is a legality
  FALSE NEGATIVE — the same class as the earlier "The One Ring as commander" and snow-basics bugs
  — and legality is the one thing this tool must be authoritative about. The deck_size check only
  catches it when the count happens to be off, which it is not in the realistic case of a
  hand-edited list where the commander also appears in the deck section. Dispatching a fix to the
  validate.py owner. Cost if wrong: a new ERROR code that a later task must know about; the risk
  is a false positive on a legal deck, which cannot happen since the commander genuinely may not
  appear in the 99.
Task 8: review spec OK, quality APPROVED, 0 Critical/Important, 4 Minor.
  Reviewer confirmed: lookup_card's fix is a genuine name match mirroring
  scryfall._index_by_name (not a special case); the fixture is in natural order with no trace of
  the masking reorder; search() routes through the _request throttle seam; errors are data on
  every path with consistent exit codes; main() does NOT swallow unexpected exceptions into a
  clean ok:false; _read_deck_text's single caller checks None; CARD TAGS lists commanders too;
  all nine operations are independent with no shared state.
Task 8: complete (commits 9bccec0..f9f9319, review clean, 4 minors deferred below)

Ruling: two of Task 8's "Minor" findings are the SAME issue I ruled IMPORTANT for Task 3's
  game_changers() — an unbounded pagination loop and no multi-page test. search() has
  `while url and len(found) < limit` with no page cap, so a response that reports has_more
  forever with empty data hangs a user-facing operation; and search_results.json has
  has_more:false, so a regression in the next_page advance logic would not be caught. Treating
  the same defect as Important in one module and Minor in another would be arbitrary. DECISION:
  fix both, plus hoist _error_payload from cli.py into api.py so the stated architectural rule
  ("api.py owns serialization, a future MCP adapter is an adapter not a rewrite") is actually
  true rather than nearly true.
  TIMING: api.py and cli.py are being edited concurrently by Task 11, and Tasks 12-13 will edit
  them again. Dispatching this fix now would conflict. QUEUED to run after Task 13 lands and
  before Task 9 (SKILL.md). Recorded here so it cannot be lost.
Task 8: minor (deferred, cosmetic): the two stacked except clauses in cli.py main() are
  functionally redundant (the second covers the first). Matches the brief verbatim; harmless.

Ruling (MY ERROR, corrected): I added Tasks 11-13 to the plan but never generated their briefs.
  The Task 11 implementer found the content inline in the plan doc, confirmed it matched its
  dispatch prompt verbatim, used it, and said so — the right call rather than blocking. Briefs for
  11, 12, 13 now generated (533/445/359 lines). Same root cause as the lost Task 8 edit: I treat
  plan edits as done when written rather than when committed AND propagated.

Ruling: FIX Critical 1 — edhrec.themes() and bracket_distribution() call int() on payload values
  with no guard, so reshaped data raises ValueError. ValueError is NOT an MtgptError, so cli.main()
  does not catch it and the user gets a raw traceback instead of the {"ok": false} envelope — in a
  module whose entire design rule is "degrade, never crash". The bracket KEY conversion is already
  try/except-guarded while the adjacent VALUE conversion is not; same bug class, inconsistently
  applied. Cost if wrong: none, guarding a conversion cannot break a well-formed payload.
Ruling: FIX Critical 2 — but NOT by changing collection()'s strictness. Reproduced: collection()
  raises UnresolvedCards for the whole batch on any not_found, so ONE unresolvable EDHREC candidate
  zeroes out ~39 good ones, and api.py's per-candidate `if card is None: continue` is dead code for
  that path. Crucially, that all-or-nothing behavior is CORRECT for a user's decklist — a typo must
  be a hard stop. It is wrong only for EDHREC candidates, where a bad suggestion should be dropped.
  So the fix adds `strict: bool = True` to collection(): decklist resolution keeps strict=True
  (hard stop, guarantee intact), commander_synergy passes strict=False and filters. This also
  retires the vestigial always-empty `missing` return value the Task 3 review flagged as a Minor —
  it becomes meaningful. Cost if wrong: a caller could pass strict=False where a hard stop was
  wanted; mitigated by strict defaulting to True so every existing call site is unchanged.
Ruling: FIX Minor 1 (clamp inclusion_rate to [0,1] — unofficial data could yield "played in 140% of
  decks") and Minor 2 (iterate CANDIDATE_LISTS in its declared priority order rather than using it
  as a membership filter, so a card appearing in two lists gets the intended attribution).
Task 11: minor (deferred): commander_slug drops rather than transliterates letters with no NFKD
  decomposition (ø, æ, ß). Fail-safe — a wrong slug 404s into SourceUnavailable rather than leaking
  bad data — and I could not identify a real commander name containing one. Noted, not fixed.
  SPLIT DISPATCH: Task 13 is concurrently editing api.py, so this fix round covers edhrec.py and
  scryfall.py only; the one-line api.py change (strict=False + filter) follows once Task 13 lands.
Task 13: implemented (commit fafdee9, 6/6 task tests, suite 235/235, trailer correct)
  Implementer found a bug in MY brief: `degraded.append(str(exc))` cannot satisfy the brief's own
  test `assert "EDHREC" in result["degraded"]`, since list membership is exact-element equality not
  substring. It changed the CODE to `degraded.append(exc.source)` rather than weakening the test —
  exactly the instruction. Second time an implementer has caught a brief defect this way.
  Controller verified the payoff end-to-end on the live stack:
   gaps: ramp 2/10-12 (+8), draw 0/8-12 (+8), spot_removal 1/5-8 (+4), protection 0/3-5 (+3),
         sweeper 1/2-3 (+1)
   suggestions: Vraska Betrayal's Sting fills BOTH draw+ramp and therefore leads the ranking
         (len(fills) primary sort working), then Tekuthal (protection, 67%), Ezuri (draw, 53%),
         Tezzeret's Gambit (48%), Prologue to Phyresis (40%), Infectious Inquiry (37%)
   ALL SIX GUARANTEES HELD: every suggestion inside Atraxa's {B,G,U,W} with NO red card; all
   legal=legal; all is_game_changer=false; none already in the deck; every one has non-empty
   `fills` tied to a gap the audit measured; every `reason` cites a real EDHREC inclusion rate.
Task 11: fix round 1/5 (edhrec+scryfall half; commits fafdee9..9dc77cd, suite 242/242)
  Controller verified: reshaped theme count skips the bad entry and keeps the good one; bad bracket
  VALUE and bad bracket KEY both skip; inclusion_rate clamps to 1.0 on glitch data (140/100);
  collection() strict-by-default still raises UnresolvedCards (decklist guarantee intact) while
  strict=False returns found + missing so candidates survive. api.py half deferred by my scope note.

Ruling: Task 10 (Moxfield browser fetch) RESOLVED AS DEFERRED, not built. I ran the verify-first
  step rather than assuming. pip install --user playwright succeeded with no sudo and Chromium
  downloaded (114 MB), but the binary cannot launch:
    error while loading shared libraries: libnspr4.so: cannot open shared object file
    libnspr4.so, libnss3.so, libnssutil3.so, libasound.so.2 => not found
    playwright install-deps --dry-run: Missing system dependencies (37)
  Those install only via apt-get as root and sudo requires a password here. Took the task's own
  Step 2 deferral branch: no fetcher written, because an intermittently-failing browser fetch is
  worse for the user than an absent one that is documented. The export/paste path from Task 2 works
  and every deck operation accepts --file and --stdin. Task 9's README will carry the note with the
  exact missing library names so the condition is reproducible and reversible. Playwright and
  Chromium are left installed under ~/.local and ~/.cache/ms-playwright; harmless, and if the libs
  are ever installed the remaining steps become viable unchanged. Plan amended and committed
  atomically (de3c3c6) per my standing rule. Cost if wrong: the user wanted 1+3 (browser does the
  export for them) and does not get it; the fallback is two clicks in Moxfield, and the note tells
  them exactly which three packages would unblock it.
Task 10: complete (deferred with documented evidence; no code)

Ruling (MY ERROR, corrected): I used `git add -A` for the Task 10 plan amendment while the bundled
  cleanup agent had uncommitted work in the same shared worktree, so my commit swept in its three
  code fixes under a message that only described the plan change. Caught it by noticing the cleanup
  commit was absent from the log while its changes were present and the tree was clean. Split into
  two honest commits (5f53af1 code, 47c3699 plan) via reset --soft; nothing was pushed and no
  implementer was live, only a read-only reviewer. RULE ADDED: in this shared worktree, never
  `git add -A` — always stage explicit paths. This is the second bookkeeping failure from the same
  root cause (treating the worktree as mine alone); the first lost the Task 8 plan rewrite.
Cleanup: complete (commit 5f53af1, suite green, tree clean)
  Controller verified all three items: commander_synergy passes strict=False; MAX_SEARCH_PAGES=20
  with the cap enforced at scryfall.py:183; cli.py delegates to api.error_payload at both sites;
  and the error envelope for an invented card name is byte-identical in shape with exit 2.
Cleanup: the agent caught a THIRD defect in one of my briefs — my suggested Item 1 test fixture
  (collection_basic.json) shares no card names with the EDHREC fixture, so count==0 with or without
  the fix and the test would have passed VACUOUSLY. It substituted search_results.json, whose
  "Cultivate" is a real Top Cards entry. Controller mutation-verified: reverting strict=False ->
  strict=True makes the test FAIL with UnresolvedCards, so the substituted fixture genuinely pins the
  behavior. Suite 246/246, tree clean after restore.
  The agent also independently observed my git add -A race and noted it self-resolved without taking
  any destructive action — correct handling.

Ruling: FIX Critical 1 — commander_synergy calls `card_from_json(p)` with no `game_changers=`, so
  every candidate reports is_game_changer=False unconditionally and suggest_additions' bracket
  filter is DEAD CODE against real data. resolve() does it correctly; this call site does not.
  Reproduced: Rhystic Study (on the live 53-card list) -> card_from_json(p).is_game_changer=False,
  card_from_json(p, game_changers=gc).is_game_changer=True.
  MY OWN VERIFICATION ERROR: my live check reported "all suggestions is_game_changer=false" and I
  read that as the filter working. It was the field being hardcoded false by omission. I observed
  the expected OUTPUT without checking that the MECHANISM produced it. That is the same
  "passes vacuously" failure I had been assigning reviewers to hunt for, committed by me.
Ruling: FIX Critical 2 — gc_in_deck is computed once before the candidate loop and never
  incremented as Game Changer suggestions are appended, so at bracket 3 (max 3) with 2 already in
  deck, several GC candidates each pass independently against the same static count and together
  push the deck over. Latent only because Critical 1 masked it; fixing 1 makes it live.
Ruling: FIX Important — the colour-identity test proves nothing. The reviewer DELETED the identity
  filter and the test still passed, because EDHREC only recommends on-colour cards so the fixture
  pool contains no off-colour candidate. The filter does work (reviewer injected Lightning Bolt and
  it was excluded) but nothing guards against regression. Needs a synthetic off-colour candidate.
Task 13: minor (deferred): the no-commander early return is untested (reviewer exercised it
  manually and confirmed a well-formed result); `if rate` treats a genuine 0.0 inclusion rate as
  falsy, practically unreachable since EDHREC would not list a card with zero recorded decks.
Note: the brief's Interfaces block lists `validate` as consumed by suggest_additions; it is not,
  and the brief's own reference code never calls it. Brief inconsistency, no code change — gap
  analysis and legality are deliberately separate operations.

Ruling: FIX bracket-3 combo fidelity — a FALSE POSITIVE in the bracket verdict, found because a
  DOC author refused to write a table it could not substantiate against RULES. I had specified
  allow_two_card_combos=False for brackets 1, 2 AND 3, but WotC bans only EARLY-GAME two-card
  combos at bracket 3; a late-game finish is legal. So mtgpt told legitimately legal bracket-3 decks
  compliant=False with an ERROR. mtgpt cannot assess combo speed (depends on tutors, ramp, pilot), so
  guessing either way is wrong: "banned" fails legal decks, "allowed" passes illegal ones. Replaced
  the boolean with a three-state field (banned / late_only / allowed) so the rule text is represented
  rather than approximated, and bracket 3 now WARNS with a message stating the real rule and handing
  the judgment to the pilot. Cost if wrong: a user at bracket 3 with an early-game combo gets a
  warning rather than an error and could self-certify wrongly — but the message names the distinction
  explicitly, which is better than a verdict that is simply wrong.
Task 12: fix round 1/5 (commit 62a099c, suite 250/250)
  Controller verified all five brackets: 1-2 ERROR/not-compliant, 3 WARNING/compliant with
  "late-game" in the message, 4-5 silent. Disclaimer logic unaffected: still absent when combos are
  supplied, present when omitted. Scope clean — only brackets.py, test_brackets.py, brackets.md.
Task 9: complete (commit 7b22fca)
Task 13: fix round 1/5 (2 Criticals + 1 Important addressed; commit 088abc8, suite 250/250)
  Controller verified by MECHANISM this time, not by output: commander_synergy now passes
  game_changers= into card_from_json and has a SourceUnavailable degrade path; driving two real cards
  through the full path gives Rhystic Study (on the live 53-card list) is_game_changer=TRUE and Sol
  Ring (not on it) FALSE. That is discriminating — my earlier check could not tell "filter working"
  from "field always false", which is exactly how I was fooled.
  Implementer verified each of its 4 new tests fails against the specific revert it targets.
  Scope clean: only api.py, test_api.py, test_suggest.py. It also recovered correctly from another
  agent's `git stash -u` by applying its own recorded SHA (not pop) and leaving the other entry alone.
Task 12: re-review — ADDRESSED, new breakage: none. Reviewer cross-checked all 5 RULES rows against
  brackets.md on every column (no drift), confirmed the `combos is not None` gate untouched and no
  falsy test introduced, confirmed `compliant` keeps the pre-existing "no ERROR" rule with no special
  case, confirmed the new field occupies the old positional slot so nothing breaks, and confirmed
  allow_two_card_combos has zero live references (surviving only in the frozen plan doc as history).
Task 12: complete (commits c9a174e..62a099c, review clean after 1 fix round)

Ruling: FIX the degrade-path silence (Task 13 round 2). A Game Changers outage is caught inside
  commander_synergy and never escapes, so suggest_additions returns degraded:[] — claiming full
  health while bracket enforcement is silently off and a bracket-2 user could be handed a Game
  Changer with nothing in the output saying the check did not run. The function's own docstring
  promises the outage is named. This is the silent-wrong-answer class the project exists to prevent,
  so reporting honesty wins over consistency with resolve()'s existing (also silent) precedent.
  Cost if wrong: a degraded entry appears where some callers expected none; the healthy-path test
  guards against crying wolf.
Ruling: DEFER the pre-sort budget ordering. pool comes from synergy_cards sorted by raw synergy, not
  by suggest's own (len(fills), synergy) key, so a Game Changer filling 1 gap with higher synergy can
  take the last budget slot ahead of one filling 2 gaps. The allowance is NEVER exceeded, so the
  guarantee holds — this is optimality, not correctness, and the fix is a restructure rather than a
  line. Principled split: honesty bugs get fixed, optimality gaps get recorded. Cost if wrong: a
  slightly worse Game Changer suggestion at the margin, never an illegal one.
Task 13: minor (deferred): the two independent game_changers() fetches (deck resolve vs candidate
  resolve) could theoretically disagree if Scryfall's list changed between the two throttled calls
  inside one invocation. Vanishingly unlikely; noted.
Note: the stale "(Layer 2)" wording in deferred_checks was already resolved by Task 12's split —
  COMBO_DEFERRED now reads "...requires Commander Spellbook (not supplied)." Minor #9 is closed.
Task 13: fix round 2/5 (commit deea278, suite 252/252)
  Controller verified the contrast that proves it: with the Game Changers list DOWN, degraded names
  the outage AND Rhystic Study is suggested at bracket 2 (the filter genuinely cannot run) — but the
  user is told. On the healthy path degraded=[] and Rhystic Study is correctly excluded. The tool now
  says when it could not enforce a rule instead of silently not enforcing it.
Task 13: complete (commits f62764a..deea278, review clean after 2 fix rounds)

Ruling (PARK, surfaced to user): resolve-side half of C2 is untested — deleting
  `game_changers_available = False` from scryfall.resolve leaves all 306 green, because the bracket
  tests construct the flag by hand. Production behavior verified correct end-to-end, but this is the
  one place the fix can rot back into the Critical it closed. ~10 lines. Parked per the no-second-wave
  rule; surfaced because it is load-bearing. Cost if wrong: a future edit to resolve() silently
  restores "outage flips compliant to true with no deferred note".
Ruling (PARK): edhrec non-finite counts (NaN/Infinity) now raise — int() was moved outside the
  existing guard, and json.loads accepts those literals. ~3 lines. New in the wave but narrow.
Ruling (PARK): --stdin is the unfixed half of I4; a UnicodeDecodeError from stdin escapes the
  envelope. Pre-existing, but the wave's new error text routes the user to --stdin, so the advice
  points at the hole. Either guard it or drop that clause.
Ruling (PARK): classify_cards does not re-verify the returned name, so `classify "Sol Ring // Not A
  Card"` answers about Sol Ring instead of raising. resolve() and lookup_card() DO re-verify.
Ruling (PARK): self-only `protection from` still tags PROTECTION (Paladin en-Vec, Progenitus) — the
  one alternative left ungated by a grant verb. Same class classify(a) was raised to close, half
  closed. Fix is to put it behind the same (gain|gains|have|has) gate.
Ruling (PARK): Chaos Warp classifies as synergy, not spot_removal. Errs safe (understates removal),
  visible in CARD TAGS.
