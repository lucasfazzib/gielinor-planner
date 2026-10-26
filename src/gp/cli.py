"""gp - Gielinor planner CLI."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from gp.config import settings
from gp.ingest import pipeline
from gp.ingest.sources import HISCORE_BOARDS
from gp.normalize.hiscores import levels
from gp.store import connect

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Old School RuneScape account planner.")
console = Console()


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
