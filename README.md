# gielinor-planner

An Old School RuneScape account planner: given your stats, account mode and the build you are going for,
what should you do next — which quest, where to train, what to buy — without ruining the build
(e.g. a quest that hands out Defence experience to a 1-Defence pure).

The rule of the project: **every game fact is traceable**. Each row in the local store records the wiki page and
revision (or API endpoint) it came from and when it was fetched. If something cannot be traced, it is flagged —
never guessed.

> **Status: phase 2 — builds, combat calculator, XP guard.** Next: "what do I do now" + training methods +
> gear upgrades with GE prices (phase 3), then a local web UI in the style of a RuneLite side panel (phase 4).
> Unofficial fan project. Game data © Jagex; wiki content from the [OSRS Wiki](https://oldschool.runescape.wiki)
> (CC BY-NC-SA 3.0); prices from the OSRS Wiki real-time prices API.

## Quick start

```bash
cp .env.example .env     # optional: GP_CONTACT (sent in the User-Agent to the prices API), GP_RSN
uv sync
uv run gp ingest                       # wiki + GE prices -> raw cache -> DuckDB
uv run gp stats --rsn "Lynx Titan"     # any public name; --mode normal|ironman|hardcore|ultimate
uv run gp quest "Fairytale I - Growing Pains"
uv run gp db                           # what is in the store and how fresh it is
uv run gp builds                       # builds/*.yaml
uv run gp build zerker                 # limits with the wiki's wording, wiki variants, key unlock requirements
uv run gp combat --rsn "Lynx Titan"    # combat level + how many levels of each skill raise it
uv run gp combat --then Strength=85    # "if I train Strength to 85, do I leave my bracket?"
uv run gp guard "1 def pure" --f2p     # quests that would ruin the build, and why
uv run gp guard zerker --rsn "SomeName" --show all
GP_OFFLINE=1 uv run gp quest "Waterfall Quest"   # works offline after the first ingest
uv run pytest
```

## First ingest (2026-10-26)

| What | Result |
|---|---|
| Quests (Bucket `quest`, keyed by quest name) | **223** quests, 672 requirements (skill / quest / QP) |
| Quest XP reward lines | 389: **352 fixed**, 28 choosable (lamps/tomes), 1 choice between packages, 2 random, 6 not classified (listed by `gp quality`) |
| Quests without a `{{Quest rewards}}` template | 28 (mostly miniquests and guide pages) |
| Equipable items | 5,433 item ids on 4,080 pages; wield/wear requirement found on 1,074 pages |
| Tradeable equipable items with a requirement | 802 of 2,101 (others: no requirement, or phrased differently -> treated as unknown) |
| Build variants (Bucket `infobox_pure`) | 60 |
| GE prices | 4,674 items mapped, 4,523 with a current price (one `/latest` call) |
| Full ingest time | ~1.5 min from a warm cache, ~3 min cold (1 request/s to the wiki) |

Example — quests that hand out **Defence** XP, the thing a pure must avoid (from `quest_xp_rewards`):
`King's Ransom` 33,000 · `Dragon Slayer I` 18,650 · `Holy Grail` 15,300 · `Olaf's Quest` 12,000 (fixed).
`The Fremennik Isles` (2 x 10,000 in one of Attack/Strength/Defence/Hitpoints) is a real **choice** — safe if you
pick something else. `Monkey Madness I` looks like one but is a **choice between packages** that both contain
Defence (35,000 or 20,000) — stored as `choice_bundle` and treated as the worst case.


## Phase 2 — rules (2026-10-26)

- **10 builds** as YAML (main, one-defence pure, berserker, obsidian mauler, strength pure, medium-level,
  defence pure, skill pure, rune pure, adamant pure). Each limit quotes the wiki page that justifies it.
- **Combat calculator** with the official formula in exact fractions; checked against the wiki's build variants
  (One-defence pure 79, Berserker 83/92/99 all match; one Obsidian mauler variant disagrees and is reported).
- **XP guard** for a fresh one-defence pure (members): Dragon Slayer I (+18,650 Defence), Nature Spirit
  (+2,000 Defence) and everything that requires them (Fairytale I, the Myreque chain, ...) come out `blocked`;
  The Fremennik Isles is `safe_if_choose`; Monkey Madness I is a `risk` (both reward packages give Defence).
  F2P: 23 quests safe, Dragon Slayer I blocked.

## Data engineering concepts in this project

| Concept | Where | Why it matters here |
|---|---|---|
| **Prefer structured sources** | `ingest/sources.py`: wiki **Bucket** API → wiki **templates** → prose (last resort) | Parsing HTML/prose is brittle; the wiki exposes typed tables (Bucket) and templates (`{{Quest rewards}}`, `{{SCP\|Skill\|xp}}`). Prose is used only for equipment requirements, which exist nowhere else |
| **Raw layer = immutable cache** | `http.py` stores every response with URL + `fetched_at` | Re-parsing never needs the network; bugs in parsers are fixed and replayed from raw; the app works offline |
| **Freshness per source (TTL)** | wiki 7 d, price mapping 1 d, prices 5 min, hiscores 10 min | Each source changes at its own speed; one global refresh would either hammer the APIs or serve stale prices |
| **Polite ingestion** | descriptive User-Agent, ≥1 s between calls, `Retry-After`/backoff on 429/5xx, bulk endpoints (`/latest` once, 50 pages per wiki call) | Free community APIs stay free when clients behave; the prices API explicitly asks for this |
| **Pagination** | Bucket queries `limit(5000).offset(n)` (server max verified) | Items and bonuses exceed one page |
| **Idempotent rebuild** | `ingest/pipeline.py` truncates and rebuilds wiki/price tables from raw | Running ingest twice gives the same store; no duplicates, no drift |
| **Append-only snapshots** | `player_snapshots` | Stats history for "how much did I gain this week" later |
| **Provenance / lineage at row level** | `source_url`, `source_revid`, `fetched_at` on every game row | Any number shown can be clicked back to the exact wiki revision that produced it |
| **Entity keys are not page names** | quests keyed by the quest name inside the row (`DECISIONS.md` D6) | One wiki page can hold many quest rows (Recipe for Disaster) — the classic "natural key" trap |
| **Unknown ≠ default** | unranked hiscore skills stay `NULL`; empty Bucket booleans stay `NULL` | Treating "unknown" as "level 1" or "false" would make the planner silently wrong |
| **Data quality reporting** | `gp ingest` reports unparsed reward lines and items without a requirement sentence | Gaps are visible and reviewable instead of hidden |
| **Pure parsers + recorded fixtures** | `normalize/*` are pure functions; tests use real responses saved by `scripts/record_fixtures.py` | Tests run offline in CI and break loudly when the wiki changes format |
| **Configuration as data** | `builds/*.yaml` + a validating loader | New builds without code; invalid files fail fast with a clear message |
| **Join, don't copy** | build variants and unlock requirements are joined from the store at runtime | Numbers have one home (the wiki snapshot) and cannot drift between YAML and data |
| **Cross-source validation** | formula-computed combat vs the infobox's stated combat (`gp quality`) | Two sources that should agree are compared; disagreements are surfaced, not hidden |
| **Graph propagation** | quest prerequisites resolved recursively in the guard | A dependency's problem is the dependent's problem — like upstream data quality |
| **Secrets out of the repo** | `.env` (git-ignored) for contact/RSN; gitleaks in CI | Nothing personal in a public repo |

## Layout

```
src/gp/
  config.py          settings from .env (never committed)
  http.py            cached, polite HTTP client = raw layer (+ offline mode)
  ingest/sources.py  one fetcher per endpoint (wiki Bucket, wiki pages, prices, hiscores)
  ingest/pipeline.py raw -> normalize -> DuckDB with provenance; idempotent
  normalize/         pure parsers: quest requirements & rewards, equip requirements, hiscores
  store.py           DuckDB schema
  cli.py             `gp` commands (typer + rich)
scripts/record_fixtures.py   saves small real responses for the tests
tests/                       parsers on recorded data, HTTP cache/offline/retry
```

## Store (DuckDB)

| Table | Grain | Source |
|---|---|---|
| `quests` | quest | Bucket `quest` + quest page (`{{Quest rewards}}`, members flag) |
| `quest_requirements` | quest × requirement (skill / quest / QP) | Bucket `quest.requirements` (rendered `data-skill`/`data-level` spans) |
| `quest_xp_rewards` | quest × reward line (`fixed` / `choice` / `random` / `unparsed`) | `{{Quest rewards}}` |
| `items` | equipable item × id | Bucket `infobox_item` + `infobox_bonuses` |
| `item_requirements` | item × skill, with the evidence sentence | item page lead sentence (`provenance = wiki_prose`) |
| `pure_builds` | build variant | Bucket `infobox_pure` |
| `prices` | item (latest) | prices API `/mapping` + `/latest` |
| `player_snapshots` | rsn × skill × fetch (appended) | official hiscores `index_lite.json` |

See [SOURCES.md](SOURCES.md) for every source and its rules, and [DECISIONS.md](DECISIONS.md) for design choices.
