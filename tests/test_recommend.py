"""Phase 3: equip-requirement phrasings (real wiki sentences), activities parsing, prices, quest plan, gear knapsack."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from gp.normalize.activities import parse_money_method, parse_recipe
from gp.normalize.items import equip_requirements
from gp.progress import resolve
from gp.recommend.gear import Candidate, best_set
from gp.recommend.now import plan_quests
from gp.recommend.prices import clean
from gp.rules.builds import find_build
from gp.rules.guard import QuestFacts, judge_all

FRESH_XP = {"Attack": 0, "Strength": 0, "Defence": 0, "Hitpoints": 1154, "Ranged": 0, "Magic": 0, "Prayer": 0}


# ---------- equipment requirement phrasings, as written on the wiki (lead sentences, 2026-10-26) ----------
@pytest.mark.parametrize(
    ("sentence", "skills", "quests"),
    [
        (
            "The '''twisted bow''' is a rare reward from the [[Chambers of Xeric]] that requires a [[Ranged]] level of 85 to wield.",
            [("Ranged", 85)],
            [],
        ),
        (
            "The '''elder maul''' is a two-handed [[crush weapon]] that requires [[Attack]] and [[Strength]] levels of 75 to wield.",
            [("Attack", 75), ("Strength", 75)],
            [],
        ),
        (
            "'''Avernic treads''' are boots that require level 80 in [[Defence]], [[Strength]], [[Ranged]], and [[Magic]] to wear.",
            [("Defence", 80), ("Strength", 80), ("Ranged", 80), ("Magic", 80)],
            [],
        ),
        (
            "'''Zorya's tome''' is a reward from [[the Fractured Archive]], requiring 85 Magic to wield.",
            [("Magic", 85)],
            [],
        ),
        (
            "The '''ancient wyvern shield''' is a magical [[shield]] requiring a [[Magic]] level of 70 and a [[Defence]] level of "
            "at least 75 to equip, as well as having started [[Dragon Slayer I]].",
            [("Magic", 70), ("Defence", 75)],
            ["Dragon Slayer I"],
        ),
        (
            "The '''ring of suffering''' is an enchanted [[zenyte ring]], requiring a [[Hitpoints]] level of 75 to be equipped.",
            [("Hitpoints", 75)],
            [],
        ),
    ],
)
def test_equip_requirement_phrasings(sentence, skills, quests):
    reqs = equip_requirements(sentence)
    assert sorted(reqs.skills) == sorted(skills)
    assert reqs.quests == quests


def test_explicit_no_requirement():
    reqs = equip_requirements("The ring has no requirements to wear.")
    assert reqs.none_stated and reqs.skills == []


def test_same_requirements_as_another_item():
    s = (
        "They are a gold-plated version of [[rune platelegs]] and share the same bonuses and requirements to equip, "
        "however, this version cannot be made through the [[Smithing]] skill."
    )
    assert equip_requirements(s).same_as == "rune platelegs"


def test_crafting_sentence_is_not_an_equip_requirement():
    assert equip_requirements(" This requires level 93 [[Magic]] and grants 110 Magic experience.").skills == []


# ---------- activities ----------
def test_recipe_parsing():
    js = json.dumps(
        {
            "ticks": "3",
            "materials": [{"name": "Yew logs", "quantity": "1"}],
            "skills": [{"name": "Fletching", "level": "70", "experience": "75", "boostable": "Yes"}],
            "members": True,
            "output": {"name": "Yew longbow (u)", "quantity": "1"},
        }
    )
    (r,) = parse_recipe("Yew longbow (u)", js)
    assert (r.skill, r.level, r.xp, r.ticks, r.materials, r.output) == (
        "Fletching",
        70,
        75.0,
        3.0,
        [("Yew logs", 1.0)],
        ("Yew longbow (u)", 1.0),
    )


def test_money_method_parsing_with_level_like_70_plus():
    js = json.dumps(
        {
            "activity": "Test",
            "members": True,
            "skill": '* <span class="scp" data-skill="Runecraft" data-level="70+">70</span>',
            "prices": {"default_kph": 55, "kph_text": "Trips per hour"},
            "inputs": [{"name": "Pure essence", "qty": 54, "isph": False}],
            "outputs": [{"name": "Nature rune", "qty": "54", "isph": False}],
        }
    )
    m = parse_money_method("Money making guide/Test", js, False, "1000")
    assert (
        m.skill_reqs == [("Runecraft", 70)] and m.actions_per_hour == 55 and m.outputs == [("Nature rune", 54.0, False)]
    )


# ---------- prices (D12) ----------
NOW = datetime(2026, 10, 26, tzinfo=UTC)


def test_stale_and_crossed_prices_are_not_used():
    fresh, old = NOW - timedelta(hours=1), NOW - timedelta(days=5)
    assert clean(100, fresh, 90, fresh, NOW) == clean(100, fresh, 90, fresh, NOW)
    assert clean(100, old, 90, fresh, NOW).buy is None
    assert clean(30_000, fresh, 100_000, fresh, NOW).sell is None  # Moonlight mead(m), 26 Oct 2026
    assert clean(30_000, fresh, 100_000, fresh, NOW).buy == 30_000


# ---------- quest plan ----------
def test_plan_respects_done_prerequisites_levels_and_ranks_unlocks_first():
    quests = {
        "A": QuestFacts("A", True, True, rewards=[("fixed", ["Cooking"], 1000, 1)]),
        "B": QuestFacts("B", True, True, prerequisites=["A"], rewards=[("fixed", ["Cooking"], 5000, 1)]),
        "C": QuestFacts("C", True, True, skill_reqs=[("Agility", 50, False)]),
        "D": QuestFacts("D", True, True, rewards=[("fixed", ["Attack"], 10, 1)]),
        "Done": QuestFacts("Done", True, True),
    }
    b = find_build("1 def pure")
    verdicts = judge_all(quests, b, FRESH_XP)
    plan = plan_quests(
        quests,
        verdicts,
        build=b,
        levels={"Agility": 10},
        done={"Done"},
        qp={},
        unlocks_by_quest={"D": ["Dragon scimitar"]},
    )
    assert [o.quest for o in plan.available] == ["D", "A"]  # D unlocks gear, then A by useful XP
    assert ("B", ["A"]) in plan.waiting_on_prerequisites
    assert ("C", ["50 Agility"]) in plan.missing_levels


# ---------- gear knapsack ----------
def cand(name, slot, price, strength, attack=0):
    return Candidate(name, slot, price, strength, attack, True, "")


def test_knapsack_stays_in_budget_and_picks_best_combination():
    cands = [
        cand("Big 2h", "2h", 900, 90),
        cand("Sword", "weapon", 300, 40),
        cand("Defender", "shield", 300, 30),
        cand("Amulet", "neck", 200, 10),
        cand("Cheap amulet", "neck", 50, 5),
    ]
    best = best_set(cands, budget=1000, step=10)
    assert best.price <= 1000
    # 2h (90) + cheap amulet (5) would cost 950 -> 95; sword+defender+amulet costs 800 -> 80. Best = 95.
    assert {c.name for c in best.items} == {"Big 2h", "Cheap amulet"} and best.strength == 95


def test_two_handed_excludes_weapon_and_shield():
    cands = [cand("2h", "2h", 100, 50), cand("Sword", "weapon", 100, 30), cand("Shield", "shield", 100, 30)]
    best = best_set(cands, budget=1000, step=10)
    slots = [c.slot for c in best.items]
    assert slots in (["2h"], ["weapon", "shield"])
    assert best.strength == 60  # weapon + shield beats the 2h here


def test_quest_name_resolution_is_case_insensitive():
    assert resolve("dragon slayer i", {"Dragon Slayer I"}) == "Dragon Slayer I"
    assert resolve("nope", {"Dragon Slayer I"}) is None
