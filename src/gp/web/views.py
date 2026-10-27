"""View models for the UI: the same service/recommend calls as the CLI, shaped for templates.

No game logic lives here - only selection, formatting and the account context that every page shares
(RSN, mode, build, F2P, level overrides), which travels in the query string so any view can be bookmarked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlencode

import duckdb
import httpx

from gp import progress, service
from gp.fmt import parse_gp
from gp.http import OfflineMiss
from gp.ingest.sources import HISCORE_BOARDS
from gp.normalize.quests import SKILLS
from gp.recommend import compare as compare_mod
from gp.recommend import gear as gear_mod
from gp.recommend import now as now_mod
from gp.recommend.prices import price_book
from gp.rules import combat as combat_rules
from gp.rules.builds import Build, BuildError, find_build, load_builds, violations
from gp.rules.guard import judge_all

# The in-game Skills tab, read row by row (layout only - not a game fact the planner relies on).
SKILL_GRID = [
    "Attack", "Hitpoints", "Mining",
    "Strength", "Agility", "Smithing",
    "Defence", "Herblore", "Fishing",
    "Ranged", "Thieving", "Cooking",
    "Prayer", "Crafting", "Firemaking",
    "Magic", "Fletching", "Woodcutting",
    "Runecraft", "Slayer", "Farming",
    "Construction", "Hunter", "Sailing",
]  # fmt: skip
TRAINING_SKILLS = ("Cooking", "Fletching", "Crafting", "Smithing", "Herblore", "Firemaking")
DEFAULT_BUILD = "main"


# ---------- account context (shared by every page) ----------
@dataclass
class Context:
    rsn: str = ""
    mode: str = "normal"
    build_id: str = DEFAULT_BUILD
    f2p: bool = False
    sets: str = ""  # "Defence=1, Prayer=43"

    @classmethod
    def from_query(cls, params) -> Context:
        mode = params.get("mode", "normal")
        return cls(
            rsn=(params.get("rsn") or "").strip()[:12],  # RuneScape names are at most 12 characters
            mode=mode if mode in HISCORE_BOARDS else "normal",
            build_id=params.get("build") or DEFAULT_BUILD,
            f2p=params.get("f2p") in ("1", "on", "true"),
            sets=(params.get("set") or "").strip()[:200],
        )

    def query(self, **extra) -> str:
        """Query string for links: the account context plus page parameters (lists repeat the key)."""
        pairs = [
            ("rsn", self.rsn),
            ("mode", "" if self.mode == "normal" else self.mode),
            ("build", self.build_id),
            ("f2p", "1" if self.f2p else ""),
            ("set", self.sets),
        ]
        for k, v in extra.items():
            pairs += [(k, item) for item in v] if isinstance(v, list) else [(k, v)]
        return urlencode([(k, v) for k, v in pairs if v not in ("", None)])

    @property
    def members(self) -> bool:
        return not self.f2p


def parse_overrides(text: str) -> tuple[dict[str, int], list[str]]:
    """'Defence=1, prayer 43' -> ({'Defence': 1, 'Prayer': 43}, errors)."""
    out, errors = {}, []
    for part in filter(None, (p.strip() for p in re.split(r"[,;]", text))):
        m = re.fullmatch(r"([A-Za-z]+)\s*[=: ]\s*(\d{1,2})", part)
        skill = m.group(1).capitalize() if m else None
        if not m or skill not in SKILLS or not 1 <= int(m.group(2)) <= 99:
            errors.append(f"could not read {part!r} - use Skill=level, e.g. Defence=1")
            continue
        out[skill] = int(m.group(2))
    return out, errors


@dataclass
class SkillCell:
    skill: str
    level: int | None
    limit: int | None
    over: bool


@dataclass
class Panel:
    """Everything the side panel shows; built once per request."""

    ctx: Context
    builds: list[Build]
    build: Build | None
    account: service.Account | None
    errors: list[str] = field(default_factory=list)
    grid: list[SkillCell] = field(default_factory=list)
    total_level: int | None = None
    combat: combat_rules.Combat | None = None
    next_combat: dict[str, int | None] = field(default_factory=dict)
    broken: list[str] = field(default_factory=list)
    done: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return self.build is not None and self.account is not None


def panel(con: duckdb.DuckDBPyConnection, ctx: Context) -> Panel:
    builds = list(load_builds().values())
    p = Panel(ctx, builds, None, None, done=progress.load_done())
    try:
        p.build = find_build(ctx.build_id)
        ctx.build_id = p.build.id
    except BuildError as exc:
        p.errors.append(str(exc))
    overrides, errors = parse_overrides(ctx.sets)
    p.errors += errors
    try:
        p.account = service.load_account(ctx.rsn or None, ctx.mode, overrides, con=con)
    except LookupError as exc:
        p.errors.append(f"{exc}. Wrong account mode, or the name is not ranked in any skill.")
    except (httpx.HTTPError, OfflineMiss) as exc:
        p.errors.append(
            f"Could not reach the hiscores ({type(exc).__name__}). Offline? Clear the name to plan a fresh account."
        )
    if p.account is None:
        return p
    acc = p.account
    for skill in SKILL_GRID:
        # a fresh account starts every skill at 1 (Hitpoints at 10); an unranked skill of a real account is unknown
        level = acc.levels.get(skill) if acc.rsn else acc.levels.get(skill, 1)
        limit = p.build.limits[skill].max if p.build and skill in p.build.limits else None
        p.grid.append(SkillCell(skill, level, limit, limit is not None and level is not None and level > limit))
    levels = [c.level for c in p.grid]
    p.total_level = None if None in levels else sum(levels)
    p.combat = combat_rules.combat(acc.levels)
    p.next_combat = combat_rules.levels_to_next_combat(acc.levels)
    if p.build:
        p.broken = violations(p.build, acc.levels)
    return p


# ---------- pages ----------
def now_page(con: duckdb.DuckDBPyConnection, p: Panel) -> dict:
    b, acc = p.build, p.account
    prices, _ = price_book(con)
    plan = now_mod.quest_plan(
        con, service.quest_facts(con), build=b, xp=acc.xp, levels=acc.levels, done=p.done, members=p.ctx.members
    )
    skills = list(b.limits) + [
        s for s in ("Attack", "Strength", "Defence", "Ranged", "Magic", "Prayer") if s not in b.limits
    ]
    milestones = now_mod.milestones(con, build=b, levels=acc.levels, xp=acc.xp, skills=skills, members=p.ctx.members)
    training = [
        t
        for skill in TRAINING_SKILLS
        for t in now_mod.training_options(con, prices, levels=acc.levels, skill=skill, members=p.ctx.members, top=3)
    ]
    money = now_mod.money_options(con, prices, levels=acc.levels, build=b, members=p.ctx.members)
    return {"plan": plan, "milestones": milestones, "training": training, "money": money}


GUARD_FILTERS = {
    "problems": {"blocked", "risk", "unknown", "safe_if_choose"},
    "safe": {"safe", "fits"},
    "all": {"blocked", "risk", "unknown", "safe_if_choose", "fits", "safe"},
}
STATUS_ORDER = ["blocked", "risk", "unknown", "safe_if_choose", "fits", "safe"]


def guard_page(con: duckdb.DuckDBPyConnection, p: Panel, show: str) -> dict:
    show = show if show in GUARD_FILTERS else "problems"
    verdicts = judge_all(service.quest_facts(con), p.build, p.account.xp, members=p.ctx.members)
    counts = {s: sum(v.status == s for v in verdicts) for s in STATUS_ORDER}
    rows = sorted(
        (v for v in verdicts if v.status in GUARD_FILTERS[show]), key=lambda v: (STATUS_ORDER.index(v.status), v.quest)
    )
    urls = dict(con.execute("select name, source_url from quests").fetchall())
    return {"rows": rows, "counts": counts, "show": show, "urls": urls, "filters": list(GUARD_FILTERS)}


# in-game Worn Equipment layout: (slot, grid column, grid row)
EQUIPMENT_LAYOUT = [
    ("head", 2, 1),
    ("cape", 1, 2), ("neck", 2, 2), ("ammo", 3, 2),
    ("weapon", 1, 3), ("body", 2, 3), ("shield", 3, 3),
    ("legs", 2, 4),
    ("hands", 1, 5), ("feet", 2, 5), ("ring", 3, 5),
]  # fmt: skip


def gear_page(con: duckdb.DuckDBPyConnection, p: Panel, budget_text: str, allow_unverified: bool) -> dict:
    try:
        budget = parse_gp(budget_text)
    except ValueError:
        return {"error": f"Budget {budget_text!r} not understood - use 500k, 5m or 1.2b.", "budget_text": budget_text}
    _, by_id = price_book(con)
    cands, excluded = gear_mod.candidates(
        con,
        by_id,
        build=p.build,
        levels=p.account.levels,
        done=p.done,
        members=p.ctx.members,
        allow_unverified=allow_unverified,
    )
    best = gear_mod.best_set(cands, budget)
    worn = {c.slot: c for c in best.items}
    if "2h" in worn:
        worn["weapon"] = worn.pop("2h")
    return {
        "budget": budget,
        "budget_text": budget_text,
        "best": best,
        "worn": worn,
        "two_handed": any(c.slot == "2h" for c in best.items),
        "layout": EQUIPMENT_LAYOUT,
        "excluded": sorted(excluded.items(), key=lambda kv: -kv[1]),
        "allow_unverified": allow_unverified,
        "candidates": len(cands),
        "source": gear_mod.SOURCE,
    }


def build_page(con: duckdb.DuckDBPyConnection, p: Panel) -> dict:
    variants = service.build_variants(p.build, con)
    unlocks = [service.unlock_requirements(item, con) for item in p.build.key_unlocks]
    return {"variants": variants, "unlocks": unlocks, "combat_source": combat_rules.SOURCE}


def compare_page(con: duckdb.DuckDBPyConnection, p: Panel, chosen: list[str]) -> dict:
    ids = [b.id for b in p.builds]
    # default: the current build next to up to three others
    others = [i for i in ids if i not in (p.build.id, "main")][:3]
    chosen = [c for c in chosen if c in ids] or [p.build.id, *others]
    prices, _ = price_book(con)
    quests = service.quest_facts(con)
    by_id = {b.id: b for b in p.builds}
    return {"summaries": [compare_mod.summarize(con, by_id[i], quests, prices) for i in chosen], "chosen": chosen}


def quests_page(con: duckdb.DuckDBPyConnection, p: Panel) -> dict:
    facts = service.quest_facts(con)
    verdicts = {v.quest: v for v in judge_all(facts, p.build, p.account.xp, members=True)}
    rows = []
    for name, members, difficulty, qp, url in con.execute(
        "select name, members, difficulty, quest_points, source_url from quests order by name"
    ).fetchall():
        q = facts[name]
        verdict = verdicts.get(name)
        missing = [f"{lvl} {s}" for s, lvl, _ in q.skill_reqs if (p.account.levels.get(s) or 1) < lvl]
        waiting = [pre for pre in q.prerequisites if pre in facts and pre not in p.done]
        rows.append(
            {
                "name": name,
                "members": members,
                "difficulty": difficulty,
                "qp": qp or 0,
                "url": url,
                "done": name in p.done,
                "status": verdict.status if verdict else "unknown",
                "reasons": verdict.reasons if verdict else [],
                "missing": missing,
                "waiting": waiting,
            }
        )
    qp_done = sum(r["qp"] for r in rows if r["done"])
    return {"rows": rows, "qp_done": qp_done, "qp_total": sum(r["qp"] for r in rows)}


def data_page(con: duckdb.DuckDBPyConnection) -> dict:
    tables = []
    for (name,) in con.execute("select table_name from duckdb_tables() order by 1").fetchall():
        n, lo, hi = con.execute(f"select count(*), min(fetched_at), max(fetched_at) from {name}").fetchone()
        tables.append({"name": name, "rows": n, "oldest": lo, "newest": hi})
    found, none_stated, total = con.execute(
        """select count(distinct page) filter (where equip_req_status = 'found'),
                  count(distinct page) filter (where equip_req_status = 'no_requirement'),
                  count(distinct page)
           from items where tradeable"""
    ).fetchone()
    unparsed = con.execute(
        "select quest, raw_text, source_url from quest_xp_rewards where kind = 'unparsed' order by quest"
    ).fetchall()
    return {
        "tables": tables,
        "equip": {"found": found, "none_stated": none_stated, "total": total},
        "unparsed": unparsed,
    }


def freshness(con: duckdb.DuckDBPyConnection) -> dict:
    """Header line: how old the wiki snapshot and the prices are."""
    wiki, prices = con.execute(
        "select (select max(fetched_at) from quests), (select max(fetched_at) from prices)"
    ).fetchone()
    now = datetime.now(UTC)
    return {
        "wiki": wiki,
        "prices": prices,
        "prices_age_min": int((now - prices).total_seconds() // 60) if prices else None,
    }


def store_ready(con: duckdb.DuckDBPyConnection) -> bool:
    return con.execute("select count(*) from quests").fetchone()[0] > 0
