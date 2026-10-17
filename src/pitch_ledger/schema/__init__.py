"""Pydantic models and enums for every table and output object in docs/event-schema.md."""

from pitch_ledger.schema.audit import AuditRecord
from pitch_ledger.schema.details import (
    CardDetails,
    DuelDetails,
    EventDetails,
    FoulDetails,
    GoalkeeperDetails,
    NoDetails,
    PassDetails,
    PressureDetails,
    ShotDetails,
    SubstitutionDetails,
    details_model_for,
)
from pitch_ledger.schema.event import Event
from pitch_ledger.schema.insight import Insight, InsightSubject
from pitch_ledger.schema.ops import OpsAlert
from pitch_ledger.schema.overlay import Overlay
from pitch_ledger.schema.reference import Match, Player, Ratings, Team
from pitch_ledger.schema.scenario import Effect, ExpectedDetection, Scenario, ScenarioTrigger
from pitch_ledger.schema.story import StoryBeats, StoryRecord
from pitch_ledger.schema.tracking import TrackingSample
from pitch_ledger.schema.trigger import Trigger

__all__ = [
    "AuditRecord",
    "CardDetails",
    "DuelDetails",
    "Effect",
    "Event",
    "EventDetails",
    "ExpectedDetection",
    "FoulDetails",
    "GoalkeeperDetails",
    "Insight",
    "InsightSubject",
    "Match",
    "NoDetails",
    "OpsAlert",
    "Overlay",
    "PassDetails",
    "Player",
    "PressureDetails",
    "Ratings",
    "Scenario",
    "ScenarioTrigger",
    "ShotDetails",
    "StoryBeats",
    "StoryRecord",
    "SubstitutionDetails",
    "Team",
    "TrackingSample",
    "Trigger",
    "details_model_for",
]
