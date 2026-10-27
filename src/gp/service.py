"""Glue between the store and the rules: loads facts, account state and build variants. Used by the CLI and the UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import duckdb

from gp.ingest import pipeline
from gp.rules.builds import Build
from gp.rules.combat import COMBAT_SKILLS, DEFAULTS, combat
from gp.rules.guard import QuestFacts
from gp.rules.xp import xp_for_level
from gp.store import connect


@dataclass
class Account:
    rsn: str | None
    mode: str
    levels: dict[str, int | None]
    xp: dict[str, int]
    source_url: str | None
    fetched_at: datetime | None

    @classmethod
    def fresh(cls) -> Account:
        """A brand-new account: every skill 1, Hitpoints 10 (1,154 XP)."""
        levels = {s: DEFAULTS.get(s, 1) for s in [*COMBAT_SKILLS]}
        return cls(None, "normal", levels, {s: xp_for_level(lv) for s, lv in levels.items()}, None, None)


def load_account(
    rsn: str | None,
    mode: str = "normal",
    overrides: dict[str, int] | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> Account:
    if not rsn:
        acc = Account.fresh()
    else:
        result = pipeline.player(rsn, mode, con=con)
        if result is None:
            raise LookupError(f"{rsn!r} is not on the {mode} hiscores")
        stats, url, fetched_at = result
        levels = {s.skill: s.level for s in stats if s.skill != "Overall"}
        xp = {s.skill: s.xp for s in stats if s.skill != "Overall" and s.xp is not None}
        acc = Account(rsn, mode, levels, xp, url, fetched_at)
    for skill, level in (overrides or {}).items():
        acc.levels[skill] = level
        acc.xp[skill] = xp_for_level(level)
    # unranked combat skills of a real account stay unknown for display, but the guard needs a number:
    # assume the minimum XP of the known/assumed level (documented in the CLI output)
    for skill in COMBAT_SKILLS:
        if skill not in acc.xp:
            acc.xp[skill] = xp_for_level(acc.levels.get(skill) or DEFAULTS[skill])
    return acc


def quest_facts(con: duckdb.DuckDBPyConnection | None = None) -> dict[str, QuestFacts]:
    con = con or connect(read_only=True)
    facts = {
        name: QuestFacts(name, members, parsed, source_url=url)
        for name, members, parsed, url in con.execute(
            "select name, members, rewards_parsed, source_url from quests"
        ).fetchall()
    }
    for quest, kind, skill, level, boostable, pre in con.execute(
        "select quest, kind, skill, level, boostable, prerequisite_quest from quest_requirements"
    ).fetchall():
        if quest not in facts:
            continue
        if kind == "skill":
            facts[quest].skill_reqs.append((skill, level, bool(boostable)))
        elif kind == "quest":
            facts[quest].prerequisites.append(pre)
    for quest, kind, skills, xp, quantity in con.execute(
        "select quest, kind, skills, xp, quantity from quest_xp_rewards"
    ).fetchall():
        if quest in facts:
            facts[quest].rewards.append((kind, list(skills or []), xp, quantity or 1))
    return facts


def build_variants(build: Build, con: duckdb.DuckDBPyConnection | None = None) -> list[dict]:
    """The wiki's variants of this build, with the combat level our formula computes next to the wiki's claim."""
    con = con or connect(read_only=True)
    cur = con.execute("select * from pure_builds where name = ? order by defence, attack, prayer", [build.wiki_build])
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        v = dict(zip(cols, row, strict=True))
        lv = {s: v[s.lower()] for s in COMBAT_SKILLS}
        v["computed_combat"] = combat(lv).level
        claimed = [int(c) for c in (v["combat_levels"] or []) if str(c).isdigit()]
        v["combat_matches_wiki"] = None if len(claimed) != 1 else claimed[0] == v["computed_combat"]
        out.append(v)
    return out


def unlock_requirements(item: str, con: duckdb.DuckDBPyConnection | None = None) -> dict:
    con = con or connect(read_only=True)
    rows = con.execute(
        "select kind, skill, level, quest, evidence, source_url from item_requirements where page = ?", [item]
    ).fetchall()
    return {
        "item": item,
        "skills": [(s, lv) for k, s, lv, *_ in rows if k == "skill"],
        "quests": [q for k, _, _, q, *_ in rows if k == "quest"],
        "evidence": rows[0][4] if rows else None,
        "source_url": rows[0][5] if rows else None,
    }
