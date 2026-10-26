"""gp - Gielinor planner CLI."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from gp import progress, service
from gp.config import settings
from gp.ingest import pipeline
from gp.ingest.sources import HISCORE_BOARDS
from gp.normalize.hiscores import levels
from gp.normalize.quests import SKILLS
from gp.recommend import compare as compare_mod
from gp.recommend import gear as gear_mod
from gp.recommend import now as now_mod
from gp.recommend.prices import price_book
from gp.rules import combat as combat_rules
from gp.rules.builds import BuildError, find_build, load_builds, violations
from gp.rules.guard import judge_all
from gp.store import connect

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Old School RuneScape account planner.")
console = Console()

STATUS_STYLE = {
    "blocked": "bold red",
    "risk": "red",
    "unknown": "yellow",
    "safe_if_choose": "cyan",
    "fits": "green",
    "safe": "dim green",
}


def _overrides(values: list[str] | None) -> dict[str, int]:
    out = {}
    for item in values or []:
        skill, _, level = item.partition("=")
        skill = skill.strip().capitalize()
        if skill not in SKILLS or not level.strip().isdigit():
            raise typer.BadParameter(f"use Skill=level, e.g. Strength=85 (got {item!r})")
        out[skill] = int(level)
    return out


def _account(rsn: str | None, mode: str, sets: list[str] | None) -> service.Account:
    try:
        return service.load_account(rsn, mode, _overrides(sets))
    except LookupError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc


@app.command()
def ingest(skip_item_pages: bool = typer.Option(False, help="Skip fetching item pages (equip requirements).")):
    """Download wiki data + GE prices into the local cache and rebuild the DuckDB store."""
    report = pipeline.run(with_item_pages=not skip_item_pages, progress=lambda m: console.print(f"[dim]{m}[/]"))
    table = Table("step", "result", title="ingest")
    for step, result in report.items():
        table.add_row(step, ", ".join(f"{k}={v}" for k, v in result.items()))
    console.print(table)


@app.command()
def stats(
    rsn: str = typer.Option(None, help="RuneScape name (default: GP_RSN from .env)."),
    mode: str = typer.Option("normal", help=f"Account mode: {', '.join(HISCORE_BOARDS)}."),
):
    """Current levels from the official hiscores."""
    rsn = rsn or settings().default_rsn
    if not rsn:
        raise typer.BadParameter("pass --rsn or set GP_RSN in .env")
    if mode not in HISCORE_BOARDS:
        raise typer.BadParameter(f"mode must be one of {list(HISCORE_BOARDS)}")
    result = pipeline.player(rsn, mode)
    if result is None:
        console.print(f"[red]{rsn!r} not found on the {mode} hiscores.[/] Wrong mode, or the name is not ranked.")
        raise typer.Exit(1)
    rows, url, fetched_at = result
    table = Table(
        "skill", "level", "xp", "rank", title=f"{rsn} ({mode})", caption=f"{url}  ·  {fetched_at:%Y-%m-%d %H:%M} UTC"
    )
    for s in rows:
        table.add_row(
            s.skill,
            str(s.level) if s.level is not None else "[yellow]unranked[/]",
            f"{s.xp:,}" if s.xp is not None else "-",
            f"{s.rank:,}" if s.rank is not None else "-",
        )
    console.print(table)
    unknown = [k for k, v in levels(rows).items() if v is None]
    if unknown:
        console.print(f"[yellow]Unranked (level unknown): {', '.join(unknown)}[/]")


@app.command()
def quest(name: str):
    """Requirements and XP rewards of a quest, with sources."""
    con = connect(read_only=True)
    q = con.execute("select * from quests where lower(name) = lower(?)", [name]).fetchone()
    if not q:
        console.print(f"[red]quest {name!r} not found[/] (run `gp ingest`?)")
        raise typer.Exit(1)
    cols = [d[0] for d in con.description]
    q = dict(zip(cols, q, strict=True))
    console.print(f"[bold]{q['name']}[/]  members={q['members']}  difficulty={q['difficulty']}  QP={q['quest_points']}")
    reqs = Table("kind", "requirement", "boostable", "needed to start", title="requirements")
    for kind, skill, level, boost, start, pre in con.execute(
        "select kind, skill, level, boostable, required_to_start, prerequisite_quest from quest_requirements "
        "where quest = ? order by kind, skill",
        [q["name"]],
    ).fetchall():
        reqs.add_row(
            kind, f"{level} {skill}" if kind == "skill" else (pre or f"{level} QP"), str(boost or ""), str(start)
        )
    console.print(reqs)
    rewards = Table("kind", "skills", "xp", "qty", title="XP rewards")
    for kind, skills, xp, qty in con.execute(
        "select kind, skills, xp, quantity from quest_xp_rewards where quest = ?", [q["name"]]
    ).fetchall():
        rewards.add_row(kind, ", ".join(skills), f"{xp:,}" if xp else "?", str(qty))
    console.print(rewards)
    console.print(
        f"[dim]source: {q['source_url']} (rev {q['source_revid']}, fetched {q['fetched_at']:%Y-%m-%d %H:%M})[/]"
    )


@app.command()
def combat(
    rsn: str = typer.Option(None, help="Read current levels from the hiscores."),
    mode: str = typer.Option("normal"),
    set_: list[str] = typer.Option(None, "--set", help="Override current levels: --set Prayer=43"),
    then: list[str] = typer.Option(None, "--then", help="Simulate: --then Strength=85 --then Attack=60"),
):
    """Combat level (official formula) and what a change would do to it."""
    acc = _account(rsn, mode, set_)
    now = combat_rules.combat(acc.levels)
    console.print(
        f"combat level [bold]{now.level}[/] ({float(now.exact):.3f}, {now.style})"
        + (f"  ·  {acc.rsn} ({acc.mode})" if acc.rsn else "  ·  fresh account")
    )
    if then:
        after_levels = {**acc.levels, **_overrides(then)}
        after = combat_rules.combat(after_levels)
        delta = after.level - now.level
        console.print(
            f"after {', '.join(then)}: [bold]{after.level}[/] ({float(after.exact):.3f}, {after.style})  "
            + (f"[red]+{delta} combat - you leave your bracket[/]" if delta else "[green]same combat level[/]")
        )
    nxt = combat_rules.levels_to_next_combat(acc.levels)
    table = Table("skill", "level", "levels for +1 combat", title="what raises your combat level")
    for skill in combat_rules.COMBAT_SKILLS:
        table.add_row(skill, str(acc.levels.get(skill) or "?"), str(nxt[skill]) if nxt[skill] else "-")
    console.print(table)
    console.print(f"[dim]formula: {combat_rules.SOURCE}[/]")


@app.command()
def builds():
    """Builds the planner knows (builds/*.yaml)."""
    table = Table("id", "names", "F2P", "limits", "source")
    for b in load_builds().values():
        limits = ", ".join(
            f"{s} <= {lim.max}" + ("" if lim.kind == "hard" else " (soft)") for s, lim in b.limits.items()
        )
        table.add_row(b.id, ", ".join(b.names[:2]), "yes" if b.f2p_possible else "no", limits or "-", b.source)
    console.print(table)


@app.command()
def build(name: str):
    """A build: limits (with the wiki's wording), the wiki's variants, key unlocks and their requirements."""
    try:
        b = find_build(name)
    except BuildError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print(f"[bold]{b.names[0]}[/] - {b.summary}\n[dim]{b.source}[/]")
    for s, lim in b.limits.items():
        console.print(f'  {s} <= {lim.max} [{lim.kind}]  [dim]"{lim.why}"[/]')
    variants = Table(
        "combat (wiki)",
        "computed",
        "Atk",
        "Str",
        "Def",
        "Pray",
        "Rng",
        "Mag",
        "HP",
        "members",
        title=f"variants on the wiki ({b.wiki_build})",
    )
    for v in service.build_variants(b):
        ok = v["combat_matches_wiki"]
        computed = f"[{'green' if ok else 'yellow' if ok is None else 'red'}]{v['computed_combat']}[/]"
        variants.add_row(
            "-".join(v["combat_levels"] or []),
            computed,
            *[str(v[k]) for k in ("attack", "strength", "defence", "prayer", "ranged", "magic", "hitpoints")],
            str(v["members"]),
        )
    console.print(variants)
    for item in b.key_unlocks:
        req = service.unlock_requirements(item)
        needs = ", ".join([f"{lv} {s}" for s, lv in req["skills"]] + req["quests"]) or "no requirement sentence found"
        console.print(f"  unlock [bold]{item}[/]: {needs}  [dim]{req['source_url'] or ''}[/]")


@app.command()
def guard(
    build_name: str = typer.Argument(..., metavar="BUILD"),
    rsn: str = typer.Option(None, help="Use this account's current XP (default: a fresh account)."),
    mode: str = typer.Option("normal"),
    set_: list[str] = typer.Option(None, "--set", help="Override levels: --set Defence=1"),
    f2p: bool = typer.Option(False, "--f2p", help="Only free-to-play quests."),
    show: str = typer.Option("problems", help="problems | all | safe"),
):
    """XP guard: quests that would break the build's limits, and why."""
    try:
        b = find_build(build_name)
    except BuildError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    acc = _account(rsn, mode, set_)
    if broken := violations(b, acc.levels):
        console.print(f"[bold red]This account already breaks the build:[/] {'; '.join(broken)}")
    verdicts = judge_all(service.quest_facts(), b, acc.xp, members=not f2p)
    wanted = {"problems": {"blocked", "risk", "unknown", "safe_if_choose"}, "safe": {"safe", "fits"}}.get(
        show, set(STATUS_STYLE)
    )
    table = Table(
        "status",
        "quest",
        "why",
        title=f"XP guard - {b.names[0]}"
        + (f" - {acc.rsn}" if acc.rsn else " - fresh account")
        + (" - F2P" if f2p else ""),
    )
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.status] = counts.get(v.status, 0) + 1
        if v.status in wanted:
            table.add_row(f"[{STATUS_STYLE[v.status]}]{v.status}[/]", v.quest, "; ".join(v.reasons) or "-")
    console.print(table)
    console.print("  ".join(f"[{STATUS_STYLE[s]}]{s}[/] {counts.get(s, 0)}" for s in STATUS_STYLE))
    console.print(
        "[dim]Fixed XP is exact; lamps count as safe only if you pick another skill; random XP and "
        "package choices are treated as the worst case (DECISIONS.md D4).[/]"
    )


def _find(build_name: str):
    try:
        return find_build(build_name)
    except BuildError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc


def _gp(value: float | None) -> str:
    if value is None:
        return "?"
    sign = "-" if value < 0 else ""
    v = abs(value)
    return f"{sign}{v / 1e6:.2f}M" if v >= 1e6 else f"{sign}{v / 1e3:.1f}k" if v >= 1e4 else f"{sign}{v:,.0f}"


def _parse_gp(text: str) -> int:
    t = text.strip().lower().replace(",", "")
    mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(t[-1:], 1)
    return int(float(t[:-1] if t[-1:] in "kmb" else t) * mult)


@app.command()
def done(
    action: str = typer.Argument("list", help="list | add | remove"),
    quests: list[str] = typer.Argument(None),
):
    """Quests you completed (kept locally in data/quests_done.txt - the hiscores do not expose them)."""
    con = connect(read_only=True)
    known = {n for (n,) in con.execute("select name from quests").fetchall()}
    names = progress.load_done()
    for raw in quests or []:
        name = progress.resolve(raw, known)
        if name is None:
            console.print(f'[red]unknown quest {raw!r}[/] (names as on the wiki, e.g. "Dragon Slayer I")')
            raise typer.Exit(1)
        if action == "add":
            names.add(name)
        elif action == "remove":
            names.discard(name)
    if action in ("add", "remove"):
        progress.save_done(names)
    console.print(f"{len(names)} quests marked done" + (": " + ", ".join(sorted(names)) if names else ""))


@app.command()
def now(
    build_name: str = typer.Argument(..., metavar="BUILD"),
    rsn: str = typer.Option(None, help="Account to plan for (default: a fresh account)."),
    mode: str = typer.Option("normal"),
    set_: list[str] = typer.Option(None, "--set", help="Override levels: --set Defence=1"),
    f2p: bool = typer.Option(False, "--f2p", help="Free-to-play only."),
):
    """What to do now: quests available and safe for the build, next milestones, training and money makers."""
    b = _find(build_name)
    acc = _account(rsn, mode, set_)
    members = not f2p
    con = connect(read_only=True)
    done_quests = progress.load_done()
    prices, _ = price_book(con)
    who = acc.rsn or ("levels from --set" if set_ else "fresh account")
    console.rule(f"[bold]{b.names[0]}[/] - {who} - {'F2P' if f2p else 'P2P'} - {len(done_quests)} quests marked done")
    if broken := violations(b, acc.levels):
        console.print(f"[bold red]Already breaks the build:[/] {'; '.join(broken)}")

    plan = now_mod.quest_plan(
        con, service.quest_facts(con), build=b, xp=acc.xp, levels=acc.levels, done=done_quests, members=members
    )
    table = Table("quest", "status", "QP", "useful XP", "unlocks gear", "notes", title="Quests you can do now")
    for o in plan.available[:15]:
        table.add_row(
            o.quest,
            f"[{STATUS_STYLE[o.status]}]{o.status}[/]",
            str(o.quest_points),
            f"{o.useful_xp:,}",
            ", ".join(o.unlocks[:3]) or "-",
            "; ".join(o.notes)[:70] or "-",
        )
    console.print(table)
    console.print(
        f"[dim]Ranked by gear unlocked, then XP in skills the build does not lock, then quest points. "
        f"{len(plan.waiting_on_prerequisites)} more are safe but wait on prerequisites; "
        f"{len(plan.missing_levels)} need higher levels.[/]"
    )

    skills = list(b.limits) + [
        s for s in ("Attack", "Strength", "Defence", "Ranged", "Magic", "Prayer") if s not in b.limits
    ]
    ms = Table(
        "skill", "level", "XP needed", "unlocks gear", "unlocks quests", title="Next milestones (within the build)"
    )
    for m in now_mod.milestones(con, build=b, levels=acc.levels, xp=acc.xp, skills=skills, members=members):
        ms.add_row(
            m.skill, str(m.level), f"{m.xp_needed:,}", ", ".join(m.items[:4]) or "-", ", ".join(m.quests[:3]) or "-"
        )
    console.print(ms)

    tr = Table("skill", "action", "lvl", "XP/action", "ticks", "XP/h ceiling", "gp/XP", title="Training at your levels")
    for skill in ("Cooking", "Fletching", "Crafting", "Smithing", "Herblore", "Firemaking"):
        for t in now_mod.training_options(con, prices, levels=acc.levels, skill=skill, members=members, top=3):
            gpxp = f"{t.gp_per_xp:+.1f}" + ("" if t.output_sold else " [dim](product unsold)[/]")
            tr.add_row(
                t.skill, t.action, str(t.level), f"{t.xp:g}", f"{t.ticks:g}", f"{t.xp_per_hour_ceiling:,.0f}", gpxp
            )
    console.print(tr)
    console.print(
        "[dim]Recipes you can do now with buyable materials, ranked by XP per action. XP/h ceiling = XP x 6,000 "
        f"ticks/h / ticks (1 tick = 0.6 s, {now_mod.GAME_TICK_SOURCE}) - excludes banking, real rates are lower. "
        "gp/XP: buy materials, sell product instantly at current GE prices.[/]"
    )

    mm = Table("activity", "category", "profit/h now", "wiki value", title="Money makers you qualify for")
    for m in now_mod.money_options(con, prices, levels=acc.levels, build=b, members=members):
        mm.add_row(
            m.activity.replace("[[", "").replace("]]", "")[:60],
            m.category or "-",
            _gp(m.profit_per_hour) if m.profit_per_hour is not None else f"? ({', '.join(m.unpriced[:2])})",
            _gp(m.wiki_value_per_hour),
        )
    console.print(mm)
    if b.limits:
        console.print(
            f"[yellow]Combat money makers train whatever attack style you use - keep it off {', '.join(b.limits)}.[/]"
        )


@app.command()
def gear(
    build_name: str = typer.Argument(..., metavar="BUILD"),
    budget: str = typer.Option("5m", help="e.g. 500k, 5m, 1.2b"),
    rsn: str = typer.Option(None),
    mode: str = typer.Option("normal"),
    set_: list[str] = typer.Option(None, "--set", help="Override levels: --set Attack=60"),
    f2p: bool = typer.Option(False, "--f2p"),
    allow_unverified: bool = typer.Option(
        False, help="Include items whose requirement could not be read from the wiki."
    ),
):
    """Best melee set for a budget: maximises strength bonus within the build's limits (knapsack over slots)."""
    b = _find(build_name)
    acc = _account(rsn, mode, set_)
    con = connect(read_only=True)
    _, by_id = price_book(con)
    cands, excluded = gear_mod.candidates(
        con,
        by_id,
        build=b,
        levels=acc.levels,
        done=progress.load_done(),
        members=not f2p,
        allow_unverified=allow_unverified,
    )
    best = gear_mod.best_set(cands, _parse_gp(budget))
    table = Table("slot", "item", "price", "str", "best atk", "req", title=f"Melee set for {budget} - {b.names[0]}")
    for c in best.items:
        table.add_row(c.slot, c.name, _gp(c.price), f"+{c.strength}", f"+{c.attack}", "verified" if c.verified else "?")
    table.add_row(
        "", "[bold]total[/]", f"[bold]{_gp(best.price)}[/]", f"[bold]+{best.strength}[/]", f"+{best.attack}", ""
    )
    console.print(table)
    console.print(
        "[dim]Objective: strength bonus (drives max hit: 0.5 + Effective Strength x (Strength Bonus + 64) / 640, "
        f"{gear_mod.SOURCE}); tie-break accuracy. Prices: instant-buy GE.[/]"
    )
    console.print(
        "[dim]Excluded: " + ", ".join(f"{n} {r}" for r, n in sorted(excluded.items(), key=lambda kv: -kv[1])) + "[/]"
    )


@app.command()
def compare(builds_: list[str] = typer.Argument(..., metavar="BUILD...")):
    """Compare builds side by side (facts and arithmetic only)."""
    con = connect(read_only=True)
    prices, _ = price_book(con)
    quests = service.quest_facts(con)
    summaries = [compare_mod.summarize(con, _find(n), quests, prices) for n in builds_]
    table = Table("", *[s.build.names[0] for s in summaries], title="Build comparison")

    def row(label, fn):
        table.add_row(label, *[fn(s) for s in summaries])

    row("members only", lambda s: "yes" if s.members_only else "no")
    row("limits", lambda s: ", ".join(f"{k} <= {v.max}" for k, v in s.build.limits.items()) or "-")
    row("combat (wiki variants)", lambda s: f"{s.combat_range[0]}-{s.combat_range[1]}" if s.combat_range else "-")
    row("play style (wiki)", lambda s: ", ".join(s.types) or "-")
    row("XP from fresh (lowest variant)", lambda s: f"{s.xp_to_lowest_variant:,}" if s.xp_to_lowest_variant else "-")
    row("XP from fresh (highest variant)", lambda s: f"{s.xp_to_highest_variant:,}" if s.xp_to_highest_variant else "-")
    row("quests blocked / risk", lambda s: f"{s.quests_blocked} / {s.quests_at_risk}")
    row("quests safe or choosable", lambda s: str(s.quests_safe))
    row("key unlocks", lambda s: "; ".join(f"{i} ({r}, {_gp(p)})" for i, r, p in s.unlocks) or "-")
    console.print(table)
    console.print(
        "[dim]No time estimates: combat XP/h depends on gear, monster and method and is not in a structured "
        "source. XP is the sum of combat-skill XP to reach the wiki's variant levels from a fresh account.[/]"
    )


@app.command()
def quality():
    """What the parsers could NOT read - review these instead of trusting silence."""
    con = connect(read_only=True)
    unparsed = Table("quest", "reward line (raw)", title="XP reward lines not classified (unparsed)")
    for quest_name, raw in con.execute(
        "select quest, raw_text from quest_xp_rewards where kind = 'unparsed' order by 1"
    ).fetchall():
        unparsed.add_row(quest_name, raw[:110])
    console.print(unparsed)
    (no_tpl,) = con.execute("select list(name order by name) from quests where not rewards_parsed").fetchone()
    console.print(f"[bold]Quests without a {{{{Quest rewards}}}} template[/] ({len(no_tpl)}): " + ", ".join(no_tpl))
    found, total = con.execute(
        "select count(distinct page) filter (where equip_req_status = 'found'), count(distinct page) from items "
        "where tradeable"
    ).fetchone()
    mismatches = Table(
        "build variant",
        "Atk/Str/Def/Pray/Rng/Mag/HP",
        "wiki says",
        "formula gives",
        title="Build variants whose stated combat level disagrees with the official formula",
    )
    for b in load_builds().values():
        for v in service.build_variants(b):
            if v["combat_matches_wiki"] is False:
                stats = "/".join(
                    str(v[k]) for k in ("attack", "strength", "defence", "prayer", "ranged", "magic", "hitpoints")
                )
                mismatches.add_row(v["name"], stats, "-".join(v["combat_levels"]), str(v["computed_combat"]))
    console.print(mismatches)
    console.print(
        f"[bold]Equip requirements[/]: found for {found:,} of {total:,} tradeable equipable items. "
        "The rest either have no requirement (e.g. bronze) or phrase it differently - treat as unknown."
    )


@app.command()
def db():
    """Row counts and data freshness per table."""
    con = connect(read_only=True)
    table = Table("table", "rows", "fetched (oldest -> newest)")
    for (name,) in con.execute("select table_name from duckdb_tables() order by 1").fetchall():
        n, lo, hi = con.execute(f"select count(*), min(fetched_at), max(fetched_at) from {name}").fetchone()
        table.add_row(name, f"{n:,}", f"{lo:%Y-%m-%d %H:%M} -> {hi:%Y-%m-%d %H:%M}" if lo else "-")
    console.print(table)
