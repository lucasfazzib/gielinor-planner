"""Quest parsing: requirements (Bucket 'quest', rendered HTML) and rewards ({{Quest rewards}} wikitext).

Pure functions over recorded inputs - tested in tests/test_quests.py with real responses.

Requirements markup (bucket field `requirements`): skill requirements are rendered spans
    <span class="scp" data-skill="Agility" data-level="25"> ... </span> [boostable] [not required to start]
and quest prerequisites are a nested wiki list under "Completion of the following quests" where
depth 2 (`**`) are direct prerequisites and deeper levels are their own prerequisites.

Reward markup (page wikitext): {{Quest rewards |qp = 2 |rewards = *{{SCP|Farming|3,500}} [[Farming]] experience ...}}
Lamps / choosable XP and random XP are classified separately (see DECISIONS.md, "XP that the player chooses").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

SKILLS = [
    "Attack",
    "Defence",
    "Strength",
    "Hitpoints",
    "Ranged",
    "Prayer",
    "Magic",
    "Cooking",
    "Woodcutting",
    "Fletching",
    "Fishing",
    "Firemaking",
    "Crafting",
    "Smithing",
    "Mining",
    "Herblore",
    "Agility",
    "Thieving",
    "Slayer",
    "Farming",
    "Runecraft",
    "Hunter",
    "Construction",
    "Sailing",
]
COMBAT_SKILLS = ["Attack", "Strength", "Defence", "Hitpoints", "Ranged", "Magic", "Prayer"]
_SKILL_RE = "|".join(SKILLS)


@dataclass
class SkillReq:
    skill: str
    level: int
    boostable: bool
    required_to_start: bool


@dataclass
class Requirements:
    skills: list[SkillReq] = field(default_factory=list)
    quests: list[str] = field(default_factory=list)  # direct prerequisites only
    quest_points: int | None = None
    raw_text: str = ""


def parse_requirements(markup: str) -> Requirements:
    req = Requirements(raw_text=markup or "")
    if not markup:
        return req
    # one requirement per wiki list line: the flags ([boostable], [not required to start]) are on the same line
    for line in markup.splitlines():
        line_soup = BeautifulSoup(line, "html.parser")
        spans = line_soup.select("span.scp[data-skill][data-level]")
        if not spans:
            continue
        flags = line_soup.get_text(" ", strip=True).lower()
        for span in spans:
            skill = span["data-skill"].strip().capitalize()
            if skill not in SKILLS:
                continue
            req.skills.append(
                SkillReq(
                    skill=skill,
                    level=int(span["data-level"]),
                    boostable="boostable" in flags and "not boostable" not in flags,
                    required_to_start="not require" not in flags and "can be started without" not in flags,
                )
            )

    text = BeautifulSoup(markup, "html.parser").get_text("")  # wiki list markup survives as plain text
    in_quests = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^\*\s*completion of", stripped, re.I):
            in_quests = True
            continue
        depth = len(stripped) - len(stripped.lstrip("*"))
        if in_quests and depth == 2:
            if m := re.search(r"\[\[([^\]|#]+)", stripped):
                req.quests.append(m.group(1).strip())
        elif depth <= 1 and in_quests:
            in_quests = False
        if m := re.search(r"(\d+)\s*\[\[Quest points?", stripped, re.I):
            req.quest_points = int(m.group(1))
    return req


# ---------- rewards ----------
@dataclass
class XpReward:
    kind: str  # fixed | choice | choice_bundle | random | unparsed
    skills: list[str]  # one skill for fixed; options for choice/random ('Any' = player's choice of any skill)
    xp: int | None
    quantity: int
    raw_text: str


@dataclass
class Rewards:
    quest_points: int | None
    xp: list[XpReward]
    found_template: bool


def _template_block(wikitext: str, name: str) -> str | None:
    start = wikitext.find("{{" + name)
    if start < 0:
        return None
    depth, i = 0, start
    while i < len(wikitext) - 1:
        pair = wikitext[i : i + 2]
        if pair == "{{":
            depth += 1
            i += 2
        elif pair == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return wikitext[start:i]
        else:
            i += 1
    return None


def _num(text: str) -> int:
    return int(text.replace(",", "").strip())


SCP_XP = re.compile(r"\{\{SCP\|(" + _SKILL_RE + r")\|([\d,]+)[^}]*\}\}", re.I)
SCP_ANY = re.compile(r"\{\{SCP\|(" + _SKILL_RE + r")[|}]", re.I)
SKILL_LINK = re.compile(r"\[\[(" + _SKILL_RE + r")(?:\|[^\]]*)?\]\]", re.I)
CHOICE_WORDS = re.compile(
    r"lamp|choos|choice|of the player's|of your choice|any skill|book of knowledge|tome|"
    r"skill of the player|combat skill|any \[\[combat|other than",
    re.I,
)
RANDOM_WORDS = re.compile(r"random|depend(?:s|ing) on|one of the following", re.I)
XP_WORDS = re.compile(r"experience|\bxp\b|lamp|tome", re.I)
NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _depth(line: str) -> int:
    return len(line) - len(line.lstrip("*"))


def _skills_in(line: str) -> list[str]:
    found = {s.capitalize() for s in SKILL_LINK.findall(line)} | {s.capitalize() for s in SCP_ANY.findall(line)}
    return sorted(found)


def _amounts(line: str) -> list[int]:
    """XP amounts: numbers >= 100 (with thousands separators) that are not the item quantity."""
    return [_num(n) for n in re.findall(r"(?<![\w,.])(\d{1,3}(?:,\d{3})+|\d{3,})(?![\w,])", line)]


def _quantity(line: str) -> int:
    # the wiki writes quantities as "2 x 7,000" or with the multiplication sign
    if m := re.search(r"(\d+)\s*[×x]\s", line):  # noqa: RUF001
        return int(m.group(1))
    if m := re.search(r"^\**\s*(an?|one|two|three|four|five|six|seven|eight|nine|ten)\b", line.strip(), re.I):
        return NUMBER_WORDS[m.group(1).lower()]
    return 1


def parse_rewards(wikitext: str) -> Rewards:
    block = _template_block(wikitext or "", "Quest rewards")
    if block is None:
        return Rewards(None, [], found_template=False)
    qp = re.search(r"\|\s*qp\s*=\s*(\d+)", block)
    # the first reward can sit on the same line as the parameter: "|rewards = * {{SCP|Strength|13,750}} ..."
    body = re.sub(r"\|\s*rewards\s*=\s*", "\n", block)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("*")]

    xp: list[XpReward] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _depth(line) != 1:  # sub-bullets are notes/details of the parent line
            i += 1
            continue
        children = []
        while i + 1 < len(lines) and _depth(lines[i + 1]) > 1:
            children.append(lines[i + 1])
            i += 1
        i += 1
        if RANDOM_WORDS.search(line):
            # e.g. Observatory Quest: "*Your item reward depends on ..." with "**Virgo: 875 {{SCP|Defence}} ..."
            xp_children = [c for c in children if XP_WORDS.search(c)]
            if xp_children or XP_WORDS.search(line):
                skills = sorted({s for c in [line, *xp_children] for s in _skills_in(c)})
                amounts = [a for c in xp_children for a in _amounts(c)] or _amounts(line)
                xp.append(
                    XpReward(
                        "random",
                        skills or ["Any"],
                        max(amounts) if amounts else None,
                        1,
                        "\n".join([line, *xp_children]),
                    )
                )
            continue
        if not XP_WORDS.search(line):
            continue
        fixed = SCP_XP.findall(line)
        if CHOICE_WORDS.search(line) and not fixed and re.search(r"'''OR'''|\bOR\b", line):
            # a choice between PACKAGES (e.g. Monkey Madness I: 35k Atk+Def & 20k Str+HP, OR the reverse):
            # every package can include the same skill, so this is NOT "safe if you pick X" - worst case applies
            amounts = _amounts(line)
            xp.append(XpReward("choice_bundle", _skills_in(line), max(amounts) if amounts else None, 1, line))
        elif CHOICE_WORDS.search(line) and not fixed:
            amounts = _amounts(line)
            xp.append(
                XpReward("choice", _skills_in(line) or ["Any"], amounts[0] if amounts else None, _quantity(line), line)
            )
        elif fixed:
            for skill, amount in fixed:
                xp.append(XpReward("fixed", [skill.capitalize()], _num(amount), 1, line))
        else:
            amounts = _amounts(line)
            xp.append(XpReward("unparsed", _skills_in(line), amounts[0] if amounts else None, _quantity(line), line))
    return Rewards(int(qp.group(1)) if qp else None, xp, found_template=True)
