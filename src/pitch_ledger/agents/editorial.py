"""Editorial check: form rules every aired text must meet, whoever wrote it.

Numbers are checked elsewhere (INS-01 and the copy number check). This module checks
the words: length for the surface they appear on, clichés and empty intensifiers, claims
the data cannot support (history, seasons, records, injuries, certainty about causes or
the future) and repetition. Templates must pass it; model drafts that fail it are sent
back for revision, and model copy that fails it falls back to the template.
"""

import re
from typing import Literal

Kind = Literal["headline", "explanation", "overlay"]

HEADLINE_MAX_WORDS = 10
EXPLANATION_MAX_SENTENCES = 2
EXPLANATION_MAX_CHARS = 320
OVERLAY_MAX_CHARS = {"en": 140}
OVERLAY_MAX_CHARS_OTHER = 160  # Spanish and Filipino run longer than English

CLICHES = (
    "showcas", "testament", "masterclass", "thrilling", "statement win", "game-changer",
    "game changer", "electrifying", "sends a message", "make no mistake", "remains to be seen",
    "only time will tell", "unbelievable", "incredible", "stunning", "superb", "brilliant",
    "world-class", "world class", "on fire", "dominant display", "edge of their seats",
    "what a", "rocket", "dominan", "surge", "big time", "momentum loss", "key moment",
    "central role",
)  # fmt: skip
"""Commentary filler: says nothing the numbers do not, or oversells them (English)."""

UNSUPPORTED = {
    "en": (
        r"\brecords?\b", r"\bfirst time\b", r"\bcareer\b", r"\bhistor",
        r"\bbecause of\b", r"\bdue to\b", r"\bcaused\b", r"\binjur", r"\balways\b",
        r"\bnever\b", r"\bwill (?:win|lose|score|go on)\b", r"\bcertainly\b",
        r"\bdefinitely\b", r"\bfans will\b",
    ),
    "es": (
        r"\br[ée]cords?\b", r"\bhist[óo]ric", r"\bdebido a\b",
        r"\blesi[óo]n", r"\bsiempre\b", r"\bnunca\b",
    ),
    "fil": (r"\brecord\b", r"\bdahil sa\b", r"\bkasaysayan\b", r"\bpalagi\b"),
}  # fmt: skip
"""Claims the data cannot back: there is no data on records, careers, earlier seasons or
injuries, and an insight may describe what coincided, not assert what caused it or what will
happen."""

SEASON_WORDS = {"en": r"\bseasons?\b", "es": r"\btemporadas?\b", "fil": r"\bseason\b"}
"""The season may be mentioned only when the insight carries season facts (metrics from this
season's earlier matches, ``engine.season``)."""

FORM_RULES = {
    "en": (
        (r"\d+[.,]\d+\s*(?:'|minutes?\b|mins?\b)", "write minutes whole, like 77'"),
        (r"\b\d+(?:st|nd|rd|th)\b", "no ordinals; write minutes like 77'"),
        (r"\bcontrol\b[^.;]{0,40}?\d+(?:[.,]\d+)?\s*%", "the control score is an index, not a %"),
        (r"\b(?:we|our|us)\b", "no first person; the broadcaster stays neutral"),
    ),
}
"""House style the number check cannot see: how minutes and the control score are written,
and no first person (club copy speaks to supporters, not as one)."""

MODE_MAX_FIGURES = {"casual": 1, "single_metric": 1}
"""On-screen figures per mode (a score, or a before → after pair, counts as one)."""

_PAIR = re.compile(
    r"\d+(?:[.,]\d+)?\s*%?"
    r"(?:\s*(?:-|\u2013|→)\s*"
    r"|\s+(?:(?:pababa|pataas|paakyat)\s+)?(?:to|a|al|sa|hanggang)\s+)"  # en, es, fil words
    r"\d+(?:[.,]\d+)?"
)
_FIGURE = re.compile(r"\d+(?:[.,]\d+)?")


def figures(text: str) -> int:
    """Numbers on screen, a score or a before → after pair counting as one."""
    return len(_FIGURE.findall(_PAIR.sub("#", text).replace("#", "0")))


# A sentence ends at . ! or ? followed by a space or the end, unless the dot closes an
# initial ("L. Haugland") or sits inside a number (0.15).
_SENTENCE_END = re.compile(r"(?<!\b[A-Z])(?<!\d(?=\d))[.!?](?=\s|$)")
_WORD = re.compile(r"\S+")


def sentences(text: str) -> list[str]:
    parts, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        parts.append(text[start : m.end()].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


def editorial_issues(
    text: str, kind: Kind, language: str = "en", season: bool = False
) -> list[str]:
    """Form problems in one text; an empty list means it may air (``season``: the insight
    carries season facts, so the season may be mentioned)."""
    issues: list[str] = []
    stripped = text.strip()
    if not stripped:
        return [f"STYLE: empty {kind}"]
    if kind == "headline":
        words = len(_WORD.findall(stripped))
        if words > HEADLINE_MAX_WORDS:
            issues.append(f"STYLE: headline has {words} words (max {HEADLINE_MAX_WORDS})")
    elif kind == "explanation":
        n = len(sentences(stripped))
        if n > EXPLANATION_MAX_SENTENCES:
            issues.append(f"STYLE: explanation has {n} sentences (max {EXPLANATION_MAX_SENTENCES})")
        if len(stripped) > EXPLANATION_MAX_CHARS:
            issues.append(
                f"STYLE: explanation has {len(stripped)} characters (max {EXPLANATION_MAX_CHARS})"
            )
    else:
        limit = OVERLAY_MAX_CHARS.get(language, OVERLAY_MAX_CHARS_OTHER)
        if len(stripped) > limit:
            issues.append(f"STYLE: on-screen text has {len(stripped)} characters (max {limit})")
    low = stripped.lower()
    if language == "en":
        issues += [f"STYLE: cliché '{c}'" for c in CLICHES if c in low]
    for pattern, why in FORM_RULES.get(language, ()):
        if re.search(pattern, stripped, re.IGNORECASE):
            issues.append(f"STYLE: {why}")
    patterns = list(UNSUPPORTED.get(language, ()))
    if not season and language in SEASON_WORDS:
        patterns.append(SEASON_WORDS[language])
    for pattern in patterns:
        if m := re.search(pattern, low):
            issues.append(f"STYLE: unsupported claim '{m.group(0)}' (no such data)")
    seen: set[str] = set()
    for s in sentences(stripped):
        key = _norm(s)
        if key and key in seen:
            issues.append("STYLE: repeated sentence")
            break
        seen.add(key)
    return issues


def mode_issues(text: str, mode: str) -> list[str]:
    """Depth rules per audience mode: casual and single-metric copy carry one figure."""
    limit = MODE_MAX_FIGURES.get(mode)
    n = figures(text)
    if limit is not None and n > limit:
        return [f"STYLE: {mode} copy has {n} figures (max {limit})"]
    return []


def draft_issues(headline: str, explanation: str, season: bool = False) -> list[str]:
    """Headline and explanation rules, plus: the explanation must not restate the headline."""
    issues = editorial_issues(headline, "headline", season=season)
    issues += editorial_issues(explanation, "explanation", season=season)
    head = _norm(headline)
    if head and head in _norm(explanation):
        issues.append("STYLE: the explanation repeats the headline")
    return issues


def is_style(finding: str) -> bool:
    return finding.startswith("STYLE:")
