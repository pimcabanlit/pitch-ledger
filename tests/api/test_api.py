"""/api/v1: public fan endpoints, producer endpoints behind Entra ID, decisions."""

import asyncio
import time
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from pitch_ledger.agents.replay import ReplayOptions, replay_match
from pitch_ledger.api.app import RateLimiter, create_app
from pitch_ledger.api.auth import EntraVerifier
from pitch_ledger.api.repository import MatchRepository
from pitch_ledger.api.settings import ApiSettings
from pitch_ledger.cli import main
from pitch_ledger.infra_adapters.ledger_queue import LedgerQueueSettings
from pitch_ledger.ledger import FileLedger
from pitch_ledger.ops import OpsDesk
from pitch_ledger.push import NullNotifier, RecordingNotifier

TENANT, AUDIENCE = "00000000-0000-0000-0000-00000000000a", "api://pitch-ledger"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A planted-collapse match replayed offline without approval: one pending insight."""
    match_dir = tmp_path / "M0002"
    assert main(["generate", "--scenario", "late_collapse", "--seed", "4212",
                 "--out", str(match_dir)]) == 0  # fmt: skip
    options = ReplayOptions(inject="first", provider="offline", stats="local",
                            only=("momentum_shift",))  # fmt: skip
    asyncio.run(replay_match(match_dir, options))
    return tmp_path


def _settings(data_dir: Path, **kw: Any) -> ApiSettings:
    base = {"data_dir": data_dir, "api_auth": "dev", "api_decision_provider": "offline"}
    return ApiSettings(_env_file=None, **{**base, **kw})  # type: ignore[call-arg]


def _repo(data_dir: Path) -> MatchRepository:
    return MatchRepository(data_dir, LedgerQueueSettings(_env_file=None, ledger="file"))  # type: ignore[call-arg]


def _client(
    data_dir: Path, verifier: EntraVerifier | None = None, notifier: Any = None, **kw: Any
) -> TestClient:
    app = create_app(
        _settings(data_dir, **kw), _repo(data_dir), verifier, notifier or NullNotifier()
    )
    return TestClient(app)


def test_realtime_negotiate_and_decision_signals(data_dir: Path) -> None:
    assert _client(data_dir).get("/api/v1/realtime/negotiate?match_id=M0002").json() == {
        "url": None, "group": "fans"}  # push off: the Studio polls  # fmt: skip
    push = RecordingNotifier()
    client = _client(data_dir, notifier=push)
    fan = client.get("/api/v1/realtime/negotiate", params={"match_id": "M0002"}).json()
    assert fan["url"].endswith("fans.M0002")
    assert client.get("/api/v1/realtime/negotiate", params={"match_id": "M9"}).status_code == 422
    closed = _client(data_dir, notifier=push, api_auth="entra", api_tenant_id=None)
    assert closed.get("/api/v1/realtime/negotiate/producer?match_id=M0002").status_code == 503
    [view] = client.get("/api/v1/matches/M0002/insights").json()
    insight_id = view["insight"]["insight_id"]
    client.post(f"/api/v1/insights/{insight_id}/decision", json={"approve": True})
    assert [(g, m["event"]) for g, m in push.sent] == [
        ("producers.M0002", "insight_changed"), ("fans.M0002", "overlays_changed")]  # fmt: skip


def test_ops_alerts_listed_and_acknowledged_by_the_signed_in_producer(data_dir: Path) -> None:
    ledger = FileLedger(data_dir / "M0002" / "ledger")
    desk = OpsDesk(ledger)
    desk.raise_alert("M0002", "feed_gap", "warning", "No feed message for 12 s", ["M0002-E000100"])
    desk.raise_alert("M0002", "model_fallback", "info", "Template copy used", ["M0002-I0001"])
    ledger.flush()
    push = RecordingNotifier()
    client = _client(data_dir, notifier=push)
    listed = client.get("/api/v1/matches/M0002/alerts", params={"state": "open"}).json()
    assert [a["alert_id"] for a in listed] == ["M0002-X0002", "M0002-X0001"]  # newest first
    acked = client.post("/api/v1/alerts/M0002-X0001/acknowledge")
    assert acked.status_code == 200 and acked.json()["acknowledged_by"] == "producer_local"
    assert push.sent[-1] == ("producers.M0002", {"event": "alert_changed", "match_id": "M0002",
        "alert_id": "M0002-X0001", "kind": "feed_gap", "severity": "warning",
        "acknowledged": True})  # fmt: skip
    open_now = client.get("/api/v1/matches/M0002/alerts?state=open").json()
    assert [a["alert_id"] for a in open_now] == ["M0002-X0002"]
    assert len(client.get("/api/v1/matches/M0002/alerts").json()) == 2  # all, by default
    assert client.post("/api/v1/alerts/M0002-X0009/acknowledge").status_code == 404
    assert client.post("/api/v1/alerts/M0002-I0001/acknowledge").status_code == 422
    assert client.post("/api/v1/alerts/M0009-X0001/acknowledge").status_code == 404
    closed = _client(data_dir, api_auth="entra", api_tenant_id=None)
    assert closed.get("/api/v1/matches/M0002/alerts").status_code == 503
    assert closed.post("/api/v1/alerts/M0002-X0002/acknowledge").status_code == 503


def test_public_endpoints(data_dir: Path) -> None:
    client = _client(data_dir)
    assert client.get("/api/v1/health").json()["matches"] == 1
    [summary] = client.get("/api/v1/matches").json()
    assert summary["match_id"] == "M0002" and summary["away"]["name"] == "Corrowmere Town"
    detail = client.get("/api/v1/matches/M0002").json()
    assert (detail["score_home"], detail["score_away"]) == (4, 1)
    assert "ratings" not in detail["players"][0]  # fans get names, not internals
    assert client.get("/api/v1/matches/M0009").status_code == 404
    assert client.get("/api/v1/matches/..%2Fetc").status_code in (404, 422)


def test_pending_insight_is_hidden_from_fans_until_a_producer_approves(data_dir: Path) -> None:
    client = _client(data_dir)
    assert client.get("/api/v1/matches/M0002/overlays").json() == []
    [view] = client.get("/api/v1/matches/M0002/insights", params={"status": "pending"}).json()
    insight_id = view["insight"]["insight_id"]
    detail = client.get(f"/api/v1/insights/{insight_id}").json()
    assert detail["insight"]["audit_status"] == "corrected"  # the planted error was caught
    assert any(a["action"] == "inject_error" for a in detail["audits"])
    assert detail["evidence"] and detail["overlays"] == []

    result = client.post(f"/api/v1/insights/{insight_id}/decision", json={"approve": True})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "aired" and body["overlays"] == 15
    assert body["approved_by"] == "producer_local"
    casual_es = client.get("/api/v1/matches/M0002/overlays",
                           params={"mode": "casual", "language": "es"}).json()  # fmt: skip
    assert len(casual_es) == 1 and casual_es[0]["language"] == "es"


def test_decision_rejects_unknown_fields_and_is_rate_limited(data_dir: Path) -> None:
    client = _client(data_dir, api_decisions_per_minute=1)
    [view] = client.get("/api/v1/matches/M0002/insights").json()
    url = f"/api/v1/insights/{view['insight']['insight_id']}/decision"
    smuggled = client.post(url, json={"approve": False, "approved_by": "someone_else"})
    assert smuggled.status_code == 422  # identity never comes from the body
    assert client.post(url, json={"approve": False, "reason": "not now"}).status_code == 200
    assert client.post(url, json={"approve": True}).status_code == 429


class FakeJwks:
    def __init__(self, public_key: Any) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("Key", (), {"key": self.public_key})()


@pytest.fixture(scope="module")
def keys() -> tuple[Any, Any]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def _token(private: Any, **claims: Any) -> str:
    now = int(time.time())
    base = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": AUDIENCE,
            "sub": "subject-1", "oid": "producer-oid-1", "name": "P. Producer",
            "roles": ["Producer"], "iat": now, "exp": now + 600}  # fmt: skip
    return jwt.encode({**base, **claims}, private, algorithm="RS256")


def test_producer_endpoints_fail_closed_and_verify_entra_tokens(
    data_dir: Path, keys: tuple[Any, Any]
) -> None:
    private, public = keys
    unconfigured = _client(data_dir, api_auth="entra", api_tenant_id=None, api_audience=None)
    assert unconfigured.get("/api/v1/me").status_code == 503
    assert unconfigured.get("/api/v1/matches").status_code == 200  # fan view stays public

    verifier = EntraVerifier(TENANT, AUDIENCE, "Producer", jwks=FakeJwks(public))
    client = _client(data_dir, verifier, api_auth="entra")
    assert client.get("/api/v1/me").status_code == 401
    wrong_aud = {"Authorization": f"Bearer {_token(private, aud='api://other')}"}
    assert client.get("/api/v1/me", headers=wrong_aud).status_code == 401
    no_role = {"Authorization": f"Bearer {_token(private, roles=[])}"}
    assert client.get("/api/v1/matches/M0002/insights", headers=no_role).status_code == 403
    good = {"Authorization": f"Bearer {_token(private)}"}
    me = client.get("/api/v1/me", headers=good).json()
    assert me == {"producer_id": "producer-oid-1", "name": "P. Producer", "auth": "entra"}
    [view] = client.get("/api/v1/matches/M0002/insights", headers=good).json()
    url = f"/api/v1/insights/{view['insight']['insight_id']}/decision"
    decided = client.post(url, json={"approve": True}, headers=good).json()
    assert decided["approved_by"] == "producer-oid-1"  # from the verified token


def test_rate_limiter_window() -> None:
    limiter = RateLimiter(2)
    limiter.check("p", now=0)
    limiter.check("p", now=1)
    with pytest.raises(HTTPException):
        limiter.check("p", now=2)
    limiter.check("p", now=61.5)  # the first call left the window
    limiter.check("other", now=2)  # per producer
