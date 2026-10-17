"""The fan story behind "Tell me more": three short beats for one aired insight.

The buildup (what led to the moment), what just happened (the aired on-screen version for
the reader's audience mode and language, already number-checked) and what it could mean
(the stakes from here). The buildup and outlook come from the ledger's story record, which
the Narrator and Translator wrote and the story checks passed (``agents.storyteller``);
an insight without one (an auto-approved badge, or one aired before stories were written)
gets the template beats, built here from the same code-computed facts.

Depth follows the audience: the analyst story carries the figures; every other mode reads
in plain words.
"""

from collections.abc import Sequence

from pitch_ledger.agents.copy import LANGUAGES
from pitch_ledger.agents.story import (
    STORY_MODES,
    depth_of,
    story_facts,
    story_title,
    template_beats,
)
from pitch_ledger.api.match_centre import MatchData
from pitch_ledger.api.schemas import StoryNarrative
from pitch_ledger.schema import Insight, Overlay, StoryRecord, Trigger


def build_narratives(
    data: MatchData,
    insight: Insight,
    trigger: Trigger | None,
    overlays: Sequence[Overlay],
    stored: StoryRecord | None = None,
) -> list[StoryNarrative]:
    """One story per audience mode and language the insight aired in."""
    files = data.files
    facts = story_facts(files.match, files.teams, files.players, data.events, insight, trigger)
    texts = {(o.audience_mode.value, o.language): o.text for o in overlays}
    templates = {
        (depth, language): template_beats(facts, depth, language)
        for depth in {depth_of(m) for m in STORY_MODES}
        for language in LANGUAGES
    }
    out = []
    for mode in STORY_MODES:
        depth = depth_of(mode)
        for language in LANGUAGES:
            moment = texts.get((mode, language)) or texts.get(("casual", language))
            if moment is None:
                if language != "en":
                    continue  # never aired in this language
                moment = insight.explanation
            beats = stored.beats_for(depth, language) if stored else None
            told = (
                {"buildup": beats.buildup, "outlook": beats.outlook}
                if beats
                else templates[(depth, language)]
            )
            out.append(
                StoryNarrative(
                    mode=mode,
                    language=language,
                    title=story_title(facts, insight, language),
                    buildup=told["buildup"],
                    moment=moment,
                    outlook=told["outlook"],
                )
            )
    return out
