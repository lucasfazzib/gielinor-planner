"""Copy small, real responses from the raw cache into tests/fixtures (run after `gp ingest`).

    uv run python scripts/record_fixtures.py

Only public game data and a public account (Lynx Titan) - never the user's RSN.
"""

from __future__ import annotations

import json
from pathlib import Path

from gp.config import settings
from gp.http import Client
from gp.ingest import sources as src

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
QUESTS = [
    "Fairytale I - Growing Pains",
    "The Grand Tree",
    "Observatory Quest",
    "Fight Arena",
    "The Fremennik Isles",
    "Waterfall Quest",
    "Monkey Madness I",
]
ITEM_PAGES = ["Rune scimitar", "Dragon scimitar", "Berserker helm", "Bronze sword", "Barrows gloves"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = Client(offline=False)
    rows = [
        r
        for r, _ in src.bucket_rows(
            client, "quest", ["page_name", "official_difficulty", "official_length", "requirements"]
        )
        if r["page_name"] in QUESTS
    ]
    (OUT / "bucket_quest.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    pages = src.wikitexts(client, QUESTS + ITEM_PAGES)
    (OUT / "wikitext.json").write_text(
        json.dumps(
            {t: {"content": p["content"], "revid": p["revid"]} for t, p in pages.items()}, indent=1, ensure_ascii=False
        )
    )
    hs = src.hiscores(client, "Lynx Titan", "normal")
    (OUT / "hiscores_lynx_titan.json").write_text(json.dumps(hs.json(), indent=1))
    latest = src.price_latest(client).json()["data"]
    (OUT / "prices_latest_sample.json").write_text(
        json.dumps({"data": {k: latest[k] for k in ("1333", "4587")}}, indent=1)
    )
    print(f"fixtures written to {OUT} (data dir: {settings().data_dir})")


if __name__ == "__main__":
    main()
