# Decisions

## D1 · Structured wiki data first (Bucket), wikitext templates second, prose last
The OSRS Wiki runs the **Bucket** extension (MediaWiki 1.45; no SMW/Cargo). Buckets give typed rows for quests,
items, equipment bonuses and build variants. What buckets do not cover is read from **wiki templates**
(`{{Quest rewards}}`, `{{SCP|Skill|xp}}`) - still structured. Prose is parsed only where the wiki has nothing
else (equipment requirements, D3), and then the sentence is stored as evidence.

## D2 · Raw cache = raw layer, DuckDB = rebuilt store
Every HTTP response is saved as JSON with its URL and fetch time. `gp ingest` rebuilds the wiki/price tables from
that cache (idempotent); player and price snapshots are appended. Offline mode serves the cache only.
No dbt here: the rules are Python functions with unit tests, DuckDB is the store and query engine.

## D3 · Equipment requirements come from prose, with evidence
`infobox_bonuses` has stats but **no wield/wear requirements**. They are only in the lead sentence of the item page
("... requiring level 40 Attack to wield"). We extract `(level, skill)` pairs only from sentences that contain
"requir" + "wield/wear/equip", store `provenance = wiki_prose` and the sentence itself. Items with no such sentence
get `equip_req_status = none_found`: either no requirement (bronze/iron) or phrased differently - reported by
`gp ingest` so it can be reviewed, never silently assumed.

## D4 · XP the player chooses (lamps) vs. random XP
- **Fixed XP** (`{{SCP|Defence|...}}`): counted exactly.
- **Choosable XP** (lamps, books, "any skill"): never counted against a locked skill; shown as
  "safe if you pick X".
- **Choice between packages** (`choice_bundle`, e.g. Monkey Madness I: "35,000 in Attack and Defence and 20,000 in
  Strength and Hitpoints OR the reverse"): every package can contain the same skill, so it is **not** "safe if you
  pick X". Treated as the worst case. (Found while checking the raw text before claiming MM1 was safe for a pure.)
- **Random XP** (e.g. Observatory Quest can give Attack/Strength/Defence/Hitpoints): treated as the **worst case** -
  the XP guard flags it as a risk for every locked skill it may hit.
- Lines the parser cannot classify are stored as `unparsed` with the raw text and listed by `gp ingest`.

## D5 · Unranked hiscore skills are unknown, not level 1
The hiscores return -1 for skills below the ranking threshold. The level is then unknown; the planner asks for an
override instead of assuming 1.

## D6 · Quest key = quest name, not page
The `quest` bucket can have several rows per page ("Recipe for Disaster/Full guide" repeats every subquest).
Quests are keyed by the `name` inside the row's `json`; one row per name, preferring the quest's own page.
Rewards are only read from the quest's own page (a guide page's rewards template would belong to the whole saga).

## D7 · Builds are YAML; numbers the wiki has are joined, not copied
`builds/*.yaml` declares only what defines a build: its limits (`max`, `hard`/`soft`) with the wiki's own wording
as the reason, plus key unlock items. Variant target levels and combat ranges come from Bucket `infobox_pure`
(joined by `wiki_build`), and unlock requirements from `item_requirements` - so a wiki edit flows in on the next
ingest instead of going stale in YAML. A loader validates every file (known skills, 1..99, Hitpoints >= 10).

## D8 · XP guard semantics
Per quest, against the account's current XP (fresh account when no RSN): `blocked` (fixed XP beyond the headroom,
a skill requirement above a limit, or a blocked prerequisite), `risk` (random XP / package choice, D4),
`safe_if_choose`, `fits` (fixed XP that still fits, e.g. a zerker under 45 Defence), `safe`, `unknown`.
Headroom = experience that keeps the skill at the limit (`xp(limit + 1) - 1`), from the wiki's XP formula.
Prerequisites propagate: a quest is never safer than the chain it requires (In Aid of the Myreque blocks
Darkness of Hallowvale, which blocks A Taste of Hope...). Cumulative XP across several quests that each "fit"
is phase 3's job (the planner chooses a set, not one quest).

## D9 · The formula is the authority, not the infobox
Combat levels are computed with exact fractions from the wiki's "Combat level" formula. When a build variant's
stated combat level disagrees (e.g. an Obsidian mauler variant says 55; its stats give 57), `gp quality` reports
it instead of trusting either value silently.

## D10 · Memory is not a source
Twice in phase 1-2 a remembered game fact was wrong (Monkey Madness I "safe for pures"; Nature Spirit's Defence XP).
Rules are only coded after reading the wiki text; tests carry the expected values from the wiki.

## D11 · Completed quests are user input
The hiscores do not expose quest completion. `gp done add/remove` keeps a local list (`data/quests_done.txt`,
git-ignored). Without it the planner assumes no quests done and shows prerequisite chains instead of hiding them.

## D12 · Prices must be liquid to be used
Buy = instant-buy, sell = instant-sell (never overstate profit). A side older than 48 h is ignored, and a crossed
market (sell > 1.2 x buy) drops the sell side. Found because a thinly traded mead turned a cooking recipe into
"+1,659 gp/XP".

## D13 · Training: rank by XP per action, show XP/h only as a ceiling
Recipes give XP per action and ticks per action, so `XP x 6,000 / ticks` is a hard ceiling (1 tick = 0.6 s,
wiki "Game tick"). It ignores banking and walking, which dominate real rates, so it is displayed but never used to
rank. Only recipes whose materials can be bought on the GE are shown (minigame/quest-only inputs are excluded).
Combat training XP/h is not available from a structured source and is not estimated.

## D14 · Gear: one objective, stated
`gp gear` maximises melee strength bonus (the gear term of the max-hit formula, wiki "Maximum melee hit") under
budget, levels, build limits, quest requirements and one-item-per-slot (two-handed excludes weapon + shield) -
a multiple-choice knapsack. Items whose requirement could not be read are excluded by default.

## D15 · Nothing personal in the repository
The contact e-mail for the prices API User-Agent (`GP_CONTACT`) and the default RSN (`GP_RSN`) live in `.env`
(git-ignored). Test fixtures use a public account.
