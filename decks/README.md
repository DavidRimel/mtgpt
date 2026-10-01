# Jodah, Archmage Eternal — Big Splashy Spells — Bracket 4

Built 2026-10-01 with mtgpt. 100 cards, legal, bracket-4 compliant,
17 Game Changers, all five colours clearing their source thresholds.

Verified with:
  python3 -m mtgpt.cli validate --file decks/jodah-archmage-eternal-bracket4.txt
  python3 -m mtgpt.cli audit    --file decks/jodah-archmage-eternal-bracket4.txt
  python3 -m mtgpt.cli bracket  --file decks/jodah-archmage-eternal-bracket4.txt --target 4
  python3 -m mtgpt.cli combos   --file decks/jodah-archmage-eternal-bracket4.txt

What the toolkit caught during the build:
  - Jeweled Lotus and Mana Crypt are BANNED (Sept 2024); both were removed.
  - {G} and {W} read SHORT because ten fetchlands produce no immediate colour.
    Three fetches became Spirebluff Canal, The World Tree, Cavern of Souls.
  - Zero basics made Prismatic Vista a dead card; it became Timeless Lotus.

Combos it assembles: Approach of the Second Sun + Demonic/Vampiric/Mystical Tutor.

Deliberately outside the default audit bands, with reasons:
  - ramp 19 vs 10-12: a five-colour deck assembling WUBRG by turn three wants it.
  - sweepers 6 vs 2-3: Ruinous Ultimatum and In Garruk's Wake ARE the splashy payoffs.
  - avg MV 4.65 vs 2.8-3.2: correct for the archetype; Jodah pays WUBRG for anything.
