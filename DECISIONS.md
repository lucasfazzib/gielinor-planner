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

## D7 · Nothing personal in the repository
The contact e-mail for the prices API User-Agent (`GP_CONTACT`) and the default RSN (`GP_RSN`) live in `.env`
(git-ignored). Test fixtures use a public account.
