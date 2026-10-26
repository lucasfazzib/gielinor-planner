"""ingest -> normalize -> DuckDB. Idempotent: wiki/price tables are rebuilt from the raw cache each run."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime

import duckdb

from gp.http import Client
from gp.ingest import sources as src
from gp.normalize.hiscores import parse_hiscores
from gp.normalize.items import equip_requirements
from gp.normalize.quests import parse_requirements, parse_rewards
from gp.store import DDL, REBUILT, connect

log = logging.getLogger(__name__)

BONUS_FIELDS = {
    "stab_attack_bonus": "stab_att",
    "slash_attack_bonus": "slash_att",
    "crush_attack_bonus": "crush_att",
    "range_attack_bonus": "range_att",
    "magic_attack_bonus": "magic_att",
    "stab_defence_bonus": "stab_def",
    "slash_defence_bonus": "slash_def",
    "crush_defence_bonus": "crush_def",
    "range_defence_bonus": "range_def",
    "magic_defence_bonus": "magic_def",
    "strength_bonus": "strength",
    "ranged_strength_bonus": "ranged_strength",
    "magic_damage_bonus": "magic_damage",
    "prayer_bonus": "prayer",
    "weapon_attack_speed": "attack_speed",
}


def _bool(value) -> bool | None:
    """Bucket booleans arrive as true/false, but empty fields come back as '' - keep those unknown."""
    return value if isinstance(value, bool) else None


def _members(wikitext: str) -> bool | None:
    m = re.search(r"\|\s*members\s*=\s*(yes|no)", wikitext or "", re.I)
    return None if not m else m.group(1).lower() == "yes"


def _insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0])
    con.executemany(
        f"insert into {table} ({', '.join(cols)}) values ({', '.join('?' * len(cols))})",
        [[r[c] for c in cols] for r in rows],
    )
    return len(rows)


def ingest_quests(client: Client, con: duckdb.DuckDBPyConnection) -> dict:
    fields = ["page_name", "official_difficulty", "official_length", "requirements", "json"]
    # A page can hold several quest rows (e.g. "Recipe for Disaster/Full guide" repeats every subquest), so the key
    # is the quest name inside `json`; the first row per name wins, preferring the quest's own page.
    by_name: dict[str, tuple[dict, object]] = {}
    for row, resp in src.bucket_rows(client, "quest", fields):
        name = json.loads(row.get("json") or "{}").get("name") or row["page_name"]
        if name not in by_name or row["page_name"] == name:
            by_name[name] = (row, resp)
    pages = src.wikitexts(client, sorted({row["page_name"] for row, _ in by_name.values()}))
    quests, reqs, rewards = [], [], []
    unparsed = []
    for name, (row, resp) in sorted(by_name.items()):
        url = src.page_url(row["page_name"])
        page = pages.get(row["page_name"], {}) if row["page_name"] == name else {}
        parsed_rewards = parse_rewards(page.get("content", ""))
        req = parse_requirements(row.get("requirements", ""))
        quests.append(
            {
                "name": name,
                "members": _members(page.get("content", "")),
                "difficulty": row.get("official_difficulty"),
                "length": row.get("official_length"),
                "quest_points": parsed_rewards.quest_points,
                "rewards_parsed": parsed_rewards.found_template,
                "source_url": url,
                "source_revid": page.get("revid"),
                "fetched_at": resp.fetched_at,
            }
        )
        for s in req.skills:
            reqs.append(
                {
                    "quest": name,
                    "kind": "skill",
                    "skill": s.skill,
                    "level": s.level,
                    "boostable": s.boostable,
                    "required_to_start": s.required_to_start,
                    "prerequisite_quest": None,
                    "raw_text": None,
                    "source_url": url,
                    "fetched_at": resp.fetched_at,
                }
            )
        for q in req.quests:
            reqs.append(
                {
                    "quest": name,
                    "kind": "quest",
                    "skill": None,
                    "level": None,
                    "boostable": None,
                    "required_to_start": True,
                    "prerequisite_quest": q,
                    "raw_text": None,
                    "source_url": url,
                    "fetched_at": resp.fetched_at,
                }
            )
        if req.quest_points:
            reqs.append(
                {
                    "quest": name,
                    "kind": "quest_points",
                    "skill": None,
                    "level": req.quest_points,
                    "boostable": None,
                    "required_to_start": True,
                    "prerequisite_quest": None,
                    "raw_text": None,
                    "source_url": url,
                    "fetched_at": resp.fetched_at,
                }
            )
        for x in parsed_rewards.xp:
            rewards.append(
                {
                    "quest": name,
                    "kind": x.kind,
                    "skills": x.skills,
                    "xp": x.xp,
                    "quantity": x.quantity,
                    "raw_text": x.raw_text,
                    "source_url": url,
                    "source_revid": page.get("revid"),
                    "fetched_at": page.get("fetched_at", resp.fetched_at),
                }
            )
            if x.kind == "unparsed":
                unparsed.append(name)
    return {
        "quests": _insert(con, "quests", quests),
        "quest_requirements": _insert(con, "quest_requirements", reqs),
        "quest_xp_rewards": _insert(con, "quest_xp_rewards", rewards),
        "quests_without_reward_template": sum(not q["rewards_parsed"] for q in quests),
        "unparsed_reward_lines": len(unparsed),
    }


def ingest_items(client: Client, con: duckdb.DuckDBPyConnection, with_pages: bool = True) -> dict:
    items = {
        r["page_name_sub"]: (r, resp)
        for r, resp in src.bucket_rows(
            client,
            "infobox_item",
            ["page_name", "page_name_sub", "item_name", "item_id", "is_members_only", "tradeable"],
        )
    }
    bonuses = list(
        src.bucket_rows(client, "infobox_bonuses", ["page_name", "page_name_sub", "equipment_slot", *BONUS_FIELDS])
    )
    rows, req_rows = [], []
    equipable_pages = sorted({b["page_name"] for b, _ in bonuses})
    pages = src.wikitexts(client, equipable_pages) if with_pages else {}
    reqs_by_page = {}
    for page_name, page in pages.items():
        found = equip_requirements(page["content"])
        reqs_by_page[page_name] = bool(found.skills)
        common = {
            "page": page_name,
            "provenance": "wiki_prose",
            "evidence": found.evidence,
            "source_url": src.page_url(page_name),
            "source_revid": page["revid"],
            "fetched_at": page["fetched_at"],
        }
        for skill, level in found.skills:
            req_rows.append({**common, "kind": "skill", "skill": skill, "level": level, "quest": None})
        for quest in found.quests:
            req_rows.append({**common, "kind": "quest", "skill": None, "level": None, "quest": quest})
    for b, resp in bonuses:
        item, _ = items.get(b["page_name_sub"], ({}, None))
        ids = item.get("item_id") or [None]
        status = ("found" if reqs_by_page.get(b["page_name"]) else "none_found") if with_pages else "not_checked"
        for item_id in ids:
            rows.append(
                {
                    "item_id": int(item_id) if item_id and str(item_id).isdigit() else None,
                    "name": item.get("item_name") or b["page_name_sub"],
                    "page": b["page_name"],
                    "members": _bool(item.get("is_members_only")),
                    "tradeable": _bool(item.get("tradeable")),
                    "slot": b.get("equipment_slot"),
                    **{col: b.get(field) for field, col in BONUS_FIELDS.items()},
                    "equip_req_status": status,
                    "source_url": src.page_url(b["page_name"]),
                    "fetched_at": resp.fetched_at,
                }
            )
    return {
        "items": _insert(con, "items", rows),
        "item_requirements": _insert(con, "item_requirements", req_rows),
        "equipable_pages": len(equipable_pages),
        "pages_with_requirement": sum(bool(v) for v in reqs_by_page.values()),
    }


def ingest_pure_builds(client: Client, con: duckdb.DuckDBPyConnection) -> dict:
    fields = [
        "name",
        "is_members_only",
        "type",
        "combat_level",
        "attack_level",
        "strength_level",
        "defence_level",
        "hitpoints",
        "ranged_level",
        "magic_level",
        "prayer_level",
    ]
    rows = []
    for r, resp in src.bucket_rows(client, "infobox_pure", fields):

        def lvl(key, r=r):
            return int(r[key]) if str(r.get(key, "")).isdigit() else None

        rows.append(
            {
                "name": r.get("name"),
                "members": _bool(r.get("is_members_only")),
                "types": r.get("type") or [],
                "combat_levels": r.get("combat_level") or [],
                "attack": lvl("attack_level"),
                "strength": lvl("strength_level"),
                "defence": lvl("defence_level"),
                "hitpoints": lvl("hitpoints"),
                "ranged": lvl("ranged_level"),
                "magic": lvl("magic_level"),
                "prayer": lvl("prayer_level"),
                "source_url": src.page_url(r.get("name") or ""),
                "fetched_at": resp.fetched_at,
            }
        )
    return {"pure_builds": _insert(con, "pure_builds", rows)}


def ingest_prices(client: Client, con: duckdb.DuckDBPyConnection) -> dict:
    mapping = src.price_mapping(client)
    latest = src.price_latest(client)
    data = latest.json()["data"]

    def ts(epoch):
        return datetime.fromtimestamp(epoch, UTC) if epoch else None

    rows = []
    for m in mapping.json():
        p = data.get(str(m["id"]), {})
        rows.append(
            {
                "item_id": m["id"],
                "name": m["name"],
                "members": _bool(m.get("members")),
                "buy_limit": m.get("limit"),
                "high_alch": m.get("highalch"),
                "high": p.get("high"),
                "high_time": ts(p.get("highTime")),
                "low": p.get("low"),
                "low_time": ts(p.get("lowTime")),
                "source_url": latest.url,
                "fetched_at": latest.fetched_at,
            }
        )
    return {"prices": _insert(con, "prices", rows), "priced": sum(r["high"] is not None for r in rows)}


def run(client: Client | None = None, with_item_pages: bool = True, progress: Callable[[str], None] = print) -> dict:
    client = client or Client()
    con = connect()
    report = {}
    with con:
        # rebuilt tables are derived from the raw cache: drop + recreate = idempotent and schema changes are free
        for table in REBUILT:
            con.execute(f"drop table if exists {table}")
        con.execute(DDL)
        for name, step in [
            ("quests", lambda: ingest_quests(client, con)),
            ("builds (infobox_pure)", lambda: ingest_pure_builds(client, con)),
            ("items", lambda: ingest_items(client, con, with_item_pages)),
            ("prices", lambda: ingest_prices(client, con)),
        ]:
            progress(f"ingesting {name}...")
            report[name] = step()
    con.close()
    return report


def player(rsn: str, mode: str, client: Client | None = None) -> tuple[list, str, datetime] | None:
    client = client or Client()
    resp = src.hiscores(client, rsn, mode)
    if resp.status == 404:
        return None
    stats = parse_hiscores(resp.json())
    con = connect()
    with con:
        _insert(
            con,
            "player_snapshots",
            [
                {
                    "rsn": rsn,
                    "mode": mode,
                    "skill": s.skill,
                    "level": s.level,
                    "xp": s.xp,
                    "rank": s.rank,
                    "source_url": resp.url,
                    "fetched_at": resp.fetched_at,
                }
                for s in stats
            ],
        )
    con.close()
    return stats, resp.url, resp.fetched_at


def dumps(report: dict) -> str:
    return json.dumps(report, indent=2, default=str)
