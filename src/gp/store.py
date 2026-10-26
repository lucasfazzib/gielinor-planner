"""DuckDB store. Every game-data row carries source_url, source_revid (wiki) and fetched_at.

Tables are rebuilt from the raw cache on each `gp ingest` (wiki data is a snapshot of the current wiki);
player snapshots and price snapshots are appended (history).
"""

from __future__ import annotations

import duckdb

from gp.config import settings

DDL = """
create table if not exists quests (
    name varchar primary key, members boolean, difficulty varchar, length varchar, quest_points integer,
    rewards_parsed boolean, source_url varchar, source_revid bigint, fetched_at timestamptz
);
create table if not exists quest_requirements (
    quest varchar, kind varchar, skill varchar, level integer, boostable boolean, required_to_start boolean,
    prerequisite_quest varchar, raw_text varchar, source_url varchar, fetched_at timestamptz
);
create table if not exists quest_xp_rewards (
    quest varchar, kind varchar, skills varchar[], xp integer, quantity integer, raw_text varchar,
    source_url varchar, source_revid bigint, fetched_at timestamptz
);
create table if not exists items (
    item_id integer, name varchar, page varchar, members boolean, tradeable boolean, slot varchar,
    stab_att integer, slash_att integer, crush_att integer, range_att integer, magic_att integer,
    stab_def integer, slash_def integer, crush_def integer, range_def integer, magic_def integer,
    strength integer, ranged_strength integer, magic_damage double, prayer integer, attack_speed integer,
    equip_req_status varchar, source_url varchar, fetched_at timestamptz
);
create table if not exists item_requirements (
    page varchar, kind varchar, skill varchar, level integer, quest varchar, provenance varchar, evidence varchar,
    source_url varchar, source_revid bigint, fetched_at timestamptz
);
create table if not exists pure_builds (
    name varchar, members boolean, types varchar[], combat_levels varchar[], attack integer, strength integer,
    defence integer, hitpoints integer, ranged integer, magic integer, prayer integer,
    source_url varchar, fetched_at timestamptz
);
create table if not exists prices (
    -- bigint: some items trade above 2^31 gp (seen: 2,294,999,989)
    item_id integer, name varchar, members boolean, buy_limit integer, high_alch bigint,
    high bigint, high_time timestamptz, low bigint, low_time timestamptz,
    source_url varchar, fetched_at timestamptz
);
create table if not exists player_snapshots (
    rsn varchar, mode varchar, skill varchar, level integer, xp bigint, rank integer,
    source_url varchar, fetched_at timestamptz
);
"""

REBUILT = ["quests", "quest_requirements", "quest_xp_rewards", "items", "item_requirements", "pure_builds", "prices"]


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        con.execute(DDL)
    return con
