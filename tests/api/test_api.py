"""/api/v1: public fan endpoints, producer endpoints behind Entra ID, decisions."""

import asyncio
import json
import time
from itertools import pairwise
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
    gap = listed[1]  # plain words for the producer; the technical text stays in message
    assert gap["headline"] == "The live match data stopped arriving for 12 seconds."
    assert gap["needs_action"] and gap["message"] == "No feed message for 12 s"
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


def test_insight_story_traces_an_aired_insight_without_producer_identity(
    data_dir: Path, keys: tuple[Any, Any]
) -> None:
    private, public = keys
    verifier = EntraVerifier(TENANT, AUDIENCE, "Producer", jwks=FakeJwks(public))
    client = _client(data_dir, verifier, api_auth="entra")
    good = {"Authorization": f"Bearer {_token(private)}"}
    [view] = client.get("/api/v1/matches/M0002/insights", headers=good).json()
    insight_id = view["insight"]["insight_id"]
    assert client.get(f"/api/v1/insights/{insight_id}/story").status_code == 404  # pending
    assert client.get(f"/api/v1/insights/{insight_id}/broadcast").status_code == 404
    client.post(f"/api/v1/insights/{insight_id}/decision", json={"approve": True}, headers=good)

    story = client.get(f"/api/v1/insights/{insight_id}/story").json()  # public, no token
    assert "producer-oid-1" not in json.dumps(story)  # the approver's identity stays internal
    assert story["insight"]["approved_by"] == "producer"
    stages = {s["key"]: s for s in story["stages"]}
    assert list(stages) == ["detected", "analyzed", "explained", "audited", "approved",
                            "published"] and all(s["done"] for s in stages.values())  # fmt: skip
    assert stages["detected"]["detail"] == "Momentum shift at 69'"
    assert stages["audited"]["detail"].startswith("Corrected after 1 revision")
    assert stages["published"]["detail"] == "15 overlays: 5 audience modes × 3 languages"  # noqa: RUF001
    assert stages["explained"]["at"] and stages["approved"]["at"]  # timestamps recorded
    planted = next(s for s in story["steps"] if s["action"] == "inject_error")
    assert planted["role"] == "test" and planted["unsupported_numbers"] == ["32"]
    check = next(s for s in story["steps"] if s["action"] == "check")
    assert check["passed"] is False and check["findings"][0].startswith("INS-01")
    assert check["unsupported_numbers"] == ["32"]  # the Auditor saw the planted draft
    redrafts = [s for s in story["steps"] if s["action"] == "draft"]
    assert [s["unsupported_numbers"] for s in redrafts] == [[], []]
    assert story["steps"][-1]["agent"] == "producer"
    # passes and turnovers since the onset, plus the opponent's one shot in the spell
    assert story["trigger"]["rule_id"] == "momentum_shift" and len(story["evidence"]) == 33
    assert sum(story["evidence_counts"].values()) == 33 and len(story["overlays"]) == 15

    assert stages["explained"]["detail"] == "2 drafts by the explainer"  # the planted, the fix

    # Two on-screen versions that fell back to templates do not make the insight a template.
    from pitch_ledger.schema import AuditRecord

    ledger = FileLedger(data_dir / "M0002" / "ledger")
    ledger.add_audit(AuditRecord(
        audit_id=ledger.next_id("M0002", "A"), match_id="M0002", insight_id=insight_id,
        trigger_id=view["insight"]["trigger_id"], round=0, actor="copy_check",
        action="fallback", findings=["single_metric/es: two figures", "single_metric/fil: x"],
    ))  # fmt: skip
    ledger.flush()
    stages = {s["key"]: s for s in client.get(f"/api/v1/insights/{insight_id}/story").json()[
        "stages"]}  # fmt: skip
    assert stages["explained"]["detail"] == "2 drafts by the explainer"
    assert stages["published"]["detail"].endswith("; 2 of them from templates (the model's text "
                                                  "failed the number or style check)")  # fmt: skip

    package = client.get(f"/api/v1/insights/{insight_id}/broadcast").json()
    assert package["format"] == "pitch-ledger.broadcast/1"
    assert package["metrics"] == story["insight"]["metrics"] and len(package["graphics"]) == 15
    assert package["audit"] == {"status": "corrected", "drafts": 2, "revisions": 1,
                                "approval": "producer",
                                "model_version": "offline-templates"}  # fmt: skip
    assert client.get("/api/v1/insights/M0002-I0099/story").status_code == 404


def test_match_centre_timeline_and_tracking_use_one_broadcast_frame(data_dir: Path) -> None:
    client = _client(data_dir)
    response = client.get("/api/v1/matches/M0002/timeline", headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip"
    timeline = response.json()
    assert [p["period"] for p in timeline["periods"]] == [1, 2]
    assert all(p["end_ms"] > 45 * 60_000 for p in timeline["periods"])
    last = timeline["events"][-1]
    assert (last["type"], last["score_home"], last["score_away"]) == ("period_end", 4, 1)
    assert {e["type"] for e in timeline["events"] if e["key"]} >= {"shot", "period_start"}
    goals = [e for e in timeline["events"] if e["type"] == "shot" and e["outcome"] == "goal"]
    assert [(g["score_home"], g["score_away"]) for g in goals] == [
        (1, 0), (1, 1), (2, 1), (3, 1), (4, 1)]  # the scoreboard changes at the goal  # fmt: skip
    raw = {json.loads(line)["seq_no"]: json.loads(line)
           for line in (data_dir / "M0002" / "events.jsonl").read_text().splitlines()}  # fmt: skip
    for e in timeline["events"]:
        stored = raw[e["seq"]]
        if stored["start_x"] is None:
            continue
        mirrored = (stored["team_id"] == "T02") != (stored["period"] == 2)  # away team: T02
        expected = round(105 - stored["start_x"], 1) if mirrored else round(stored["start_x"], 1)
        assert e["x"] == expected
        assert e["duration_ms"] == stored["duration_ms"]  # the Studio's ball flight times

    def keeper_x(period: int) -> float:
        window = client.get("/api/v1/matches/M0002/tracking",
                            params={"period": period, "from_s": 0, "to_s": 10}).json()  # fmt: skip
        assert len(window["frames"]) == 11 and len(window["players"]) >= 22
        i = window["players"].index("T01-P01")  # the home goalkeeper
        return float(window["frames"][0][2 * i])

    assert keeper_x(1) < 15 and keeper_x(2) > 90  # home defends the left goal, then the right
    capped = client.get("/api/v1/matches/M0002/tracking",
                        params={"period": 1, "from_s": 60, "to_s": 5000}).json()  # fmt: skip
    assert (capped["from_s"], capped["to_s"], len(capped["frames"])) == (60, 360, 301)
    assert client.get("/api/v1/matches/M0009/timeline").status_code == 404
    assert client.get("/api/v1/matches/M0002/tracking?period=3").status_code == 422


def test_match_centre_analytics_and_pass_network_come_from_the_engine(data_dir: Path) -> None:
    from pitch_ledger.engine.metrics import shot_xg
    from pitch_ledger.schema import Event

    client = _client(data_dir)
    data = client.get("/api/v1/matches/M0002/analytics").json()
    events = [Event.model_validate(json.loads(line)) for line in
              (data_dir / "M0002" / "events.jsonl").read_text().splitlines()]  # fmt: skip
    shots = [e for e in events if e.event_type.value == "shot"]
    assert [s["xg"] for s in data["shots"]] == [round(shot_xg(e), 2) for e in shots]
    home = [s for s in data["shots"] if s["team_id"] == "T01"]
    assert home[-1]["team_shots"] == len(home)
    assert home[-1]["team_xg"] == round(sum(shot_xg(e) for e in shots if e.team_id == "T01"), 2)
    assert sum(s["outcome"] == "goal" for s in data["shots"]) == 5
    minutes = [m for m in data["team_state"] if m["team_id"] == "T02" and m["minute"] == 69.0]
    assert minutes and 0 <= minutes[0]["control"] <= 100  # whole numbers, as insights quote them
    assert 0 <= minutes[0]["pass_accuracy"] <= 100 and minutes[0]["tempo"] > 0

    from pitch_ledger.engine.windows import MatchEngine
    from pitch_ledger.matchlab.io import read_match

    engine = MatchEngine(read_match(data_dir / "M0002", with_tracking=False).match, events)
    chaos = {(c["period"], c["minute"]): c["chaos"] for c in data["chaos"]}
    assert len(chaos) == len(data["team_state"]) // 2  # one per minute, both teams share it
    assert chaos[(2, 69.0)] == round(engine.chaos_window(2, 19 * 60_000, 24 * 60_000).chaos)
    assert all(0 <= c <= 100 for c in chaos.values())

    params = {"team": "T02", "period": 2, "end_ms": 1431700, "window_s": 600}
    net = client.get("/api/v1/matches/M0002/pass-network", params=params).json()
    window = [e for e in events if e.team_id == "T02" and e.period == 2
              and 831700 < e.clock_ms <= 1431700 and e.event_type.value == "pass"
              and e.outcome is not None and e.outcome.value == "complete"
              and e.receiver_player_id and e.details.set_piece != "throw_in"]  # type: ignore[union-attr]  # fmt: skip
    assert net["passes"] == len(window) == sum(edge["passes"] for edge in net["edges"])
    assert all(node["player_id"].startswith("T02-") for node in net["nodes"])
    bad = {**params, "team": "T05"}
    assert client.get("/api/v1/matches/M0002/pass-network", params=bad).status_code == 404


def test_player_timeline_running_totals_match_the_stats_tools(data_dir: Path) -> None:
    from pitch_ledger.mcp_server.tools import StatsTools

    client = _client(data_dir)
    tools = StatsTools(data_dir / "M0002")
    scorers = [e.player_id for e in tools.events if e.event_type.value == "shot"
               and e.outcome is not None and e.outcome.value == "goal"]  # fmt: skip
    for pid in {scorers[0], "T01-P01", "T02-P05"}:
        assert pid is not None
        timeline = client.get(f"/api/v1/matches/M0002/players/{pid}/timeline").json()
        assert timeline["team_id"] == pid[:3]
        rows = timeline["minutes"]
        clocks = [(r["period"], r["clock_ms"]) for r in rows]
        assert clocks == sorted(clocks) and rows[0]["clock_ms"] == 60_000
        stats = tools.get_player_stats(pid)
        last = rows[-1]  # the period end: whole-match totals
        for key in ("passes", "passes_completed", "pass_accuracy", "shots", "xg", "goals",
                    "sprints", "top_speed_kmh"):  # fmt: skip
            assert last[key] == stats[key], (pid, key)
        assert last["distance_km"] == pytest.approx(stats["distance_km"], abs=0.01)
        assert all(a["passes"] <= b["passes"] for a, b in pairwise(rows))
        half = [r for r in rows if r["period"] == 1][-1]  # period 1 totals, by the tools too
        first = tools.get_player_stats(pid, period=1)
        assert (half["passes"], half["goals"]) == (first["passes"], first["goals"])
    assert client.get("/api/v1/matches/M0002/players/T05-P01/timeline").status_code == 404
    assert client.get("/api/v1/matches/M0002/players/bad/timeline").status_code == 422


def test_public_endpoints(data_dir: Path) -> None:
    client = _client(data_dir)
    assert client.get("/api/v1/health").json()["matches"] == 1
    [summary] = client.get("/api/v1/matches").json()
    assert summary["match_id"] == "M0002" and summary["away"]["name"] == "Corrowmere Town"
    assert (summary["score_home"], summary["score_away"]) == (4, 1)
    assert summary["aired_insights"] == 0  # the fixture's only insight is pending
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
    assert all(a["summary"] for a in detail["audits"])
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


def test_any_account_access_lets_any_signed_in_microsoft_account_produce(
    data_dir: Path, keys: tuple[Any, Any]
) -> None:
    """The public demo: judges sign in with their own work or personal account."""
    private, public = keys
    verifier = EntraVerifier(None, AUDIENCE, "Producer", jwks=FakeJwks(public), any_account=True)
    client = _client(data_dir, verifier, api_auth="entra")
    other = "11111111-2222-3333-4444-555555555555"
    personal = "9188040d-6c67-4c5b-b112-36a304b66dad"  # the consumer (personal account) tenant
    for tid in (other, personal, TENANT):
        token = _token(private, iss=f"https://login.microsoftonline.com/{tid}/v2.0", tid=tid,
                       roles=[], oid=f"oid-{tid[:4]}", name=None,
                       preferred_username="judge@example.com")  # fmt: skip
        me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).json()
        assert me["producer_id"] == f"oid-{tid[:4]}" and me["name"] == "judge@example.com"
    spoofed = _token(private, iss=f"https://login.microsoftonline.com/{other}/v2.0", tid=TENANT)
    assert (
        client.get("/api/v1/me", headers={"Authorization": f"Bearer {spoofed}"}).status_code == 401
    )
    no_tid = _token(private)  # issuer present, tenant claim missing
    assert (
        client.get("/api/v1/me", headers={"Authorization": f"Bearer {no_tid}"}).status_code == 401
    )
    wrong_aud = _token(private, aud="api://other", tid=TENANT)
    assert (
        client.get("/api/v1/me", headers={"Authorization": f"Bearer {wrong_aud}"}).status_code
        == 401
    )


def test_role_access_still_requires_our_tenant_and_the_role(keys: tuple[Any, Any]) -> None:
    private, public = keys
    verifier = EntraVerifier(TENANT, AUDIENCE, "Producer", jwks=FakeJwks(public))
    other = "11111111-2222-3333-4444-555555555555"
    foreign = _token(private, iss=f"https://login.microsoftonline.com/{other}/v2.0", tid=other)
    with pytest.raises(HTTPException) as caught:
        verifier.verify(foreign)
    assert caught.value.status_code == 401
    with pytest.raises(ValueError):
        EntraVerifier(None, AUDIENCE, "Producer", jwks=FakeJwks(public))


def test_any_account_settings_build_a_verifier_without_a_tenant(tmp_path: Path) -> None:
    settings = ApiSettings(data_dir=tmp_path, api_auth="entra", api_audience="aud-id",
                           api_tenant_id=None, api_producers="any_account")  # fmt: skip
    app = create_app(settings)
    assert app.state.verifier is not None and app.state.verifier.any_account


def test_a_decision_keeps_its_model_cost(data_dir: Path, tmp_path: Path) -> None:
    from pitch_ledger.eventstore import FileEventStore

    store = FileEventStore(tmp_path / "store")
    app = create_app(_settings(data_dir), _repo(data_dir), None, NullNotifier(), usage_store=store)
    client = TestClient(app)
    [view] = client.get("/api/v1/matches/M0002/insights", params={"status": "pending"}).json()
    insight_id = view["insight"]["insight_id"]
    assert client.post(f"/api/v1/insights/{insight_id}/decision",
                       json={"approve": True}).json()["status"] == "aired"  # fmt: skip
    store.close()
    [row] = [
        json.loads(x) for x in (tmp_path / "store" / "RunUsage.jsonl").read_text().splitlines()
    ]
    assert (row["kind"], row["match_id"], row["aired_insights"]) == ("decision", "M0002", 1)
    assert row["calls"] > 0 and row["cost_usd"] == 0.0  # offline templates: no cost
    assert row["model_version"] == "offline-templates"
