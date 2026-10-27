# Sources

Every game fact in the store has `source_url` + `fetched_at` (and `source_revid` for wiki pages, i.e. the exact
page revision that produced it). Raw responses are cached under `data/raw/<source>/` with their fetch time,
so the app works offline after the first `gp ingest` (`GP_OFFLINE=1`).

| Source | Endpoint | Used for | Format (verified 2026-10-26) | Freshness | Rules we follow |
|---|---|---|---|---|---|
| OSRS Wiki - Bucket | `https://oldschool.runescape.wiki/api.php?action=bucket&query=...` | quests (`quest`), build variants (`infobox_pure`), items (`infobox_item`), equipment stats (`infobox_bonuses`), training recipes (`recipe.production_json`: XP, level, ticks, materials, product), money-making methods (`money_making_guide`) | JSON rows; max 5000 per query, paginated with `.offset()` | 7 days | descriptive User-Agent, >= 1 s between requests, cached |
| OSRS Wiki - page wikitext | `api.php?action=query&prop=revisions&rvprop=content\|ids\|timestamp` (50 titles per call) | quest rewards (`{{Quest rewards}}`: QP, `{{SCP\|Skill\|xp}}`, lamps), quest `members` flag, equip requirements (lead sentence) | wikitext + revision id | 7 days | same |
| OSRS Wiki - images | `api.php?action=query&prop=imageinfo&iiprop=url` (one call for all files), then each image URL | skill and interface icons for the UI (34 files, names checked to exist 2026-10-27) | PNG, cached base64 in the raw layer, stored in `icons` | 30 days | same; downloaded once, served locally |
| OSRS Wiki - real-time prices | `https://prices.runescape.wiki/api/v1/osrs/mapping`, `/latest`, `/1h` | GE prices (instant buy/sell + time), buy limits, alch values, 1-hour traded volume (liquidity check, `DECISIONS.md` D12) | `/latest` and `/1h` = whole market in one call each | mapping 1 day, latest/1h 5 min | UA with contact (`GP_CONTACT`), **never per item**, cached |
| Jagex hiscores | `https://secure.runescape.com/m=<board>/index_lite.json?player=<rsn>` | current levels/XP of a player | JSON with skill names (25 skills incl. Sailing); unranked = -1; 404 if not on that board | 10 min | one call per lookup |
| Community notes | `notes/community/*.md` | tips curated by hand with a link to the thread | markdown | manual | **no scraping** of Reddit or forums |

Boards per account mode: `hiscore_oldschool` (normal), `hiscore_oldschool_ironman`,
`hiscore_oldschool_hardcore_ironman`, `hiscore_oldschool_ultimate`.

Reference pages (checked to exist, used by later phases): Account builds, Combat pure, Free-to-play account builds,
Berserker (Account build), One-defence pure, Obsidian mauler, Medium-level build, Defence pure, Tanking, Skill pure,
Quest experience rewards, Optimal quest guide, Combat level (formula). "PvP account builds" redirects to Combat pure,
"Tank" to Tanking, "Skiller" to Skill pure.

Game facts checked on the wiki (not from memory): Sailing released 19 November 2025 (members skill, starts with
the quest Pandemonium) - `https://oldschool.runescape.wiki/w/Sailing`. Game tick = 0.6 s -
`https://oldschool.runescape.wiki/w/Game_tick`. Melee max hit: Base Damage = 0.5 + Effective Strength x
(Strength Bonus + 64) / 640 - `https://oldschool.runescape.wiki/w/Maximum_melee_hit`.

Not used yet: Wise Old Man API (XP history) - planned for a later phase.
