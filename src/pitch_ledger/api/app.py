"""FastAPI app: ``/api/v1``.

Public (fan view): health, matches, a match, the aired overlays of a match, the Match Centre
replay data (timeline, tracking frames, analytics, pass network, a player's running
totals), and the story and broadcast package of an aired
insight (no producer identity).
Producer (Entra ID producer role, see ``auth``): every insight with its status, one insight
with its audit trail and evidence, the approve/block decision (rate-limited: an approval
runs the Personalizer and Translator), and the match's ops alerts with acknowledgement.

Run: ``pitch-ledger api`` (Uvicorn).
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from pitch_ledger.agents.replay import decide_insight
from pitch_ledger.api.auth import EntraVerifier, Producer, producer
from pitch_ledger.api.match_centre import (
    Analytics,
    MatchCentre,
    MatchData,
    PassNetwork,
    PlayerTimeline,
    Timeline,
    TrackingWindow,
)
from pitch_ledger.api.narrative import build_narratives
from pitch_ledger.api.plain import explain_alert, explain_step
from pitch_ledger.api.repository import MatchRepository
from pitch_ledger.api.schemas import (
    AlertState,
    AlertView,
    AuditStep,
    BroadcastPackage,
    DecisionRequest,
    DecisionResult,
    Health,
    InsightDetail,
    InsightStatus,
    InsightStory,
    InsightView,
    MatchDetail,
    MatchSummary,
    Me,
    PlayerView,
    Realtime,
    team_view,
)
from pitch_ledger.api.settings import ApiSettings
from pitch_ledger.api.story import build_broadcast, build_process, build_story, evidence_events
from pitch_ledger.eventstore.port import EventStore
from pitch_ledger.eventstore.rows import insight_status, run_usage_row
from pitch_ledger.feed.live import match_minute
from pitch_ledger.infra_adapters.ledger_queue import LedgerQueueSettings
from pitch_ledger.infra_adapters.stores import StoreSettings, open_store
from pitch_ledger.infra_adapters.webpubsub import PushSettings, open_notifier
from pitch_ledger.ledger import FileLedger, InMemoryLedger
from pitch_ledger.matchlab.io import MatchFiles
from pitch_ledger.ops import OpsDesk
from pitch_ledger.push import Notifier
from pitch_ledger.schema import Insight, OpsAlert, Overlay

log = logging.getLogger(__name__)

MatchIdPath = Annotated[str, Path(pattern=r"^M\d{4}$")]
InsightIdPath = Annotated[str, Path(pattern=r"^M\d{4}-I\d{4}$")]
AlertIdPath = Annotated[str, Path(pattern=r"^M\d{4}-X\d{4}$")]
Mode = Annotated[str | None, Query(pattern=r"^[a-z_]{3,20}$")]
Language = Annotated[str | None, Query(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")]
PlayerIdPath = Annotated[str, Path(pattern=r"^T\d{2}-P\d{2}$")]
AIRED_COUNT_TTL_S = 60.0


class RateLimiter:
    """At most ``limit`` calls per key in a sliding minute (in-process; one API replica)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        calls = self._calls[key]
        while calls and now - calls[0] > 60:
            calls.popleft()
        if len(calls) >= self.limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many decisions; wait")
        calls.append(now)


def create_app(
    settings: ApiSettings | None = None,
    repository: MatchRepository | None = None,
    verifier: EntraVerifier | None = None,
    notifier: Notifier | None = None,
    usage_store: EventStore | None = None,
) -> FastAPI:
    """``usage_store`` keeps each decision's model cost (``RunUsage``); by default the ADX
    event store when ``PITCH_LEDGER_STORE=adx``, else none (local runs write nothing)."""
    settings = settings or ApiSettings()
    repo = repository or MatchRepository(settings.data_dir, LedgerQueueSettings())
    tenant, audience = settings.api_tenant_id, settings.api_audience
    any_account = settings.api_producers == "any_account"
    if verifier is None and settings.api_auth == "entra" and audience and (tenant or any_account):
        verifier = EntraVerifier(
            tenant, audience, settings.api_producer_role, any_account=any_account
        )
    app = FastAPI(title="Pitch Ledger API", version="1", docs_url="/api/v1/docs",
                  openapi_url="/api/v1/openapi.json")  # fmt: skip
    app.state.settings, app.state.verifier = settings, verifier
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)  # timelines and tracking are large
    limiter = RateLimiter(settings.api_decisions_per_minute)
    stores: list[EventStore] = [usage_store] if usage_store is not None else []

    def keep_usage(insight: Insight, spent: dict[str, Any]) -> None:
        """A decision's model cost to ``RunUsage`` (fail-open: never fails the decision)."""
        if not stores:
            store_settings = StoreSettings()
            if store_settings.store != "adx":
                return
            try:
                stores.append(open_store(store_settings, fail_open=True))
            except Exception as exc:  # settings or sign-in: skip the cost row, keep serving
                log.warning("decision cost not kept: %s", exc)
                return
        row = run_usage_row("decision", insight.match_id, spent["usage"], spent["provider"],
                            spent["model_version"], int(insight_status(insight) == "aired"),
                            time.time())  # fmt: skip
        stores[0].append("RunUsage", [row])
        stores[0].flush()

    centre = MatchCentre()
    push: Notifier = notifier or open_notifier(PushSettings())

    def match_files(match_id: str) -> MatchFiles:
        files = repo.files(match_id)
        if files is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no match {match_id}")
        return files

    def summary(files: MatchFiles, aired: int | None = None) -> MatchSummary:
        teams = {t.team_id: t for t in files.teams}
        m = files.match
        last = files.events[-1] if files.events else None
        return MatchSummary(match_id=m.match_id, kickoff_utc=m.kickoff_utc, venue=m.venue,
                            home=team_view(teams[m.home_team_id]),
                            away=team_view(teams[m.away_team_id]),
                            score_home=last["score_home"] if last else None,
                            score_away=last["score_away"] if last else None,
                            aired_insights=aired)  # fmt: skip

    aired_counts: dict[str, tuple[float, int]] = {}

    def aired_count(match_id: str) -> int:
        """Aired insights of a match, cached a minute (one ledger read per match)."""
        now = time.monotonic()
        cached = aired_counts.get(match_id)
        if cached is None or now - cached[0] > AIRED_COUNT_TTL_S:
            insights = repo.ledger(match_id).insights(match_id)
            cached = (now, sum(insight_status(i) == "aired" for i in insights))
            aired_counts[match_id] = cached
        return cached[1]

    def insight_of(insight_id: str) -> tuple[InMemoryLedger, InsightView]:
        match_id = insight_id.split("-I")[0]
        match_files(match_id)
        ledger = repo.ledger(match_id)
        insight = ledger.get_insight(insight_id)
        if insight is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no insight {insight_id}")
        return ledger, InsightView(insight=insight, status=insight_status(insight))

    @app.get("/api/v1/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok", ledger=repo.settings.describe_ledger(),
                      matches=len(repo.match_ids()))  # fmt: skip

    @app.get("/api/v1/matches", response_model=list[MatchSummary])
    def matches() -> list[MatchSummary]:
        """Every match with its final score (or the score so far) and aired insight count."""
        return [summary(f, aired_count(mid)) for mid in repo.match_ids() if (f := repo.files(mid))]

    @app.get("/api/v1/matches/{match_id}", response_model=MatchDetail)
    def match(match_id: MatchIdPath) -> MatchDetail:
        files = match_files(match_id)
        last = files.events[-1] if files.events else None
        players = [PlayerView(player_id=p.player_id, team_id=p.team_id,
                              display_name=p.display_name, shirt_number=p.shirt_number,
                              position=p.position.value) for p in files.players]  # fmt: skip
        return MatchDetail(
            **summary(files).model_dump(),
            players=players,
            last_minute=round(match_minute(last), 1) if last else None,
        )

    @app.get("/api/v1/matches/{match_id}/overlays", response_model=list[Overlay])
    def overlays(
        match_id: MatchIdPath, mode: Mode = None, language: Language = None
    ) -> list[Overlay]:
        """Overlays of aired insights only (the fan view), in match order."""
        match_files(match_id)
        ledger = repo.ledger(match_id)
        aired = {i.insight_id for i in ledger.insights(match_id) if insight_status(i) == "aired"}
        found = [o for o in ledger.overlays() if o.insight_id in aired
                 and (mode is None or o.audience_mode.value == mode)
                 and (language is None or o.language == language)]  # fmt: skip
        return sorted(found, key=lambda o: (o.period, o.trigger_clock_ms, o.overlay_id))

    def centre_data(match_id: str) -> MatchData:
        match_dir = repo.match_dir(match_id)
        if match_dir is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no match {match_id}")
        return centre.get(match_dir)

    @app.get("/api/v1/matches/{match_id}/timeline", response_model=Timeline)
    def timeline(match_id: MatchIdPath) -> Timeline:
        """Every event of the match on one clock, positions in the broadcast frame."""
        return centre_data(match_id).timeline

    @app.get("/api/v1/matches/{match_id}/tracking", response_model=TrackingWindow)
    def tracking(
        match_id: MatchIdPath,
        period: Annotated[int, Query(ge=1, le=2)],
        from_s: Annotated[int, Query(ge=0)] = 0,
        to_s: Annotated[int, Query(ge=0)] = 299,
    ) -> TrackingWindow:
        """Player positions per second for up to five minutes of one period."""
        return centre_data(match_id).tracking_window(period, from_s, to_s)

    @app.get("/api/v1/matches/{match_id}/analytics", response_model=Analytics)
    def analytics(match_id: MatchIdPath) -> Analytics:
        """Team state per minute and every shot with xG (the browser shows them up to its clock)."""
        return centre_data(match_id).analytics()

    @app.get("/api/v1/matches/{match_id}/pass-network", response_model=PassNetwork)
    def pass_network(
        match_id: MatchIdPath,
        team: Annotated[str, Query(pattern=r"^T\d{2}$")],
        period: Annotated[int, Query(ge=1, le=2)],
        end_ms: Annotated[int, Query(ge=0)],
        window_s: Annotated[int, Query(ge=60, le=2700)] = 600,
    ) -> PassNetwork:
        """One team's completed passes in the window before ``end_ms`` (same half)."""
        data = centre_data(match_id)
        if team not in (data.files.match.home_team_id, data.files.match.away_team_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{team} is not in {match_id}")
        return data.pass_network(team, period, end_ms, window_s)

    @app.get("/api/v1/matches/{match_id}/players/{player_id}/timeline",
             response_model=PlayerTimeline)  # fmt: skip
    def player_timeline(match_id: MatchIdPath, player_id: PlayerIdPath) -> PlayerTimeline:
        """A player's running totals at every match minute (the browser shows them up to its
        clock): passing, shooting and, from tracking, distance, top speed and sprints."""
        data = centre_data(match_id)
        found = data.player_timeline(player_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{player_id} is not in {match_id}")
        return found

    def aired(insight_id: str) -> tuple[InMemoryLedger, InsightView]:
        """An insight the public may see: aired only (pending and blocked stay internal)."""
        ledger, view = insight_of(insight_id)
        if view.status != "aired":
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no aired insight {insight_id}")
        return ledger, view

    @app.get("/api/v1/insights/{insight_id}/story", response_model=InsightStory)
    def story(insight_id: InsightIdPath) -> InsightStory:
        """An aired insight traced end to end: detection, numbers, agents, approval, overlays."""
        ledger, view = aired(insight_id)
        files = match_files(view.insight.match_id)
        trigger = ledger.get_trigger(view.insight.trigger_id or "")
        overlays = ledger.overlays(insight_id)
        stored = ledger.story(insight_id)
        narratives = build_narratives(centre_data(view.insight.match_id), view.insight, trigger,
                                      overlays, stored)  # fmt: skip
        return build_story(summary(files), view.insight, trigger, ledger.audits(insight_id),
                           overlays, {e["event_id"]: e for e in files.events},
                           narratives, stored)  # fmt: skip

    @app.get("/api/v1/insights/{insight_id}/broadcast", response_model=BroadcastPackage)
    def broadcast(insight_id: InsightIdPath) -> BroadcastPackage:
        """The aired insight as a structured broadcast event (every mode and language)."""
        ledger, view = aired(insight_id)
        return build_broadcast(view.insight, ledger.audits(insight_id), ledger.overlays(insight_id))

    @app.get("/api/v1/realtime/negotiate", response_model=Realtime)
    def negotiate(match_id: Annotated[str, Query(pattern=r"^M\d{4}$")]) -> Realtime:
        """A WebSocket URL that receives the match's fan signals (push off: no URL; poll)."""
        match_files(match_id)
        return Realtime(url=push.client_url(match_id, "fans"), group="fans")

    @app.get("/api/v1/realtime/negotiate/producer", response_model=Realtime)
    def negotiate_producer(
        match_id: Annotated[str, Query(pattern=r"^M\d{4}$")],
        who: Annotated[Producer, Depends(producer)],
    ) -> Realtime:
        match_files(match_id)
        return Realtime(url=push.client_url(match_id, "producers"), group="producers")

    @app.get("/api/v1/me", response_model=Me)
    def me(who: Annotated[Producer, Depends(producer)]) -> Me:
        return Me(producer_id=who.producer_id, name=who.name, auth=settings.api_auth)

    @app.get("/api/v1/matches/{match_id}/insights", response_model=list[InsightView])
    def insights(
        match_id: MatchIdPath,
        who: Annotated[Producer, Depends(producer)],
        status_: Annotated[InsightStatus | None, Query(alias="status")] = None,
    ) -> list[InsightView]:
        match_files(match_id)
        views = [InsightView(insight=i, status=insight_status(i))
                 for i in repo.ledger(match_id).insights(match_id)]  # fmt: skip
        views = [v for v in views if status_ is None or v.status == status_]
        return sorted(views, key=lambda v: (v.insight.period, v.insight.clock_ms))

    @app.get("/api/v1/insights/{insight_id}", response_model=InsightDetail)
    def insight(
        insight_id: InsightIdPath, who: Annotated[Producer, Depends(producer)]
    ) -> InsightDetail:
        ledger, view = insight_of(insight_id)
        events = {e["event_id"]: e for e in match_files(view.insight.match_id).events}
        evidence = evidence_events(events, view.insight.evidence_event_ids)
        audits = ledger.audits(insight_id)
        steps = [AuditStep(**a.model_dump(), summary=explain_step(a)) for a in audits]
        overlays = ledger.overlays(insight_id)
        trigger = ledger.get_trigger(view.insight.trigger_id or "")
        process = build_process(view.insight, trigger, audits, overlays, evidence,
                                ledger.story(insight_id))  # fmt: skip
        return InsightDetail(**view.model_dump(), audits=steps, evidence=evidence,
                             overlays=overlays, process=process)  # fmt: skip

    @app.post("/api/v1/insights/{insight_id}/decision", response_model=DecisionResult)
    async def decision(
        insight_id: InsightIdPath,
        body: DecisionRequest,
        who: Annotated[Producer, Depends(producer)],
    ) -> DecisionResult:
        limiter.check(who.producer_id)
        ledger, view = insight_of(insight_id)
        match_dir = repo.match_dir(view.insight.match_id)
        assert match_dir is not None  # insight_of checked the match exists
        spent: dict[str, Any] = {}
        outcome = await decide_insight(match_dir, ledger, insight_id, who.producer_id,
                                       body.approve, body.reason,
                                       provider=settings.api_decision_provider,
                                       notifier=push, usage_out=spent)  # fmt: skip
        decided = ledger.get_insight(insight_id) or view.insight
        if spent.get("usage", {}).get("calls"):
            await asyncio.to_thread(keep_usage, decided, spent)
        signal = {"insight_id": insight_id, "status": insight_status(decided)}
        push.notify(decided.match_id, "producers", "insight_changed", signal)
        if signal["status"] == "aired":
            push.notify(decided.match_id, "fans", "overlays_changed", signal)
        return DecisionResult(insight_id=insight_id, status=insight_status(decided),
                              audit_status=outcome.audit_status, overlays=outcome.overlays,
                              approved_by=decided.approved_by)  # fmt: skip

    @app.get("/api/v1/matches/{match_id}/alerts", response_model=list[AlertView])
    def alerts(
        match_id: MatchIdPath,
        who: Annotated[Producer, Depends(producer)],
        state: AlertState = "all",
    ) -> list[AlertView]:
        """The match's ops alerts, newest first (``open``: not acknowledged yet), each with a
        plain headline and what to do for the producer."""
        match_files(match_id)
        ledger = repo.ledger(match_id)
        found = [a for a in ledger.alerts(match_id)
                 if state == "all" or a.acknowledged_by is None]  # fmt: skip
        views = []
        for a in sorted(found, key=lambda a: (a.raised_at, a.alert_id), reverse=True):
            insight_id = next((r for r in a.refs if "-I" in r), None)
            trigger_id = next((r for r in a.refs if "-T" in r), None)
            brief = explain_alert(
                a,
                ledger.get_insight(insight_id) if insight_id else None,
                ledger.audits(insight_id) if insight_id else [],
                ledger.get_trigger(trigger_id) if trigger_id else None,
            )
            views.append(AlertView(**a.model_dump(), headline=brief.headline,
                                   action=brief.action, needs_action=brief.needs_action,
                                   details=brief.details))  # fmt: skip
        return views

    @app.post("/api/v1/alerts/{alert_id}/acknowledge", response_model=OpsAlert)
    def acknowledge(alert_id: AlertIdPath, who: Annotated[Producer, Depends(producer)]) -> OpsAlert:
        """Mark an alert as seen; the producer comes from the verified token."""
        match_id = alert_id.split("-X")[0]
        match_files(match_id)
        ledger = repo.ledger(match_id)
        alert = OpsDesk(ledger, notifier=push).acknowledge(alert_id, who.producer_id)
        if alert is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no alert {alert_id}")
        if isinstance(ledger, FileLedger):
            ledger.flush()
        return alert

    return app
