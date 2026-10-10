"""Request and response contracts between the orchestrator and the agents.

Every request is sent to the model as JSON; every reply must be JSON matching the response
model. Numbers never travel from a model into the ledger: a draft names the trigger
metrics it relies on (``metric_keys``) and code copies their values.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pitch_ledger.schema import Trigger


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Facts(_Model):
    """Names and context the agents may use alongside the trigger's metrics."""

    home_team_id: str
    away_team_id: str
    team_names: dict[str, str]
    player_names: dict[str, str]
    subject_team_id: str | None = None
    opponent_team_id: str | None = None
    subject_player_id: str | None = None
    focus_player_id: str | None = None
    """For player-focus copy about a team moment: the subject team's most involved player."""
    signals: list[str] = Field(default_factory=list)
    """Context chosen in code from the metrics (e.g. 'state: puts X ahead'): words a model
    may use to say why the moment matters; every number in them is a metric."""

    def names(self) -> dict[str, str]:
        """Template name placeholders: team, opp, player, home, away, and side (whether the
        subject team is home or away)."""
        out = {
            "home": self.team_names[self.home_team_id],
            "away": self.team_names[self.away_team_id],
        }
        if self.subject_team_id:
            out["team"] = self.team_names[self.subject_team_id]
            out["side"] = "home" if self.subject_team_id == self.home_team_id else "away"
        if self.opponent_team_id:
            out["opp"] = self.team_names[self.opponent_team_id]
        player = self.subject_player_id or self.focus_player_id
        if player:
            out["player"] = self.player_names[player]
        return out


class ExplainRequest(_Model):
    """Explainer (or Narrator, for recaps): write the insight for a trigger."""

    task: Literal["explain", "narrate"] = "explain"
    copy_key: str
    trigger: Trigger
    facts: Facts
    feedback: list[str] = Field(default_factory=list)
    """Auditor findings on the previous draft, to fix in this one."""


class InsightDraft(_Model):
    headline: str
    explanation: str
    metric_keys: list[str]
    evidence_event_ids: list[str]


class ReviewRequest(_Model):
    """Auditor's model review: claims, tone and neutrality (numbers are checked in code)."""

    task: Literal["review"] = "review"
    draft: InsightDraft
    metrics: dict[str, int | float]
    facts: Facts


class ReviewVerdict(_Model):
    verdict: Literal["pass", "revise"]
    issues: list[str] = Field(default_factory=list)


class PersonalizeRequest(_Model):
    """Personalizer: the approved insight as on-screen copy for each mode, in one call."""

    task: Literal["personalize"] = "personalize"
    copy_key: str
    modes: list[str]
    headline: str
    explanation: str
    metrics: dict[str, int | float]
    facts: Facts


class TranslateRequest(_Model):
    """Translator: one mode's English copy into each target language, in one call."""

    task: Literal["translate"] = "translate"
    copy_key: str
    mode: str
    languages: list[str]
    text: str
    metrics: dict[str, int | float]
    facts: Facts


class CopyText(_Model):
    text: str


class CopySet(_Model):
    """Several copies in one reply, keyed by audience mode or by language."""

    texts: dict[str, str]


class PingRequest(_Model):
    """A one-word connectivity check (``check-models``)."""

    task: Literal["ping"] = "ping"
    reply_with: str = "ok"
