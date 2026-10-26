"""Equipment requirements from the item page's lead section (the wiki has no structured field - DECISIONS.md D3).

The wiki phrases them in many ways. Patterns covered (each with a real sentence in tests/test_parsers.py):
    "requiring level 40 [[Attack]] to wield"                       N [[Skill]]
    "requires a [[Ranged]] level of 85 to wield"                   [[Skill]] level of N
    "a [[Defence]] level of at least 75 to equip"                  [[Skill]] level of at least N
    "requires a [[Hitpoints]] of 75 to equip"                      [[Skill]] of N
    "requires [[Attack]] and [[Strength]] levels of 75 to wield"   [[A]] and [[B]] levels of N
    "require level 80 in [[Defence]], [[Strength]], ... to wear"   level N in [[A]], [[B]] and [[C]]
    "requiring 85 Magic to wield"                                  N Skill (unlinked)
    "has no requirements to wear"                                  explicitly no requirement
    "share the same bonuses and requirements to equip ... [[rune platelegs]]"  inherit from that item
A sentence qualifies only if it mentions wield/wear/equip; negations ("lack a Defence requirement") are skipped.
Quests named after "complet..." or "start..." in the same sentence are quest requirements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gp.normalize.quests import SKILLS

_SKILL_RE = "|".join(SKILLS)
_LINKED = r"\[\[(?:[^\]|]*\|)?(" + _SKILL_RE + r")\]\]"
EQUIP_VERB = re.compile(r"\b(?:wield|wear|equip)", re.I)
NEGATION = re.compile(r"\b(?:lack|without|not require|does not)\b[^.]{0,40}requirement", re.I)
NO_REQUIREMENT = re.compile(r"\bno (?:skill |level )?requirements?\b", re.I)
SAME_AS = re.compile(r"same[^.]*requirements?", re.I)

PATTERNS = [
    # N [[Skill]]
    (re.compile(r"(\d{1,2})\s*" + _LINKED, re.I), lambda m: [(m.group(2), m.group(1))]),
    # [[Skill]] (level) of (at least) N
    (
        re.compile(_LINKED + r"\s*(?:level\s*)?of\s*(?:at least\s*)?(\d{1,2})\b", re.I),
        lambda m: [(m.group(1), m.group(2))],
    ),
    # [[A]] and [[B]] levels of N
    (
        re.compile(_LINKED + r"\s*and\s*" + _LINKED + r"\s*levels?\s*of\s*(\d{1,2})\b", re.I),
        lambda m: [(m.group(1), m.group(3)), (m.group(2), m.group(3))],
    ),
    # N Skill (no link)
    (re.compile(r"(\d{1,2})\s+(" + _SKILL_RE + r")\b(?!\]\])", re.I), lambda m: [(m.group(2), m.group(1))]),
]
_LIST_SEP = r"(?:\s*,\s*(?:and\s+)?|\s+and\s+)?"  # "A, B", "A and B", "A, and B"
LEVEL_IN_LIST = re.compile(r"levels?\s*(\d{1,2})\s*in\s*(?:the\s*)?((?:" + _LINKED + _LIST_SEP + r")+)", re.I)
LINK = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
GENERIC_LINKS = {"quest", "quests", "miniquest", "quest series", "achievement diary", "achievement diaries"}


@dataclass
class EquipRequirements:
    skills: list[tuple[str, int]] = field(default_factory=list)
    quests: list[str] = field(default_factory=list)
    evidence: str = ""
    none_stated: bool = False  # the wiki says explicitly that there is no requirement
    same_as: str | None = None  # the wiki says the requirements are those of another item


def lead_section(wikitext: str) -> str:
    text = (wikitext or "").split("\n==", 1)[0]
    for _ in range(3):  # strip (nested) templates such as infoboxes
        text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    return re.sub(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>|'''|''", "", text, flags=re.S)


def _plain(sentence: str) -> str:
    return re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", sentence).strip()


def _skill_pairs(sentence: str) -> list[tuple[str, int]]:
    found: dict[str, int] = {}
    for m in LEVEL_IN_LIST.finditer(sentence):
        for skill in re.findall(_LINKED, m.group(2), re.I):
            found.setdefault(skill.capitalize(), int(m.group(1)))
    for pattern, extract in PATTERNS:
        for m in pattern.finditer(sentence):
            for skill, level in extract(m):
                found.setdefault(skill.capitalize(), int(level))
    return list(found.items())


def equip_requirements(wikitext: str) -> EquipRequirements:
    for sentence in re.split(r"(?<=[.!?])\s+", lead_section(wikitext)):
        if not EQUIP_VERB.search(sentence) or NEGATION.search(sentence):
            continue
        if NO_REQUIREMENT.search(sentence):
            return EquipRequirements(evidence=_plain(sentence), none_stated=True)
        links = [
            x.strip() for x in LINK.findall(sentence) if x.lower() not in GENERIC_LINKS and x.strip() not in SKILLS
        ]
        if SAME_AS.search(sentence) and links:
            return EquipRequirements(evidence=_plain(sentence), same_as=links[0])
        pairs = _skill_pairs(sentence)
        if not pairs:
            continue
        verb = re.search(r"\b(?:complet|start)\w*", sentence, re.I)
        after = sentence[verb.end() :] if verb else ""
        quests = [
            q.strip() for q in LINK.findall(after) if q.strip() not in SKILLS and q.strip().lower() not in GENERIC_LINKS
        ]
        return EquipRequirements(pairs, quests, _plain(sentence))
    return EquipRequirements()
