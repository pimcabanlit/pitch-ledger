"""Storyteller: the "Tell me more" beats for one aired insight, in two model calls.

The Narrator rewrites the English template beats (buildup and outlook, plain and numbers
depth) as broadcast prose in one call; the Translator renders them in the other languages
in one call. Every beat passes the story checks (``agents.story.beat_problems``: numbers
from the computed facts only, the form rules, the plain depth's figure limit); a failing
beat is asked for again on its own with the reasons, up to twice, then the template beat
airs instead.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pitch_ledger.agents import prompts
from pitch_ledger.agents.contracts import StoryRequest, TranslateStoryRequest
from pitch_ledger.agents.copy_check import CheckedCopy, checked_set
from pitch_ledger.agents.model_manager import ModelManager
from pitch_ledger.agents.story import (
    BEATS,
    DEPTHS,
    StoryFacts,
    beat_problems,
    fact_sheet,
    template_beats,
)
from pitch_ledger.schema import Insight, StoryBeats, StoryRecord

KEYS = tuple(f"{depth}.{beat}" for depth in DEPTHS for beat in BEATS)
STORY_TOKENS = 900  # four beats of up to 360 characters, as JSON
TRANSLATE_TOKENS = 1800  # eight beats (two languages) of up to 420 characters


@dataclass
class ToldStory:
    record: StoryRecord
    fallbacks: list[str] = field(default_factory=list)
    """One line per beat that aired as its template, with why (``plain_buildup/es: ...``)."""


def _split(key: str) -> tuple[str, str, str]:
    """``"es:plain.buildup"`` (or ``"plain.buildup"`` for English) -> language, depth, beat."""
    language, _, rest = key.rpartition(":")
    depth, beat = rest.split(".")
    return language or "en", depth, beat


async def tell(
    models: ModelManager,
    copy_key: str,
    insight: Insight,
    signals: Sequence[str],
    facts: StoryFacts,
    languages: Sequence[str],
) -> ToldStory:
    """Every depth's buildup and outlook in every language, checked beat by beat."""
    templates = {
        lang: {
            f"{depth}.{beat}": text
            for depth in DEPTHS
            for beat, text in template_beats(facts, depth, lang).items()
        }
        for lang in languages
    }
    sheet = fact_sheet(facts)

    def check(key: str, text: str) -> list[str]:
        language, depth, _ = _split(key)
        return beat_problems(text, facts, "numbers" if depth == "numbers" else "plain", language)

    english: dict[str, CheckedCopy] = {}
    wanted_en = {k: "en" for k in KEYS if templates.get("en", {}).get(k)}
    if wanted_en:
        english = await checked_set(
            models,
            "narrator",
            prompts.STORYTELLER,
            lambda keys, feedback: StoryRequest(
                copy_key=copy_key,
                headline=insight.headline,
                explanation=insight.explanation,
                facts=sheet,
                signals=list(signals),
                drafts={k: templates["en"][k] for k in keys},
                feedback=feedback,
            ),
            wanted_en,
            {},
            templates["en"],
            problems=check,
            max_tokens=STORY_TOKENS,
        )

    others = [lang for lang in languages if lang != "en"]
    wanted = {f"{lang}:{k}": lang for lang in others for k in english if templates[lang][k]}
    translated: dict[str, CheckedCopy] = {}
    if wanted:
        translated = await checked_set(
            models,
            "translator",
            prompts.STORY_TRANSLATOR,
            lambda keys, feedback: TranslateStoryRequest(
                texts={k: english[k.split(":")[1]].text for k in keys},
                drafts={k: templates[wanted[k]][k.split(":")[1]] for k in keys},
                facts=sheet,
                feedback=feedback,
            ),
            wanted,
            {},
            {k: templates[wanted[k]][k.split(":")[1]] for k in wanted},
            problems=check,
            max_tokens=TRANSLATE_TOKENS,
        )

    beats = []
    fallbacks = []
    for lang in languages:
        done = english if lang == "en" else {k.split(":")[1]: v for k, v in translated.items()
                                             if k.startswith(f"{lang}:")}  # fmt: skip
        for depth in DEPTHS:
            texts: dict[str, str] = {}
            sources: dict[str, str] = {}
            for beat in BEATS:
                key = f"{depth}.{beat}"
                got = done.get(key)
                texts[beat] = got.text if got else templates[lang][key]
                sources[beat] = got.source if got else "template"
                if got and got.source == "template" and got.findings:
                    fallbacks.append(f"{depth}_{beat}/{lang}: " + "; ".join(got.findings))
            beats.append(StoryBeats.model_validate(
                {"depth": depth, "language": lang, **texts, "sources": sources}))  # fmt: skip
    record = StoryRecord(
        insight_id=insight.insight_id,
        match_id=insight.match_id,
        beats=beats,
        model_version=models.version,
        written_at=datetime.now(UTC),
    )
    return ToldStory(record, fallbacks)
