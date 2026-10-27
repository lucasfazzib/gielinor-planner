"""Fetchers: one function per endpoint. They only download (into the raw cache); parsing lives in gp.normalize.

Freshness (TTL) per source:
  wiki (buckets, page wikitext) .... 7 days   (game data changes weekly; `--refresh` forces it)
  prices /mapping .................. 1 day
  prices /latest ................... 5 minutes (the API updates every minute; we never call per item)
  hiscores ......................... 10 minutes
"""

from __future__ import annotations

from collections.abc import Iterator

from gp.http import Cached, Client

WIKI_API = "https://oldschool.runescape.wiki/api.php"
WIKI_PAGE = "https://oldschool.runescape.wiki/w/"
PRICES = "https://prices.runescape.wiki/api/v1/osrs"
DAY = 86400
WIKI_TTL = 7 * DAY
BUCKET_PAGE = 5000  # server-side maximum per query (verified in phase 0)

# Hiscores endpoint per account mode (index_lite.json). A player missing from a mode's board returns 404.
HISCORE_BOARDS = {
    "normal": "hiscore_oldschool",
    "ironman": "hiscore_oldschool_ironman",
    "hardcore": "hiscore_oldschool_hardcore_ironman",
    "ultimate": "hiscore_oldschool_ultimate",
}


def page_url(title: str) -> str:
    return WIKI_PAGE + title.replace(" ", "_")


# ---------- OSRS Wiki: Bucket (structured data) ----------
def bucket_rows(client: Client, bucket: str, fields: list[str], ttl: float = WIKI_TTL) -> Iterator[tuple[dict, Cached]]:
    """All rows of a bucket, paginated. Yields (row, response) so callers keep provenance."""
    offset = 0
    select = ",".join(f"'{f}'" for f in fields)
    while True:
        query = f"bucket('{bucket}').select({select}).limit({BUCKET_PAGE}).offset({offset}).run()"
        # formatversion=2: real JSON booleans (v1 encodes them MediaWiki-style and they would read as unknown)
        resp = client.get(
            "wiki_bucket",
            WIKI_API,
            {"action": "bucket", "query": query, "format": "json", "formatversion": "2"},
            ttl=ttl,
        )
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"bucket {bucket}: {payload['error']}")
        rows = payload.get("bucket", [])
        for row in rows:
            yield row, resp
        if len(rows) < BUCKET_PAGE:
            return
        offset += BUCKET_PAGE


# ---------- OSRS Wiki: page wikitext (templates the buckets do not cover) ----------
def wikitexts(client: Client, titles: list[str], ttl: float = WIKI_TTL) -> dict[str, dict]:
    """{title: {content, revid, timestamp, fetched_at}} - 50 titles per request (API limit for revisions)."""
    out: dict[str, dict] = {}
    for i in range(0, len(titles), 50):
        chunk = sorted(titles[i : i + 50])
        resp = client.get(
            "wiki_pages",
            WIKI_API,
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content|ids|timestamp",
                "rvslots": "main",
                "titles": "|".join(chunk),
                "redirects": "1",
                "format": "json",
                "formatversion": "2",
            },
            ttl=ttl,
        )
        data = resp.json()["query"]
        # map normalized/redirected titles back to what we asked for
        alias = {n["to"]: n["from"] for n in data.get("normalized", []) + data.get("redirects", [])}
        for page in data.get("pages", []):
            if page.get("missing") or not page.get("revisions"):
                continue
            rev = page["revisions"][0]
            requested = alias.get(page["title"], page["title"])
            out[requested] = {
                "title": page["title"],
                "content": rev["slots"]["main"]["content"],
                "revid": rev["revid"],
                "timestamp": rev["timestamp"],
                "fetched_at": resp.fetched_at,
            }
    return out


# ---------- OSRS Wiki: images (icons for the UI) ----------
def wiki_file_urls(client: Client, files: list[str], ttl: float = WIKI_TTL) -> dict[str, tuple[str, str]]:
    """{file name: (image url, file page url)} via imageinfo - 50 files per request. Missing files are left out."""
    out: dict[str, tuple[str, str]] = {}
    for i in range(0, len(files), 50):
        chunk = sorted(files[i : i + 50])
        resp = client.get(
            "wiki_files",
            WIKI_API,
            {
                "action": "query",
                "prop": "imageinfo",
                "iiprop": "url",
                "titles": "|".join(f"File:{f}" for f in chunk),
                "format": "json",
                "formatversion": "2",
            },
            ttl=ttl,
        )
        for page in resp.json()["query"]["pages"]:
            info = page.get("imageinfo")
            if not page.get("missing") and info:
                out[page["title"].removeprefix("File:")] = (info[0]["url"], info[0]["descriptionurl"])
    return out


def wiki_image(client: Client, url: str, ttl: float = 30 * DAY) -> Cached:
    return client.get("wiki_images", url, ttl=ttl, binary=True)


# ---------- Real-time prices API ----------
def price_mapping(client: Client) -> Cached:
    return client.get("prices", f"{PRICES}/mapping", ttl=DAY)


def price_latest(client: Client) -> Cached:
    return client.get("prices", f"{PRICES}/latest", ttl=300)


def price_1h(client: Client) -> Cached:
    """Average price and traded volume per side over the last hour (liquidity check, DECISIONS.md D12)."""
    return client.get("prices", f"{PRICES}/1h", ttl=300)


# ---------- Official hiscores ----------
def hiscores(client: Client, rsn: str, mode: str = "normal") -> Cached:
    board = HISCORE_BOARDS[mode]
    return client.get(
        "hiscores", f"https://secure.runescape.com/m={board}/index_lite.json", {"player": rsn}, ttl=600, allow_404=True
    )
