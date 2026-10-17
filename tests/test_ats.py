"""Fetch behaviour: real data or an error — never a silent empty list."""

import pytest
import requests

from src import ats
from src.ats import FetchError, _get, fetch_company


class Resp:
    def __init__(self, status=200, payload=None, headers=None, bad_json=False):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def fake_http(monkeypatch):
    """Install a scripted sequence of responses; the last one repeats."""
    calls, sleeps = [], []

    def install(*responses):
        queue = list(responses)

        def get(url, **_):
            calls.append(url)
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(ats.requests, "get", get)
        monkeypatch.setattr(ats.time, "sleep", sleeps.append)
        return calls, sleeps

    return install


def test_success_returns_json(fake_http):
    calls, sleeps = fake_http(Resp(200, {"ok": True}))
    assert _get("u") == {"ok": True}
    assert len(calls) == 1 and sleeps == []


def test_transient_errors_are_retried_with_backoff(fake_http):
    calls, sleeps = fake_http(requests.Timeout("slow"), Resp(503), Resp(200, {"ok": True}))
    assert _get("u") == {"ok": True}
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]


def test_gives_up_after_max_attempts(fake_http):
    calls, sleeps = fake_http(Resp(503))
    with pytest.raises(FetchError, match="after 4 attempts"):
        _get("u")
    assert len(calls) == ats.MAX_ATTEMPTS
    assert sleeps == [2.0, 4.0, 8.0]


def test_permanent_errors_fail_immediately(fake_http):
    calls, sleeps = fake_http(Resp(404))
    with pytest.raises(FetchError, match="HTTP 404"):
        _get("u")
    assert len(calls) == 1 and sleeps == []


def test_retry_after_header_is_respected(fake_http):
    _, sleeps = fake_http(Resp(429, headers={"Retry-After": "7"}), Resp(200, {}))
    _get("u")
    assert sleeps == [7]


def test_invalid_json_is_retried(fake_http):
    _, sleeps = fake_http(Resp(200, bad_json=True), Resp(200, {"x": 1}))
    assert _get("u") == {"x": 1}
    assert sleeps == [2.0]


def test_empty_board_is_not_an_error(fake_http):
    fake_http(Resp(200, {"jobs": []}))
    assert fetch_company("Acme", "ashby", "acme") == []


def test_unexpected_payload_raises(fake_http):
    fake_http(Resp(200, []))  # Greenhouse should return a dict
    with pytest.raises(FetchError, match="unexpected Greenhouse payload"):
        fetch_company("Acme", "greenhouse", "acme")


def test_unknown_ats_raises():
    with pytest.raises(FetchError, match="unknown ATS"):
        fetch_company("Acme", "workday", "acme")


def test_ashby_roles_are_normalized(fake_http):
    fake_http(Resp(200, {"jobs": [{
        "id": "abc", "title": "Data Engineer", "department": "Engineering", "team": "Data",
        "location": "Remote", "isRemote": True, "publishedAt": "2026-10-01T12:00:00Z",
        "jobUrl": "https://jobs.ashbyhq.com/acme/abc",
    }]}))
    [role] = fetch_company("Acme", "ashby", "acme", captured_at="2026-10-17")
    assert role["role_id"] == "abc"
    assert role["company"] == "Acme" and role["ats"] == "ashby"
    assert role["published_at"] == "2026-10-01" and role["captured_at"] == "2026-10-17"
    assert role["is_remote"] is True
