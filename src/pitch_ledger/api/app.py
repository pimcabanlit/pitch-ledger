"""FastAPI app: ``/api/v1``.

Public (fan view): health, matches, a match, the aired overlays of a match, and the story
and broadcast package of an aired insight (no producer identity).
Producer (Entra ID producer role, see ``auth``): every insight with its status, one insight
with its audit trail and evidence, the approve/block decision (rate-limited: an approval
runs the Personalizer and Translator), and the match's ops alerts with acknowledgement.

Run: ``pitch-ledger api`` (Uvicorn).
"""

import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.middleware.cors import CORSMiddleware

from pitch_ledger.agents.replay import decide_insight
from pitch_ledger.api.auth import EntraVerifier, Producer, producer
from pitch_ledger.api.repository import MatchRepository
from pitch_ledger.api.schemas import (
    AlertState,
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
from pitch_ledger.api.story import build_broadcast, build_story, evidence_events
from pitch_ledger.eventstore.rows import insight_status
from pitch_ledger.feed.live import match_minute
from pitch_ledger.infra_adapters.ledger_queue import LedgerQueueSettings
from pitch_ledger.infra_adapters.webpubsub import PushSettings, open_notifier
from pitch_ledger.ledger import FileLedger, InMemoryLedger
from pitch_ledger.matchlab.io import MatchFiles
from pitch_ledger.ops import OpsDesk
from pitch_ledger.push import Notifier
from pitch_ledger.schema import OpsAlert, Overlay

MatchIdPath = Annotated[str, Path(pattern=r"^M\d{4}$")]
InsightIdPath = Annotated[str, Path(pattern=r"^M\d{4}-I\d{4}$")]
AlertIdPath = Annotated[str, Path(pattern=r"^M\d{4}-X\d{4}$")]
Mode = Annotated[str | None, Query(pattern=r"^[a-z_]{3,20}$")]
Language = Annotated[str | None, Query(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")]


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
) -> FastAPI:
    settings = settings or ApiSettings()
    repo = repository or MatchRepository(settings.data_dir, LedgerQueueSettings())
    tenant, audience = settings.api_tenant_id, settings.api_audience
    if verifier is None and settings.api_auth == "entra" and tenant and audience:
        verifier = EntraVerifier(tenant, audience, settings.api_producer_role)
    app = FastAPI(title="Pitch Ledger API", version="1", docs_url="/api/v1/docs",
                  openapi_url="/api/v1/openapi.json")  # fmt: skip
    app.state.settings, app.state.verifier = settings, verifier
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    limiter = RateLimiter(settings.api_decisions_per_minute)
    push: Notifier = notifier or open_notifier(PushSettings())

    def match_files(match_id: str) -> MatchFiles:
        files = repo.files(match_id)
        if files is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no match {match_id}")
        return files

    def summary(files: MatchFiles) -> MatchSummary:
        teams = {t.team_id: t for t in files.teams}
        m = files.match
        return MatchSummary(match_id=m.match_id, kickoff_utc=m.kickoff_utc, venue=m.venue,
                            home=team_view(teams[m.home_team_id]),
                            away=team_view(teams[m.away_team_id]))  # fmt: skip

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
        return [summary(f) for mid in repo.match_ids() if (f := repo.files(mid, False))]

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
            score_home=last["score_home"] if last else None,
            score_away=last["score_away"] if last else None,
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
        return build_story(summary(files), view.insight, trigger, ledger.audits(insight_id),
                           ledger.overlays(insight_id),
                           {e["event_id"]: e for e in files.events})  # fmt: skip

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
        return InsightDetail(**view.model_dump(), audits=ledger.audits(insight_id),
                             evidence=evidence, overlays=ledger.overlays(insight_id))  # fmt: skip

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
        outcome = await decide_insight(match_dir, ledger, insight_id, who.producer_id,
                                       body.approve, body.reason,
                                       provider=settings.api_decision_provider,
                                       notifier=push)  # fmt: skip
        decided = ledger.get_insight(insight_id) or view.insight
        signal = {"insight_id": insight_id, "status": insight_status(decided)}
        push.notify(decided.match_id, "producers", "insight_changed", signal)
        if signal["status"] == "aired":
            push.notify(decided.match_id, "fans", "overlays_changed", signal)
        return DecisionResult(insight_id=insight_id, status=insight_status(decided),
                              audit_status=outcome.audit_status, overlays=outcome.overlays,
                              approved_by=decided.approved_by)  # fmt: skip

    @app.get("/api/v1/matches/{match_id}/alerts", response_model=list[OpsAlert])
    def alerts(
        match_id: MatchIdPath,
        who: Annotated[Producer, Depends(producer)],
        state: AlertState = "all",
    ) -> list[OpsAlert]:
        """The match's ops alerts, newest first (``open``: not acknowledged yet)."""
        match_files(match_id)
        found = [a for a in repo.ledger(match_id).alerts(match_id)
                 if state == "all" or a.acknowledged_by is None]  # fmt: skip
        return sorted(found, key=lambda a: (a.raised_at, a.alert_id), reverse=True)

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
