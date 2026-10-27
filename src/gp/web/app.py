"""Local web UI - server-rendered pages over the same store and rules as the CLI.

- Binds to 127.0.0.1 only (`gp ui`); there is no account system and nothing leaves the machine except the
  hiscores lookup the CLI also makes.
- One DuckDB connection per request, closed afterwards, so `gp ingest` can run between page loads.
- The only write is the completed-quests list; form posts from another origin are refused.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import duckdb
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gp import progress
from gp.fmt import gp_short
from gp.ingest.sources import HISCORE_BOARDS
from gp.store import connect
from gp.web import views

HERE = Path(__file__).parent
STATUS_LABEL = {
    "blocked": "blocked",
    "risk": "risk",
    "unknown": "unknown",
    "safe_if_choose": "safe if you choose",
    "fits": "fits",
    "safe": "safe",
}
TABS = [
    ("now", "Now", "Skills"),
    ("guard", "XP guard", "Combat"),
    ("gear", "Gear", "Worn equipment"),
    ("build", "Build", "Account"),
    ("compare", "Compare", "Equipment stats"),
    ("quests", "Quests", "Quest list"),
    ("data", "Data", "Collection log"),
]


# Opening/closing the same DuckDB file from several threads at once races ("Unique file handle conflict" - seen
# when a page loads its icons in parallel). Attach/detach is serialised; queries still run concurrently.
_ATTACH = threading.Lock()


def _db() -> Iterator[duckdb.DuckDBPyConnection]:
    with _ATTACH:
        con = connect()
    try:
        yield con
    finally:
        with _ATTACH:
            con.close()


def create_app() -> FastAPI:
    app = FastAPI(title="gielinor-planner", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    env = templates.env
    env.filters["gp"] = gp_short
    env.filters["num"] = lambda v: "?" if v is None else f"{v:,}"
    env.filters["wikiname"] = lambda s: (s or "").replace("[[", "").replace("]]", "")
    env.globals.update(STATUS_LABEL=STATUS_LABEL, TABS=TABS, MODES=list(HISCORE_BOARDS))

    def render(request: Request, con, template: str, tab: str, ctx: views.Context, **data) -> HTMLResponse:
        icons = {name for (name,) in con.execute("select name from icons").fetchall()}
        return templates.TemplateResponse(
            request,
            template,
            {"tab": tab, "ctx": ctx, "icons": icons, "fresh": views.freshness(con), **data},
        )

    def page(tab: str, template: str, build_data):
        """A tab that needs the side panel (account + build) before it can show anything."""

        def handler(request: Request, con: duckdb.DuckDBPyConnection = Depends(_db)) -> HTMLResponse:
            ctx = views.Context.from_query(request.query_params)
            if not views.store_ready(con):
                return render(request, con, "empty.html", tab, ctx)
            p = views.panel(con, ctx)
            data = build_data(con, p, request.query_params) if p.ok else {}
            return render(request, con, template, tab, ctx, p=p, **data)

        handler.__name__ = f"page_{tab}"
        app.get(f"/{tab}", response_class=HTMLResponse)(handler)
        return handler

    page("now", "now.html", lambda con, p, q: views.now_page(con, p))
    page("guard", "guard.html", lambda con, p, q: views.guard_page(con, p, q.get("show", "problems")))
    page(
        "gear",
        "gear.html",
        lambda con, p, q: views.gear_page(con, p, q.get("budget") or "5m", q.get("unverified") == "1"),
    )
    page("build", "build.html", lambda con, p, q: views.build_page(con, p))
    page("compare", "compare.html", lambda con, p, q: views.compare_page(con, p, q.getlist("c")))
    page("quests", "quests.html", lambda con, p, q: views.quests_page(con, p))
    page("data", "data.html", lambda con, p, q: views.data_page(con))

    @app.get("/")
    def root(request: Request) -> RedirectResponse:
        # no landing page: the planner opens on "what do I do now"
        return RedirectResponse(f"/now?{request.url.query}" if request.url.query else "/now")

    @app.post("/quests/done")
    async def save_done(request: Request, con: duckdb.DuckDBPyConnection = Depends(_db)) -> Response:
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.url.netloc:
            return Response("cross-origin form posts are not accepted", status_code=403)
        form = await request.form()
        known = {n for (n,) in con.execute("select name from quests").fetchall()}
        shown = {n for n in form.getlist("shown") if n in known}
        ticked = {n for n in form.getlist("done") if n in known}
        # only the rows that were on the page change; quests filtered out keep their state
        progress.save_done((progress.load_done() - shown) | ticked)
        back = form.get("back") or ""
        return RedirectResponse(f"/quests?{back}#saved", status_code=303)

    @app.get("/icon/{name}.png")
    def icon(name: str, con: duckdb.DuckDBPyConnection = Depends(_db)) -> Response:
        row = con.execute("select data, mime from icons where name = ?", [name]).fetchone()
        if not row:
            return Response(status_code=404)
        return Response(row[0], media_type=row[1], headers={"Cache-Control": "max-age=86400"})

    return app
