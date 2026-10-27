"""UI tests against a tiny store built in a temp dir - no network, no ingest.

The game rows below are a hand-made SYNTHETIC fixture to exercise the pages; the only real values copied in are
Dragon Slayer I's 18,650 Defence XP (wiki, phase 1) so the XP guard has something real to block.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from gp import config, progress
from gp.ingest import pipeline
from gp.store import connect
from gp.web import views
from gp.web.app import create_app

NOW = datetime.now(UTC)
WIKI = "https://oldschool.runescape.wiki/w/"
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GP_DATA_DIR", str(tmp_path))
    config.settings.cache_clear()
    yield tmp_path
    config.settings.cache_clear()


def _seed():
    con = connect()
    con.executemany(
        "insert into quests values (?, ?, ?, ?, ?, true, ?, 1, ?)",
        [
            ("Cook's Assistant", False, "Novice", "Very short", 1, WIKI + "Cook's_Assistant", NOW),
            ("Dragon Slayer I", False, "Experienced", "Long", 2, WIKI + "Dragon_Slayer_I", NOW),
        ],
    )
    con.executemany(
        "insert into quest_xp_rewards values (?, 'fixed', ?, ?, 1, '', ?, 1, ?)",
        [
            ("Cook's Assistant", ["Cooking"], 300, WIKI + "Cook's_Assistant", NOW),
            ("Dragon Slayer I", ["Defence"], 18650, WIKI + "Dragon_Slayer_I", NOW),
        ],
    )
    con.execute(
        """insert into items (item_id, name, page, members, tradeable, slot, stab_att, slash_att, crush_att,
                              strength, equip_req_status, source_url, fetched_at)
           values (1, 'Test sword', 'Test sword', false, true, 'weapon', 5, 4, -2, 6, 'no_requirement', ?, ?)""",
        [WIKI + "Test_sword", NOW],
    )
    con.execute(
        """insert into prices (item_id, name, members, high, high_time, low, low_time, volume_low_1h, source_url,
                               fetched_at)
           values (1, 'Test sword', false, 1000, ?, 900, ?, 10, 'https://prices.runescape.wiki/api/v1/osrs/latest', ?)""",
        [NOW, NOW, NOW],
    )
    con.execute("insert into icons values ('Attack', 'Attack icon.png', 'image/png', ?, ?, ?)", [PNG_1x1, WIKI, NOW])
    con.close()


@pytest.fixture
def client(data_dir):
    _seed()
    return TestClient(create_app())


def test_empty_store_explains_the_first_ingest(data_dir):
    page = TestClient(create_app()).get("/now")
    assert page.status_code == 200
    assert "uv run gp ingest" in page.text


def test_root_opens_the_planner_not_a_landing_page(client):
    r = client.get("/?build=zerker", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/now?build=zerker"


@pytest.mark.parametrize("tab", ["now", "guard", "gear", "build", "compare", "quests", "data"])
def test_every_tab_renders_for_a_fresh_account(client, tab):
    r = client.get(f"/{tab}?build=1+def+pure")
    assert r.status_code == 200, r.text[:500]
    assert "Fresh account" in r.text


def test_guard_blocks_dragon_slayer_for_a_one_defence_pure(client):
    r = client.get("/guard?build=one-defence-pure&show=problems")
    row = r.text.split("Dragon Slayer I")[0].rsplit("<tr>", 1)[-1]
    assert "st-blocked" in row


def test_gear_picks_the_only_item_and_rejects_a_bad_budget(client):
    assert "Test sword" in client.get("/gear?build=main&budget=5k").text
    assert "not understood" in client.get("/gear?build=main&budget=lots").text


def test_bad_build_and_bad_override_are_explained(client):
    assert "alert" in client.get("/now?build=nonsense").text
    page = client.get("/now?set=Defense%3D1").text  # misspelt skill
    assert "could not read" in page


def test_unknown_player_shows_an_error_not_a_fresh_account(client, monkeypatch):
    monkeypatch.setattr(pipeline, "player", lambda *a, **k: None)
    page = client.get("/now?rsn=NoSuchName").text
    assert "is not on the normal hiscores" in page
    assert "Fresh account" not in page


def test_marking_quests_done_and_refusing_cross_origin_posts(client, data_dir):
    form = {"shown": ["Cook's Assistant", "Dragon Slayer I"], "done": ["Cook's Assistant"], "back": "build=main"}
    r = client.post("/quests/done", data=form, follow_redirects=False)
    assert r.status_code == 303
    assert progress.load_done() == {"Cook's Assistant"}
    evil = client.post("/quests/done", data={"shown": ["Cook's Assistant"]}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    assert progress.load_done() == {"Cook's Assistant"}


def test_icons_are_served_from_the_store(client):
    assert client.get("/icon/Attack.png").content == PNG_1x1
    assert client.get("/icon/Nope.png").status_code == 404


def test_parallel_requests_do_not_race_on_the_database_file(client):
    # a page loads ~30 icons at once; each request opens its own connection
    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(lambda _: client.get("/icon/Attack.png").status_code, range(64)))
    assert set(codes) == {200}


def test_parse_overrides():
    assert views.parse_overrides("Defence=1, prayer 43") == ({"Defence": 1, "Prayer": 43}, [])
    _, errors = views.parse_overrides("Defence=100; Foo=3")
    assert len(errors) == 2


def test_context_query_round_trip():
    ctx = views.Context.from_query({"rsn": "Lynx Titan", "mode": "ironman", "build": "zerker", "f2p": "1"})
    assert ctx.query(c=["a", "b"]) == "rsn=Lynx+Titan&mode=ironman&build=zerker&f2p=1&c=a&c=b"
