"""'What do I do now?' - quests available now, next stat milestones, training methods, money makers.

Every number shown is either read from the store (wiki/API, with its source) or computed from those numbers with
a formula documented here. Nothing is estimated from outside knowledge.

Rankings (stated in the output so they can be judged):
  quests    : unlocks gear first, then useful XP (XP in skills the build does not lock), then quest points
  training  : recipes you can do now whose materials all have a GE price (so you can actually buy them),
              ranked by XP per action. The XP/h shown is a CEILING = XP per action x 6,000 ticks/hour / ticks per
              action (1 tick = 0.6 s, https://oldschool.runescape.wiki/w/Game_tick) - banking/walking not included,
              so real rates are lower; it is shown, never used for ranking.
  money     : profit/hour recomputed with current GE prices (outputs sold instantly, inputs bought instantly)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

from gp.recommend.prices import Price
from gp.rules.builds import Build
from gp.rules.guard import QuestFacts, Verdict, judge_all
from gp.rules.xp import xp_for_level

TICKS_PER_HOUR = 6000
GAME_TICK_SOURCE = "https://oldschool.runescape.wiki/w/Game_tick"
OK_STATUSES = {"safe", "fits", "safe_if_choose"}


@dataclass
class QuestOption:
    quest: str
    status: str
    quest_points: int
    useful_xp: int
    unlocks: list[str]
    notes: list[str]
    source_url: str


@dataclass
class QuestPlan:
    available: list[QuestOption] = field(default_factory=list)
    waiting_on_prerequisites: list[tuple[str, list[str]]] = field(default_factory=list)
    missing_levels: list[tuple[str, list[str]]] = field(default_factory=list)


def plan_quests(
    quests: dict[str, QuestFacts],
    verdicts: list[Verdict],
    *,
    build: Build,
    levels: dict[str, int | None],
    done: set[str],
    qp: dict[str, int],
    unlocks_by_quest: dict[str, list[str]],
) -> QuestPlan:
    plan = QuestPlan()
    by_name = {v.quest: v for v in verdicts}
    for name, verdict in by_name.items():
        if name in done or verdict.status not in OK_STATUSES:
            continue
        q = quests[name]
        missing = [f"{lvl} {s}" for s, lvl, _ in q.skill_reqs if (levels.get(s) or 1) < lvl]
        pending = [p for p in q.prerequisites if p in quests and p not in done]
        if pending:
            plan.waiting_on_prerequisites.append((name, pending))
            continue
        if missing:
            plan.missing_levels.append((name, missing))
            continue
        useful = 0
        for kind, skills, xp, qty in q.rewards:
            if (kind == "fixed" and skills and skills[0] not in build.limits) or (
                kind == "choice" and [s for s in skills if s not in build.limits or s == "Any"]
            ):
                useful += (xp or 0) * qty
        plan.available.append(
            QuestOption(
                name,
                verdict.status,
                qp.get(name) or 0,
                useful,
                unlocks_by_quest.get(name, []),
                [r for r in verdict.reasons if verdict.status != "safe"],
                q.source_url,
            )
        )
    plan.available.sort(key=lambda o: (-len(o.unlocks), -o.useful_xp, -o.quest_points, o.quest))
    return plan


def quest_plan(
    con: duckdb.DuckDBPyConnection,
    quests: dict[str, QuestFacts],
    *,
    build: Build,
    xp: dict[str, int],
    levels: dict[str, int | None],
    done: set[str],
    members: bool,
) -> QuestPlan:
    verdicts = judge_all(quests, build, xp, members=members)
    qp = dict(con.execute("select name, quest_points from quests").fetchall())
    unlocks: dict[str, list[str]] = {}
    for quest, page in con.execute(
        "select quest, page from item_requirements where kind = 'quest' order by page"
    ).fetchall():
        unlocks.setdefault(quest, []).append(page)
    return plan_quests(quests, verdicts, build=build, levels=levels, done=done, qp=qp, unlocks_by_quest=unlocks)


# ---------- milestones ----------
@dataclass
class Milestone:
    skill: str
    level: int
    xp_needed: int
    items: list[str]
    quests: list[str]


def milestones(
    con: duckdb.DuckDBPyConnection,
    *,
    build: Build,
    levels: dict[str, int | None],
    xp: dict[str, int],
    skills: list[str],
    members: bool,
    per_skill: int = 2,
) -> list[Milestone]:
    out = []
    for skill in skills:
        current = levels.get(skill) or 1
        cap = build.limits[skill].max if skill in build.limits else 99
        if current >= cap:
            continue
        items = con.execute(
            """select r.level, list(distinct r.page order by r.page) from item_requirements r
               join items i on i.page = r.page
               where r.kind = 'skill' and r.skill = ? and r.level > ? and r.level <= ? and i.tradeable
                 and (? or not coalesce(i.members, true))
               group by r.level""",
            [skill, current, cap, members],
        ).fetchall()
        quests = con.execute(
            """select r.level, list(distinct r.quest order by r.quest)
               from quest_requirements r join quests q on q.name = r.quest
               where r.kind = 'skill' and r.skill = ? and r.level > ? and r.level <= ?
                 and (? or not coalesce(q.members, true))
               group by r.level""",
            [skill, current, cap, members],
        ).fetchall()
        by_level: dict[int, Milestone] = {}
        for lvl, names in items:
            by_level.setdefault(lvl, Milestone(skill, lvl, xp_for_level(lvl) - xp.get(skill, 0), [], [])).items.extend(
                names
            )
        for lvl, names in quests:
            by_level.setdefault(lvl, Milestone(skill, lvl, xp_for_level(lvl) - xp.get(skill, 0), [], [])).quests.extend(
                names
            )
        out.extend(sorted(by_level.values(), key=lambda m: m.level)[:per_skill])
    return out


# ---------- training ----------
@dataclass
class TrainingOption:
    skill: str
    action: str
    level: int
    xp: float
    ticks: float
    xp_per_hour_ceiling: float
    gp_per_xp: float  # negative = costs money
    output_sold: bool  # False: the product has no GE price, so gp/XP counts only the materials
    source_url: str


def training_options(
    con: duckdb.DuckDBPyConnection,
    prices: dict[str, Price],
    *,
    levels: dict[str, int | None],
    skill: str,
    members: bool,
    top: int = 5,
) -> list[TrainingOption]:
    rows = con.execute(
        """select name, level, xp, ticks, materials, output_item, output_qty, other_skills, source_url, members
           from recipes where skill = ? and level <= ? and ticks > 0 and (? or not coalesce(members, true))""",
        [skill, levels.get(skill) or 1, members],
    ).fetchall()
    best: dict[str, TrainingOption] = {}
    for name, level, xp, ticks, materials, out_item, out_qty, others, url, _ in rows:
        if any((levels.get(o["skill"]) or 1) < o["level"] for o in others or []) or not materials:
            continue
        mat_prices = [prices.get(m["item"]) for m in materials]
        if any(p is None or p.buy is None for p in mat_prices):
            continue  # untradeable inputs (minigame/quest items) - not something to buy and train with
        cost = sum(p.buy * m["qty"] for p, m in zip(mat_prices, materials, strict=True))
        out_price = prices.get(out_item) if out_item else None
        sold = out_price is not None and out_price.sell is not None
        value = out_price.sell * (out_qty or 1) if sold else 0.0
        option = TrainingOption(
            skill, name.split("#")[0], level, xp, ticks, xp * TICKS_PER_HOUR / ticks, (value - cost) / xp, sold, url
        )
        key = option.action
        if key not in best or option.gp_per_xp > best[key].gp_per_xp:
            best[key] = option
    return sorted(best.values(), key=lambda t: (-t.xp, -t.gp_per_xp))[:top]


# ---------- money ----------
@dataclass
class MoneyOption:
    activity: str
    category: str | None
    profit_per_hour: float | None
    wiki_value_per_hour: float | None
    unpriced: list[str]
    source_url: str


def money_options(
    con: duckdb.DuckDBPyConnection,
    prices: dict[str, Price],
    *,
    levels: dict[str, int | None],
    build: Build,
    members: bool,
    top: int = 8,
) -> list[MoneyOption]:
    rows = con.execute(
        """select activity, category, actions_per_hour, skill_reqs, inputs, outputs, wiki_value_per_hour, source_url
           from money_methods where (? or not coalesce(members, true)) and not coalesce(recurring, false)""",
        [members],
    ).fetchall()
    out = []
    for activity, category, aph, skill_reqs, inputs, outputs, wiki_value, url in rows:
        reqs = skill_reqs or []
        if any((levels.get(r["skill"]) or 1) < r["level"] for r in reqs):
            continue
        if any(r["skill"] in build.limits and r["level"] > build.limits[r["skill"]].max for r in reqs):
            continue
        unpriced, total = [], 0.0
        for sign, rows_, side in ((1, outputs or [], "sell"), (-1, inputs or [], "buy")):
            for it in rows_:
                qty = it["qty"] * (1 if it["per_hour"] else (aph or 0))
                p = prices.get(it["item"])
                price = getattr(p, side) if p else None
                if price is None:
                    unpriced.append(it["item"])
                else:
                    total += sign * price * qty
        out.append(MoneyOption(activity, category, None if unpriced else total, wiki_value, unpriced, url))
    out.sort(key=lambda m: -(m.profit_per_hour if m.profit_per_hour is not None else (m.wiki_value_per_hour or 0)))
    return out[:top]
