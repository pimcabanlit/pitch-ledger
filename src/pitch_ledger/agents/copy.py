"""Copy templates: deterministic text for every processed trigger rule, audience mode and
language.

They serve two purposes: the offline model renders them (local runs and tests at zero
cost), and in production they are the fallback when a model call fails or its output
does not pass the number or editorial checks. Every number comes from the trigger's
code-computed metrics; names come from the match roster; context words (is the goal an
equaliser, did the opponent turn a spell into shots) come from ``phrases``, chosen in
code from the same metrics. ``{name:0}`` rounds a number to whole units.

Each insight says what happened, then why it matters. Modes differ in depth, not facts:
analyst (the numbers and the comparison), casual (plain words, one figure), club_focus
(the subject team's supporters), player_focus (the player's part), single_metric (one
labelled number).
"""

import re
import string
from collections.abc import Mapping
from dataclasses import dataclass

from pitch_ledger.agents import phrases
from pitch_ledger.schema import Trigger
from pitch_ledger.schema.enums import AudienceMode

LANGUAGES = ("en", "es", "fil")
MODES = tuple(m.value for m in AudienceMode)
Lang = dict[str, str]


@dataclass(frozen=True)
class RuleCopy:
    headline: str
    explanation: str
    modes: dict[str, Lang]  # mode -> language -> template
    player_fallback: Lang | None = None
    """player_focus copy for a team moment with no player in its evidence."""
    requires: tuple[str, ...] = ()
    """Metrics this variant cannot do without (besides those its explanation names)."""


def _l(en: str, es: str, fil: str) -> Lang:
    return {"en": en, "es": es, "fil": fil}


def _modes(**by_mode: Lang) -> dict[str, Lang]:
    missing = set(MODES) - set(by_mode)
    if missing:
        raise ValueError(f"missing modes: {missing}")
    return by_mode


def _recap(label_en: str, short_en: str, label_es: str, short_es: str, label_fil: str) -> RuleCopy:
    score = "{home} {score_home}-{score_away} {away}"
    ft = short_en == "FT"
    tail = "{second_half}" if ft else "{possession_note}"
    verdict, casual = (
        ("{verdict_ft}", "{casual_verdict_ft}") if ft else ("{verdict}", "{casual_verdict}")
    )
    return RuleCopy(
        headline=f"{label_en}: {score}",
        explanation=f"{verdict} {tail}",
        modes=_modes(
            analyst=_l(
                f"{short_en} {score} | shots {{shots_home}}-{{shots_away}}{{on_target_pair}} | xG "
                "{xg_home}-{xg_away} | possession {possession_home:0}%-{possession_away:0}%",
                f"{short_es} {score} | tiros {{shots_home}}-{{shots_away}}{{on_target_pair}} | xG "
                "{xg_home}-{xg_away} | posesión {possession_home:0} %-{possession_away:0} %",
                f"{short_en} {score} | tira {{shots_home}}-{{shots_away}}{{on_target_pair}} | xG "
                "{xg_home}-{xg_away} | possession {possession_home:0}%-{possession_away:0}%",
            ),
            casual=_l(
                f"{label_en}: {score}.{casual}",
                f"{label_es}: {score}.{casual}",
                f"{label_fil}: {score}.{casual}",
            ),
            club_focus=_l(
                f"{label_en} for {{home}}: {{score_home}}-{{score_away}} against {{away}}, "
                "with {possession_home:0}% of the ball.",
                f"{label_es} para {{home}}: {{score_home}}-{{score_away}} ante {{away}}, "
                "con el {possession_home:0} % del balón.",
                f"{label_fil} para sa {{home}}: {{score_home}}-{{score_away}} kontra "
                "{away}, hawak ang bola {possession_home:0}% ng oras.",
            ),
            player_focus=_l(
                f"{label_en}: {score}, {{shots_home}} shots to {{shots_away}}.",
                f"{label_es}: {score}, {{shots_home}} tiros contra {{shots_away}}.",
                f"{label_fil}: {score}, {{shots_home}} tira laban sa {{shots_away}}.",
            ),
            single_metric=_l(
                f"{short_en} xG: {{xg_home}}-{{xg_away}}",
                f"xG al {label_es.lower()}: {{xg_home}}-{{xg_away}}",
                f"{short_en} xG: {{xg_home}}-{{xg_away}}",
            ),
        ),
    )


CATALOGUE: dict[str, RuleCopy] = {
    "momentum_shift/build_up": RuleCopy(
        headline="{team} lose control: {control_before} to {control_after} since {onset_minute:0}'",
        explanation=(
            "Since {onset_minute:0}', {team} have made {turnovers_after} turnovers, "
            "{own_half_turnovers_after} in their own half, and completed "
            "{pass_accuracy_after:0}% of passes against {expected_pass_accuracy_after:0}% "
            "expected for their difficulty. {shots_outcome}"
        ),
        modes=_modes(
            analyst=_l(
                "{team} control {control_before} → {control_after} since {onset_minute:0}'. "
                "Passing {pass_accuracy_after:0}% v {expected_pass_accuracy_after:0}% expected; "
                "{turnovers_after} turnovers ({own_half_turnovers_after} own half).",
                "{team}: control {control_before} → {control_after} desde el {onset_minute:0}'. "
                "Pase {pass_accuracy_after:0} % frente a {expected_pass_accuracy_after:0} % "
                "esperado; {turnovers_after} pérdidas ({own_half_turnovers_after} en campo "
                "propio).",
                "{team}: control {control_before} → {control_after} mula {onset_minute:0}'. "
                "Pasa {pass_accuracy_after:0}% vs {expected_pass_accuracy_after:0}% inaasahan; "
                "{turnovers_after} turnover ({own_half_turnovers_after} sa sariling half).",
            ),
            casual=_l(
                "{team} can't keep hold of the ball: {turnovers_after} giveaways in this "
                "spell{casual_outcome}.",
                "{team} no retiene el balón: {turnovers_after} pérdidas en este tramo"
                "{casual_outcome}.",
                "Hirap hawakan ng {team} ang bola: {turnovers_after} beses nawalan sa yugtong "
                "ito{casual_outcome}.",
            ),
            club_focus=_l(
                "Worrying spell for {team}: control down from {control_before} to "
                "{control_after}{club_outcome}.",
                "Mal momento para {team}: el control cae de {control_before} a "
                "{control_after}{club_outcome}.",
                "Mabigat na yugto para sa {team}: bumaba ang control mula {control_before} "
                "hanggang {control_after}{club_outcome}.",
            ),
            player_focus=_l(
                "{player} is {team}'s busiest player in a spell where control fell from "
                "{control_before} to {control_after}.",
                "{player} es el jugador más activo de {team} mientras su control cae de "
                "{control_before} a {control_after}.",
                "Si {player} ang pinakaabala sa {team} habang bumababa ang control mula "
                "{control_before} hanggang {control_after}.",
            ),
            single_metric=_l(
                "{team} control: {control_before} → {control_after}",
                "Control de {team}: {control_before} → {control_after}",
                "Control ng {team}: {control_before} → {control_after}",
            ),
        ),
        player_fallback=_l(
            "{team}'s players are struggling to keep the ball: control {control_before} to "
            "{control_after}.",
            "A los jugadores de {team} les cuesta conservar el balón: control "
            "{control_before} a {control_after}.",
            "Hirap ang mga manlalaro ng {team} na panatilihin ang bola: control "
            "{control_before} hanggang {control_after}.",
        ),
    ),
    "momentum_shift/pressing": RuleCopy(
        headline="{opp}'s press squeezes {team}: control {control_before} to {control_after}",
        explanation=(
            "Since {onset_minute:0}', {ppda_change}, and {team} have lost the ball "
            "{own_half_turnovers_after} times in their own half. {shots_outcome}"
        ),
        modes=_modes(
            analyst=_l(
                "{opp} PPDA {opponent_ppda_before} → {opponent_ppda_after} since "
                "{onset_minute:0}'. {team} control {control_before} → {control_after}, "
                "{own_half_turnovers_after} own-half turnovers.",
                "PPDA de {opp} {opponent_ppda_before} → {opponent_ppda_after} desde el "
                "{onset_minute:0}'. Control de {team} {control_before} → {control_after}, "
                "{own_half_turnovers_after} pérdidas en campo propio.",
                "PPDA ng {opp} {opponent_ppda_before} → {opponent_ppda_after} mula "
                "{onset_minute:0}'. Control ng {team} {control_before} → {control_after}, "
                "{own_half_turnovers_after} turnover sa sariling half.",
            ),
            casual=_l(
                "{opp} are pressing high and {team} can't get out{casual_outcome}.",
                "{opp} presiona arriba y {team} no consigue salir{casual_outcome}.",
                "Mataas ang press ng {opp} at hindi makalabas ang {team}{casual_outcome}.",
            ),
            club_focus=_l(
                "{team} are pinned back: control down to {control_after} under {opp}'s "
                "press{club_outcome}.",
                "{team}, encerrado: su control baja a {control_after} ante la presión de "
                "{opp}{club_outcome}.",
                "Naiipit ang {team}: bumaba sa {control_after} ang control habang mataas ang "
                "press ng {opp}{club_outcome}.",
            ),
            player_focus=_l(
                "{player} is {team}'s busiest player while {opp}'s press cuts control from "
                "{control_before} to {control_after}.",
                "{player} es el más activo de {team} mientras la presión de {opp} baja su "
                "control de {control_before} a {control_after}.",
                "Si {player} ang pinakaabala sa {team} habang ibinababa ng press ng {opp} ang "
                "control mula {control_before} hanggang {control_after}.",
            ),
            single_metric=_l(
                "{opp} PPDA: {opponent_ppda_before} → {opponent_ppda_after}",
                "PPDA de {opp}: {opponent_ppda_before} → {opponent_ppda_after}",
                "PPDA ng {opp}: {opponent_ppda_before} → {opponent_ppda_after}",
            ),
        ),
        player_fallback=_l(
            "{team}'s players are being hurried by {opp}'s press: control {control_before} to "
            "{control_after}.",
            "La presión de {opp} acelera a los jugadores de {team}: control {control_before} "
            "a {control_after}.",
            "Minamadali ng press ng {opp} ang mga manlalaro ng {team}: control "
            "{control_before} hanggang {control_after}.",
        ),
        requires=("opponent_ppda_before", "opponent_ppda_after"),
    ),
    "goal": RuleCopy(
        headline="{goal_headline}",
        explanation="{player} scores on {minute:0}' from a {xg} xG chance{move}. {goal_context}",
        modes=_modes(
            analyst=_l(
                "{player} {state}: {score_home}-{score_away}, {minute:0}'. Chance worth {xg} "
                "xG.{match_xg}",
                "{player} {state}: {score_home}-{score_away}, {minute:0}'. Ocasión de {xg} "
                "xG.{match_xg}",
                "Si {player} ay {state}: {score_home}-{score_away}, {minute:0}'. Tsansang {xg} "
                "xG.{match_xg}",
            ),
            casual=_l(
                "{player} {state}{casual_tally}! {home} {score_home}-{score_away} {away}.",
                "¡{player} {state}{casual_tally}! {home} {score_home}-{score_away} {away}.",
                "Si {player} ay {state}{casual_tally}! {home} {score_home}-{score_away} {away}.",
            ),
            club_focus=_l("{club_goal}", "{club_goal}", "{club_goal}"),
            player_focus=_l(
                "{player}: {player_goal}", "{player}: {player_goal}", "{player}: {player_goal}"
            ),
            single_metric=_l(
                "{home} {score_home}-{score_away} {away}",
                "{home} {score_home}-{score_away} {away}",
                "{home} {score_home}-{score_away} {away}",
            ),
        ),
    ),
    "sending_off": RuleCopy(
        headline="{player} sent off, {team} down to {players_left}",
        explanation=(
            "{player} is sent off on {minute:0}'{score_note}. {team} have {players_left} "
            "players{time_left}."
        ),
        modes=_modes(
            analyst=_l(
                "Red card: {player} ({team}) {minute:0}'{score_note}. {team} down to "
                "{players_left}.",
                "Roja: {player} ({team}) en el {minute:0}'{score_note}. {team} se queda con "
                "{players_left}.",
                "Red card: {player} ({team}) {minute:0}'{score_note}. {players_left} na lang ang "
                "{team}.",
            ),
            casual=_l(
                "{player} is sent off! {team} are down to {players_left}.",
                "¡Expulsado {player}! {team} se queda con {players_left}.",
                "Pinalabas si {player}! {players_left} na lang ang {team}.",
            ),
            club_focus=_l(
                "Blow for {team}: {player} is off, {players_left} players{time_left}.",
                "Golpe para {team}: fuera {player}, {players_left} jugadores{time_left}.",
                "Dagok sa {team}: palabas si {player}, {players_left} manlalaro{time_left}.",
            ),
            player_focus=_l(
                "{player} sees red on {minute:0}', leaving {team} with {players_left}.",
                "{player} ve la roja en el {minute:0}' y deja a {team} con {players_left}.",
                "Red card kay {player} sa {minute:0}', {players_left} na lang ang {team}.",
            ),
            single_metric=_l(
                "{team} players: {players_left}",
                "Jugadores de {team}: {players_left}",
                "Manlalaro ng {team}: {players_left}",
            ),
        ),
    ),
    "sending_off_impact": RuleCopy(
        headline="{team} with {players_left}: possession {possession_before:0}% to "
        "{possession_after:0}%",
        explanation=(
            "In the {window_min} minutes since going down to {players_left}, {team} have "
            "{possession_after:0}% of the ball (it was {possession_before:0}%) and a control "
            "score of {control_after} (was {control_before}). {shots_outcome}"
        ),
        modes=_modes(
            analyst=_l(
                "{team} with {players_left}, last {window_min} min: possession "
                "{possession_before:0}% → {possession_after:0}%, control {control_before} → "
                "{control_after}, {turnovers_after} turnovers.",
                "{team} con {players_left}, últimos {window_min} min: posesión "
                "{possession_before:0} % → {possession_after:0} %, control {control_before} → "
                "{control_after}, {turnovers_after} pérdidas.",
                "{team} na may {players_left}, huling {window_min} min: possession "
                "{possession_before:0}% → {possession_after:0}%, control {control_before} → "
                "{control_after}, {turnovers_after} turnover.",
            ),
            casual=_l(
                "Down to {players_left}, {team} are {ball_share}{casual_outcome}.",
                "Con {players_left}, {team} está {ball_share}{casual_outcome}.",
                "Sa {players_left} manlalaro, ang {team} ay {ball_share}{casual_outcome}.",
            ),
            club_focus=_l(
                "{team} with {players_left}: {possession_after:0}% of the ball in the last "
                "{window_min} minutes{club_outcome}.",
                "{team} con {players_left}: {possession_after:0} % del balón en los últimos "
                "{window_min} minutos{club_outcome}.",
                "{team} na may {players_left}: {possession_after:0}% ng bola sa huling "
                "{window_min} minuto{club_outcome}.",
            ),
            player_focus=_l(
                "{player} is {team}'s busiest player since the red card: possession "
                "{possession_before:0}% to {possession_after:0}%.",
                "{player} es el más activo de {team} desde la roja: posesión "
                "{possession_before:0} % a {possession_after:0} %.",
                "Si {player} ang pinakaabala sa {team} mula sa red card: possession "
                "{possession_before:0}% hanggang {possession_after:0}%.",
            ),
            single_metric=_l(
                "{team} possession: {possession_before:0}% → {possession_after:0}%",
                "Posesión de {team}: {possession_before:0} % → {possession_after:0} %",
                "Possession ng {team}: {possession_before:0}% → {possession_after:0}%",
            ),
        ),
        player_fallback=_l(
            "{team}'s players with {players_left}: possession {possession_before:0}% to "
            "{possession_after:0}%.",
            "Los jugadores de {team} con {players_left}: posesión {possession_before:0} % a "
            "{possession_after:0} %.",
            "Mga manlalaro ng {team} na may {players_left}: possession {possession_before:0}% "
            "hanggang {possession_after:0}%.",
        ),
    ),
    "penalty_awarded": RuleCopy(
        headline="Penalty to {team}: {player} fouled{at_score}",
        explanation=("{player} is fouled in the box on {minute:0}'{score_note}. {pen_stakes}"),
        modes=_modes(
            analyst=_l(
                "Penalty: {team}, {minute:0}', {player} fouled{score_note}.",
                "Penalti: {team}, {minute:0}', falta sobre {player}{score_note}.",
                "Penalty: {team}, {minute:0}', na-foul si {player}{score_note}.",
            ),
            casual=_l(
                "Penalty! {player} is fouled and {team} have a big chance{casual_pen}.",
                "¡Penalti! Falta sobre {player} y {team} tiene una gran ocasión{casual_pen}.",
                "Penalty! Na-foul si {player} at may malaking tsansa ang {team}{casual_pen}.",
            ),
            club_focus=_l(
                "Big moment for {team}: a penalty on {minute:0}'{score_note}.",
                "Momento clave para {team}: penalti en el {minute:0}'{score_note}.",
                "Malaking sandali para sa {team}: penalty sa {minute:0}'{score_note}.",
            ),
            player_focus=_l(
                "{player} wins a penalty for {team} on {minute:0}'.",
                "{player} provoca un penalti para {team} en el {minute:0}'.",
                "Nakakuha si {player} ng penalty para sa {team} sa {minute:0}'.",
            ),
            single_metric=_l(
                "Penalty: {team} ({minute:0}')",
                "Penalti: {team} ({minute:0}')",
                "Penalty: {team} ({minute:0}')",
            ),
        ),
    ),
    "player_form": RuleCopy(
        headline="{player} has {shots} shots in {window_min} minutes{form_goals}",
        explanation=(
            "{player} has taken {shots} shots worth {npxg} xG in {window_min} minutes{trend}, "
            "{on_target} on target{goal_part}. {share_sentence}"
        ),
        modes=_modes(
            analyst=_l(
                "{player}: {shots} shots, {npxg} npxG, {on_target} on target in {window_min} "
                "min{trend}.",
                "{player}: {shots} tiros, {npxg} npxG, {on_target} a puerta en {window_min} "
                "min{trend}.",
                "{player}: {shots} tira, {npxg} npxG, {on_target} on target sa {window_min} "
                "min{trend}.",
            ),
            casual=_l(
                "{player} keeps shooting: {shots} attempts in this spell{casual_goal}.",
                "{player} no deja de disparar: {shots} intentos en este tramo{casual_goal}.",
                "Tira nang tira si {player}: {shots} subok sa yugtong ito{casual_goal}.",
            ),
            club_focus=_l("{club_form}", "{club_form}", "{club_form}"),
            player_focus=_l(
                "{player} has put {on_target} of {shots} shots on target in {window_min} "
                "minutes{goal_part}.",
                "{player} ha puesto {on_target} de {shots} tiros a puerta en {window_min} "
                "minutos{goal_part}.",
                "{on_target} sa {shots} tira ni {player} ang on target sa {window_min} "
                "minuto{goal_part}.",
            ),
            single_metric=_l(
                "{player} xG this spell: {npxg}",
                "xG de {player} en este tramo: {npxg}",
                "xG ni {player} sa yugtong ito: {npxg}",
            ),
        ),
    ),
    "half_time_recap": _recap("Half-time", "HT", "Descanso", "Descanso", "Half-time"),
    "full_time_recap": _recap("Full-time", "FT", "Final", "Final", "Full-time"),
    "shot_speed": RuleCopy(
        headline="{player} hits a {shot_speed_kmh} km/h shot",
        explanation="{player}'s shot leaves the boot at {shot_speed_kmh} km/h{speed_chance}.",
        modes=_modes(
            analyst=_l(
                "{player} ({team}) shot speed {shot_speed_kmh} km/h{speed_chance}.",
                "{player} ({team}): disparo a {shot_speed_kmh} km/h{speed_chance}.",
                "{player} ({team}) bilis ng tira {shot_speed_kmh} km/h{speed_chance}.",
            ),
            casual=_l(
                "Fierce strike from {player}: {shot_speed_kmh} km/h.",
                "Zapatazo de {player}: {shot_speed_kmh} km/h.",
                "Malakas na tira ni {player}: {shot_speed_kmh} km/h.",
            ),
            club_focus=_l(
                "{team}'s {player} lets fly at {shot_speed_kmh} km/h.",
                "{player}, de {team}, dispara a {shot_speed_kmh} km/h.",
                "Bumanat si {player} ng {team} sa {shot_speed_kmh} km/h.",
            ),
            player_focus=_l(
                "{player}'s shot clocked {shot_speed_kmh} km/h.",
                "El disparo de {player} alcanzó {shot_speed_kmh} km/h.",
                "Umabot sa {shot_speed_kmh} km/h ang tira ni {player}.",
            ),
            single_metric=_l(
                "Shot speed: {shot_speed_kmh} km/h",
                "Velocidad del disparo: {shot_speed_kmh} km/h",
                "Bilis ng tira: {shot_speed_kmh} km/h",
            ),
        ),
    ),
    "top_speed": RuleCopy(
        headline="{player} reaches {top_speed_kmh} km/h",
        explanation=(
            "{player} hits {top_speed_kmh} km/h, the fastest this player has run so far in the "
            "tracking data."
        ),
        modes=_modes(
            analyst=_l(
                "{player} ({team}) top speed {top_speed_kmh} km/h, from tracking.",
                "{player} ({team}): velocidad máxima {top_speed_kmh} km/h, según el tracking.",
                "{player} ({team}) top speed {top_speed_kmh} km/h, mula sa tracking.",
            ),
            casual=_l(
                "{player} is flying: {top_speed_kmh} km/h!",
                "¡{player} vuela: {top_speed_kmh} km/h!",
                "Lumilipad si {player}: {top_speed_kmh} km/h!",
            ),
            club_focus=_l(
                "{team}'s {player} sprints at {top_speed_kmh} km/h.",
                "{player}, de {team}, esprinta a {top_speed_kmh} km/h.",
                "Kumaripas si {player} ng {team} sa {top_speed_kmh} km/h.",
            ),
            player_focus=_l(
                "{player}'s fastest so far: {top_speed_kmh} km/h.",
                "Lo más rápido de {player} hasta ahora: {top_speed_kmh} km/h.",
                "Pinakamabilis ni {player} sa ngayon: {top_speed_kmh} km/h.",
            ),
            single_metric=_l(
                "Top speed: {top_speed_kmh} km/h",
                "Velocidad máxima: {top_speed_kmh} km/h",
                "Top speed: {top_speed_kmh} km/h",
            ),
        ),
    ),
}

NAME_FIELDS = frozenset({"team", "opp", "player", "home", "away", "side"})
"""Placeholders filled from the roster (``side``: the subject team is home or away), not
from metrics."""

FALLBACK: dict[str, str] = {"momentum_shift/pressing": "momentum_shift/build_up"}
"""When a variant's numbers are missing (e.g. PPDA undefined), use this variant instead."""

_FIELD = re.compile(r"\{(\w+)\}")


def copy_key(trigger: Trigger) -> str:
    """Catalogue key for a trigger: the rule, plus the detector for momentum shifts."""
    if trigger.rule_id == "momentum_shift":
        return f"momentum_shift/{trigger.context.get('detector', 'build_up')}"
    return trigger.rule_id


def supported(key: str) -> bool:
    return key in CATALOGUE


def _natural(value: float) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.10g}"


class _Formatter(string.Formatter):
    """Formats numbers for a language: ``{x:0}`` rounds to whole units; Spanish uses 6,2."""

    def __init__(self, language: str) -> None:
        self.language = language

    def format_field(self, value: object, format_spec: str) -> str:
        if isinstance(value, int | float) and not isinstance(value, bool):
            text = f"{value:.{int(format_spec)}f}" if format_spec else _natural(value)
            return text.replace(".", ",") if self.language == "es" else text
        return str(super().format_field(value, format_spec))


def expand(template: str, values: Mapping[str, object], language: str) -> str:
    """Replace phrase fields with the phrase chosen for these values (phrases may hold
    further placeholders, filled afterwards)."""
    for _ in range(4):
        found = False

        def sub(m: re.Match[str]) -> str:
            nonlocal found
            if m.group(1) in phrases.SELECTORS:
                found = True
                return phrases.choose(m.group(1), values, language)
            return m.group(0)

        template = _FIELD.sub(sub, template)
        if not found:
            break
    return template


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def fill(template: str, values: Mapping[str, object], language: str) -> str:
    """Render one template; raises KeyError when a placeholder has no value."""
    return _tidy(_Formatter(language).vformat(expand(template, values, language), (), dict(values)))


def template_fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def resolve_key(key: str, values: Mapping[str, object]) -> str:
    """The variant to use: falls back (e.g. pressing -> build-up) when a number the variant's
    explanation needs is missing, so headline, explanation and every mode stay consistent."""
    rule = CATALOGUE[key]
    needed = template_fields(expand(rule.explanation, values, "en")) - NAME_FIELDS
    needed |= set(rule.requires)
    if not needed <= set(values) and key in FALLBACK:
        return resolve_key(FALLBACK[key], values)
    return key


def headline(key: str, values: Mapping[str, object]) -> str:
    return fill(CATALOGUE[resolve_key(key, values)].headline, values, "en")


def explanation(key: str, values: Mapping[str, object]) -> str:
    return fill(CATALOGUE[resolve_key(key, values)].explanation, values, "en")


def mode_template(key: str, mode: str, language: str, values: Mapping[str, object]) -> str:
    rule = CATALOGUE[resolve_key(key, values)]
    template = rule.modes[mode][language]
    if mode == "player_focus" and "player" not in values and rule.player_fallback:
        template = rule.player_fallback[language]
    return template


def mode_text(key: str, mode: str, language: str, values: Mapping[str, object]) -> str:
    return fill(mode_template(key, mode, language, values), values, language)


def used_metrics(
    key: str, metrics: Mapping[str, float], values: Mapping[str, object] | None = None
) -> list[str]:
    """Metric names the headline and explanation use (after any fallback and phrase
    choice; pass the full ``values`` so phrases that depend on names choose as rendered)."""
    values = values if values is not None else metrics
    rule = CATALOGUE[resolve_key(key, values)]
    text = expand(rule.headline, values, "en") + " " + expand(rule.explanation, values, "en")
    return sorted((template_fields(text) - NAME_FIELDS) & set(metrics))


def signals(key: str, values: Mapping[str, object]) -> list[str]:
    """Context a model may use, in English, chosen and filled in code: the match state a
    goal creates, the scorer's tally, what the opponent did with a spell, and so on."""
    if key not in CATALOGUE:
        return []
    rule = CATALOGUE[resolve_key(key, values)]
    used = " ".join([rule.headline, rule.explanation, *(m["en"] for m in rule.modes.values())])
    out = []
    for name in phrases.SIGNALS:
        if "{" + name + "}" not in used:
            continue
        try:
            text = fill("{" + name + "}", values, "en")
        except (KeyError, ValueError):
            continue
        if text:
            out.append(f"{name}: {text}")
    return out
