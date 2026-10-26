"""Side-by-side build comparison. Only facts and arithmetic on facts - no time estimates, because combat XP/h
depends on gear, monster and method and is not in any structured source we use (stated in the output)."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from gp.recommend.prices import Price
from gp.rules.builds import Build
from gp.rules.combat import COMBAT_SKILLS
from gp.rules.guard import QuestFacts, judge_all
from gp.rules.xp import xp_for_level
from gp.service import Account, build_variants, unlock_requirements


@dataclass
class BuildSummary:
    build: Build
    members_only: bool
    combat_range: tuple[int, int] | None
    types: list[str]
    xp_to_lowest_variant: int | None
    xp_to_highest_variant: int | None
    quests_blocked: int
    quests_at_risk: int
    quests_safe: int
    unlocks: list[tuple[str, str, int | None]]  # (item, requirements, GE price)


def summarize(
    con: duckdb.DuckDBPyConnection, build: Build, quests: dict[str, QuestFacts], prices: dict[str, Price]
) -> BuildSummary:
    fresh = Account.fresh()
    variants = build_variants(build, con)
    computed = [v["computed_combat"] for v in variants]

    def xp_needed(v: dict) -> int:
        return sum(max(0, xp_for_level(v[s.lower()]) - fresh.xp.get(s, 0)) for s in COMBAT_SKILLS if v[s.lower()])

    needs = sorted(xp_needed(v) for v in variants)
    verdicts = judge_all(quests, build, fresh.xp, members=True)
    count = {s: sum(v.status == s for v in verdicts) for s in ("blocked", "risk", "safe", "fits", "safe_if_choose")}
    unlocks = []
    for item in build.key_unlocks:
        req = unlock_requirements(item, con)
        text = ", ".join([f"{lv} {s}" for s, lv in req["skills"]] + req["quests"]) or "no requirement found"
        p = prices.get(item)
        unlocks.append((item, text, p.buy if p else None))
    types = sorted({t for v in variants for t in (v["types"] or [])})
    return BuildSummary(
        build=build,
        members_only=not build.f2p_possible,
        combat_range=(min(computed), max(computed)) if computed else None,
        types=types,
        xp_to_lowest_variant=needs[0] if needs else None,
        xp_to_highest_variant=needs[-1] if needs else None,
        quests_blocked=count["blocked"],
        quests_at_risk=count["risk"],
        quests_safe=count["safe"] + count["fits"] + count["safe_if_choose"],
        unlocks=unlocks,
    )
