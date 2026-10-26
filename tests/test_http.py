import json

import httpx
import pytest

from gp.http import Client, OfflineMiss


def make(tmp_path, handler, **kw):
    return Client(raw_dir=tmp_path, user_agent="test/1.0", min_interval=0, transport=httpx.MockTransport(handler), **kw)


def test_response_is_cached_with_fetch_time(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.headers["User-Agent"] == "test/1.0"
        return httpx.Response(200, json={"ok": 1})

    client = make(tmp_path, handler)
    first = client.get("x", "https://example.org/a", {"q": "1"})
    second = client.get("x", "https://example.org/a", {"q": "1"})
    assert len(calls) == 1 and not first.from_cache and second.from_cache
    stored = json.loads(next((tmp_path / "x").iterdir()).read_text())
    assert stored["url"] == "https://example.org/a?q=1" and "fetched_at" in stored


def test_offline_serves_stale_cache_and_fails_on_miss(tmp_path):
    make(tmp_path, lambda r: httpx.Response(200, text="v1")).get("x", "https://example.org/a")
    offline = make(tmp_path, lambda r: pytest.fail("network used offline"), offline=True)
    assert offline.get("x", "https://example.org/a", ttl=0).body == "v1"
    with pytest.raises(OfflineMiss):
        offline.get("x", "https://example.org/other")


def test_429_retries(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    answers = iter([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, text="ok")])
    assert make(tmp_path, lambda r: next(answers)).get("x", "https://example.org/a").body == "ok"


def test_404_allowed_for_hiscores(tmp_path):
    resp = make(tmp_path, lambda r: httpx.Response(404, text="")).get("x", "https://example.org/a", allow_404=True)
    assert resp.status == 404
