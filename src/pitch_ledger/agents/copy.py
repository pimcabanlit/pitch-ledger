"""Copy templates: deterministic text for every processed trigger rule, audience mode and
language.

They serve two purposes: the offline model renders them (local runs and tests at zero
cost), and in production they are the fallback when a model call fails or its output
does not pass the number check. Every number comes from the trigger's code-computed
metrics; names come from the match roster. ``{name:0}`` rounds a number to whole units.
"""

import string
from collections.abc import Mapping
from dataclasses import dataclass

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


def _modes(**by_mode: Lang) -> dict[str, Lang]:
    missing = set(MODES) - set(by_mode)
    if missing:
        raise ValueError(f"missing modes: {missing}")
    return by_mode


def _recap(label_en: str, short_en: str, label_es: str, short_es: str, label_fil: str) -> RuleCopy:
    score = "{home} {score_home}-{score_away} {away}"
    return RuleCopy(
        headline=f"{label_en}: {score}",
        explanation=(
            f"{score} at {label_en.lower()}. Shots {{shots_home}}-{{shots_away}}, xG "
            "{xg_home}-{xg_away}, possession {possession_home}%-{possession_away}%."
        ),
        modes=_modes(
            analyst={
                "en": f"{short_en} {score} | shots {{shots_home}}-{{shots_away}} | xG "
                "{xg_home}-{xg_away} | possession {possession_home}%-{possession_away}%",
                "es": f"{short_es} {score} | tiros {{shots_home}}-{{shots_away}} | xG "
                "{xg_home}-{xg_away} | posesión {possession_home} %-{possession_away} %",
                "fil": f"{short_en} {score} | tira {{shots_home}}-{{shots_away}} | xG "
                "{xg_home}-{xg_away} | possession {possession_home}%-{possession_away}%",
            },
            casual={
                "en": f"{label_en}: {score}.",
                "es": f"{label_es}: {score}.",
                "fil": f"{label_fil}: {score}.",
            },
            club_focus={
                "en": f"{label_en} for {{home}}: {{score_home}}-{{score_away}} against {{away}}, "
                "with {possession_home}% of the ball.",
                "es": f"{label_es} para {{home}}: {{score_home}}-{{score_away}} ante {{away}}, "
                "con el {possession_home} % del balón.",
                "fil": f"{label_fil} para sa {{home}}: {{score_home}}-{{score_away}} kontra "
                "{away}, hawak ang bola {possession_home}% ng oras.",
            },
            player_focus={
                "en": f"{label_en}: {score}, {{shots_home}} shots to {{shots_away}}.",
                "es": f"{label_es}: {score}, {{shots_home}} tiros contra {{shots_away}}.",
                "fil": f"{label_fil}: {score}, {{shots_home}} tira laban sa {{shots_away}}.",
            },
            single_metric={
                "en": f"{short_en} xG: {{xg_home}}-{{xg_away}}",
                "es": f"xG al {label_es.lower()}: {{xg_home}}-{{xg_away}}",
                "fil": f"{short_en} xG: {{xg_home}}-{{xg_away}}",
            },
        ),
    )


CATALOGUE: dict[str, RuleCopy] = {
    "momentum_shift/build_up": RuleCopy(
        headline="{team}'s build-up has broken down",
        explanation=(
            "Since {onset_minute:0}', {team}'s control score has fallen from {control_before} "
            "to {control_after}: pass accuracy {pass_accuracy_before}% → {pass_accuracy_after}% "
            "and {turnovers_after} turnovers, {own_half_turnovers_after} in their own half."
        ),
        modes=_modes(
            analyst={
                "en": "{team} control {control_before} → {control_after} since "
                "{onset_minute:0}'. Pass accuracy {pass_accuracy_before}% → "
                "{pass_accuracy_after}%, {turnovers_after} turnovers "
                "({own_half_turnovers_after} in own half).",
                "es": "{team}: control {control_before} → {control_after} desde el "
                "{onset_minute:0}'. Precisión de pase {pass_accuracy_before} % → "
                "{pass_accuracy_after} %, {turnovers_after} pérdidas "
                "({own_half_turnovers_after} en campo propio).",
                "fil": "{team}: control {control_before} → {control_after} mula "
                "{onset_minute:0}'. Pass accuracy {pass_accuracy_before}% → "
                "{pass_accuracy_after}%, {turnovers_after} turnover "
                "({own_half_turnovers_after} sa sariling half).",
            },
            casual={
                "en": "{team} can't keep the ball: {turnovers_after} giveaways in the last "
                "{minutes_since_onset:0} minutes.",
                "es": "{team} ya no retiene el balón: {turnovers_after} pérdidas en los últimos "
                "{minutes_since_onset:0} minutos.",
                "fil": "Hirap nang hawakan ng {team} ang bola: {turnovers_after} beses nawalan "
                "sa huling {minutes_since_onset:0} minuto.",
            },
            club_focus={
                "en": "Worrying spell for {team}: control is down to {control_after} and {opp} "
                "are taking over.",
                "es": "Momento delicado para {team}: el control ha caído hasta {control_after} "
                "y {opp} manda.",
                "fil": "Mabigat na yugto para sa {team}: bumaba sa {control_after} ang control "
                "at {opp} na ang may hawak ng laro.",
            },
            player_focus={
                "en": "{player} and {team} are struggling to keep the ball since "
                "{onset_minute:0}'.",
                "es": "{player} y {team} sufren para conservar el balón desde el "
                "{onset_minute:0}'.",
                "fil": "Nahihirapan si {player} at ang {team} na panatilihin ang bola mula "
                "{onset_minute:0}'.",
            },
            single_metric={
                "en": "{team} control: {control_before} → {control_after}",
                "es": "Control de {team}: {control_before} → {control_after}",
                "fil": "Control ng {team}: {control_before} → {control_after}",
            },
        ),
    ),
    "momentum_shift/pressing": RuleCopy(
        headline="{opp}'s press is squeezing {team}",
        explanation=(
            "Since {onset_minute:0}', {opp} have pressed higher (PPDA {opponent_ppda_before} → "
            "{opponent_ppda_after}) and {team}'s control score has dropped from "
            "{control_before} to {control_after}, with {own_half_turnovers_after} turnovers in "
            "their own half."
        ),
        modes=_modes(
            analyst={
                "en": "{opp} press: PPDA {opponent_ppda_before} → {opponent_ppda_after} since "
                "{onset_minute:0}'. {team} control {control_before} → {control_after}, "
                "{own_half_turnovers_after} own-half turnovers.",
                "es": "Presión de {opp}: PPDA {opponent_ppda_before} → {opponent_ppda_after} "
                "desde el {onset_minute:0}'. Control de {team} {control_before} → "
                "{control_after}, {own_half_turnovers_after} pérdidas en campo propio.",
                "fil": "Press ng {opp}: PPDA {opponent_ppda_before} → {opponent_ppda_after} "
                "mula {onset_minute:0}'. Control ng {team} {control_before} → "
                "{control_after}, {own_half_turnovers_after} turnover sa sariling half.",
            },
            casual={
                "en": "{opp} are hunting the ball high up the pitch and {team} can't get out.",
                "es": "{opp} presiona arriba y {team} no consigue salir.",
                "fil": "Mataas ang press ng {opp} at hindi makalabas ang {team}.",
            },
            club_focus={
                "en": "{team} are being pinned back: {opp}'s press has pushed their control "
                "down to {control_after}.",
                "es": "{team} está encerrado: la presión de {opp} ha bajado su control a "
                "{control_after}.",
                "fil": "Naiipit ang {team}: bumaba sa {control_after} ang control nila dahil sa "
                "press ng {opp}.",
            },
            player_focus={
                "en": "{player} is under constant pressure as {opp} press {team} high.",
                "es": "{player} recibe presión constante: {opp} aprieta muy arriba a {team}.",
                "fil": "Laging pinipressure si {player} habang mataas ang press ng {opp} sa "
                "{team}.",
            },
            single_metric={
                "en": "{opp} PPDA: {opponent_ppda_before} → {opponent_ppda_after}",
                "es": "PPDA de {opp}: {opponent_ppda_before} → {opponent_ppda_after}",
                "fil": "PPDA ng {opp}: {opponent_ppda_before} → {opponent_ppda_after}",
            },
        ),
    ),
    "goal": RuleCopy(
        headline="{player} scores for {team}",
        explanation=(
            "{player} scores on {minute:0}' from a chance worth {xg} xG. "
            "{home} {score_home}-{score_away} {away}."
        ),
        modes=_modes(
            analyst={
                "en": "Goal {player} ({team}) {minute:0}', xG {xg}. "
                "{home} {score_home}-{score_away} {away}.",
                "es": "Gol de {player} ({team}) en el {minute:0}', xG {xg}. "
                "{home} {score_home}-{score_away} {away}.",
                "fil": "Goal ni {player} ({team}) sa {minute:0}', xG {xg}. "
                "{home} {score_home}-{score_away} {away}.",
            },
            casual={
                "en": "Goal! {player} scores for {team}. It's {home} {score_home}-{score_away} "
                "{away}.",
                "es": "¡Gol! {player} marca para {team}. {home} {score_home}-{score_away} {away}.",
                "fil": "Goal! Umiskor si {player} para sa {team}. {home} "
                "{score_home}-{score_away} {away}.",
            },
            club_focus={
                "en": "{team} goal! {player} finds the net on {minute:0}'.",
                "es": "¡Gol de {team}! {player} marca en el {minute:0}'.",
                "fil": "Goal ng {team}! Umiskor si {player} sa {minute:0}'.",
            },
            player_focus={
                "en": "{player} scores on {minute:0}' from a {xg} xG chance.",
                "es": "{player} marca en el {minute:0}' en una ocasión de {xg} xG.",
                "fil": "Umiskor si {player} sa {minute:0}' mula sa {xg} xG na tsansa.",
            },
            single_metric={
                "en": "{home} {score_home}-{score_away} {away}",
                "es": "{home} {score_home}-{score_away} {away}",
                "fil": "{home} {score_home}-{score_away} {away}",
            },
        ),
    ),
    "sending_off": RuleCopy(
        headline="{player} sent off",
        explanation=(
            "{player} is sent off on {minute:0}'; {team} are down to {players_left} players."
        ),
        modes=_modes(
            analyst={
                "en": "Red card: {player} ({team}) {minute:0}'. {team} down to {players_left}.",
                "es": "Roja: {player} ({team}) en el {minute:0}'. {team} se queda con "
                "{players_left}.",
                "fil": "Red card: {player} ({team}) {minute:0}'. {players_left} na lang ang "
                "{team}.",
            },
            casual={
                "en": "{player} is sent off! {team} have {players_left} players left.",
                "es": "¡Expulsado {player}! {team} se queda con {players_left} jugadores.",
                "fil": "Pinalabas si {player}! {players_left} na lang ang manlalaro ng {team}.",
            },
            club_focus={
                "en": "Blow for {team}: {player} sent off on {minute:0}', down to {players_left}.",
                "es": "Golpe para {team}: expulsado {player} en el {minute:0}', se queda con "
                "{players_left}.",
                "fil": "Dagok sa {team}: pinalabas si {player} sa {minute:0}', {players_left} na "
                "lang sila.",
            },
            player_focus={
                "en": "{player} sees red on {minute:0}'.",
                "es": "{player} ve la roja en el {minute:0}'.",
                "fil": "Red card kay {player} sa {minute:0}'.",
            },
            single_metric={
                "en": "{team}: {players_left} players",
                "es": "{team}: {players_left} jugadores",
                "fil": "{team}: {players_left} manlalaro",
            },
        ),
    ),
    "sending_off_impact": RuleCopy(
        headline="{team}'s game since the red card",
        explanation=(
            "In the {window_min} minutes since going down to {players_left}, {team}'s possession "
            "is {possession_after}% (was {possession_before}%) and their control score "
            "{control_after} (was {control_before})."
        ),
        modes=_modes(
            analyst={
                "en": "{team} with {players_left}: possession {possession_before}% → "
                "{possession_after}%, control {control_before} → {control_after} over "
                "{window_min} minutes.",
                "es": "{team} con {players_left}: posesión {possession_before} % → "
                "{possession_after} %, control {control_before} → {control_after} en "
                "{window_min} minutos.",
                "fil": "{team} na may {players_left}: possession {possession_before}% → "
                "{possession_after}%, control {control_before} → {control_after} sa "
                "{window_min} minuto.",
            },
            casual={
                "en": "Down to {players_left}, {team} now have the ball {possession_after}% of "
                "the time.",
                "es": "Con {players_left}, {team} tiene ahora el balón el {possession_after} % "
                "del tiempo.",
                "fil": "{players_left} na lang, hawak ng {team} ang bola {possession_after}% ng "
                "oras.",
            },
            club_focus={
                "en": "{team} with {players_left} men: {possession_after}% possession since the "
                "red card.",
                "es": "{team} con {players_left}: {possession_after} % de posesión desde la roja.",
                "fil": "{team} na may {players_left}: {possession_after}% possession mula sa red "
                "card.",
            },
            player_focus={
                "en": "{team}'s {players_left} have had {possession_after}% of the ball since the "
                "red card.",
                "es": "Los {players_left} de {team} han tenido el {possession_after} % del balón "
                "desde la roja.",
                "fil": "{possession_after}% ng bola ang hawak ng {players_left} ng {team} mula sa "
                "red card.",
            },
            single_metric={
                "en": "{team} possession: {possession_before}% → {possession_after}%",
                "es": "Posesión de {team}: {possession_before} % → {possession_after} %",
                "fil": "Possession ng {team}: {possession_before}% → {possession_after}%",
            },
        ),
    ),
    "penalty_awarded": RuleCopy(
        headline="Penalty to {team}",
        explanation="{team} win a penalty on {minute:0}' after {player} is fouled.",
        modes=_modes(
            analyst={
                "en": "Penalty {team}, {minute:0}': {player} fouled.",
                "es": "Penalti para {team}, {minute:0}': falta sobre {player}.",
                "fil": "Penalty para sa {team}, {minute:0}': na-foul si {player}.",
            },
            casual={
                "en": "Penalty! {player} is brought down and {team} have a spot kick.",
                "es": "¡Penalti! Derriban a {player} y {team} tiene un penalti.",
                "fil": "Penalty! Napatumba si {player} at may penalty ang {team}.",
            },
            club_focus={
                "en": "Penalty for {team} on {minute:0}'!",
                "es": "¡Penalti para {team} en el {minute:0}'!",
                "fil": "Penalty para sa {team} sa {minute:0}'!",
            },
            player_focus={
                "en": "{player} wins a penalty on {minute:0}'.",
                "es": "{player} provoca un penalti en el {minute:0}'.",
                "fil": "Nakakuha si {player} ng penalty sa {minute:0}'.",
            },
            single_metric={
                "en": "Penalty {team} {minute:0}'",
                "es": "Penalti {team} {minute:0}'",
                "fil": "Penalty {team} {minute:0}'",
            },
        ),
    ),
    "player_form": RuleCopy(
        headline="{player} is on fire",
        explanation=(
            "{player} has had {shots} shots worth {npxg} xG in the last {window_min} minutes, "
            "{on_target} on target."
        ),
        modes=_modes(
            analyst={
                "en": "{player}: {shots} shots, {npxg} npxG, {on_target} on target in "
                "{window_min} minutes.",
                "es": "{player}: {shots} tiros, {npxg} npxG, {on_target} a puerta en "
                "{window_min} minutos.",
                "fil": "{player}: {shots} tira, {npxg} npxG, {on_target} on target sa "
                "{window_min} minuto.",
            },
            casual={
                "en": "{player} keeps shooting: {shots} attempts in {window_min} minutes.",
                "es": "{player} no para de disparar: {shots} intentos en {window_min} minutos.",
                "fil": "Walang tigil sa pagtira si {player}: {shots} tira sa {window_min} minuto.",
            },
            club_focus={
                "en": "{team}'s {player} is causing all sorts of problems: {shots} shots in "
                "{window_min} minutes.",
                "es": "{player}, de {team}, está causando estragos: {shots} tiros en "
                "{window_min} minutos.",
                "fil": "Nagpapahirap si {player} ng {team}: {shots} tira sa {window_min} minuto.",
            },
            player_focus={
                "en": "{player}: {shots} shots, {on_target} on target, {goals} goals in the last "
                "{window_min} minutes.",
                "es": "{player}: {shots} tiros, {on_target} a puerta, {goals} goles en los "
                "últimos {window_min} minutos.",
                "fil": "{player}: {shots} tira, {on_target} on target, {goals} goal sa huling "
                "{window_min} minuto.",
            },
            single_metric={
                "en": "{player}: {npxg} xG in {window_min} minutes",
                "es": "{player}: {npxg} xG en {window_min} minutos",
                "fil": "{player}: {npxg} xG sa {window_min} minuto",
            },
        ),
    ),
    "half_time_recap": _recap("Half-time", "HT", "Descanso", "Desc.", "Half-time"),
    "full_time_recap": _recap("Full-time", "FT", "Final", "Final", "Full-time"),
    "shot_speed": RuleCopy(
        headline="Rocket from {player}",
        explanation="{player} hits a shot at {shot_speed_kmh} km/h.",
        modes=_modes(
            analyst={
                "en": "{player} shot speed {shot_speed_kmh} km/h.",
                "es": "Velocidad del disparo de {player}: {shot_speed_kmh} km/h.",
                "fil": "Bilis ng tira ni {player}: {shot_speed_kmh} km/h.",
            },
            casual={
                "en": "What a strike from {player}: {shot_speed_kmh} km/h!",
                "es": "¡Qué disparo de {player}: {shot_speed_kmh} km/h!",
                "fil": "Ang lakas ng tira ni {player}: {shot_speed_kmh} km/h!",
            },
            club_focus={
                "en": "{team}'s {player} unleashes a {shot_speed_kmh} km/h shot.",
                "es": "{player}, de {team}, suelta un disparo a {shot_speed_kmh} km/h.",
                "fil": "Pinakawalan ni {player} ng {team} ang {shot_speed_kmh} km/h na tira.",
            },
            player_focus={
                "en": "{player}: shot at {shot_speed_kmh} km/h.",
                "es": "{player}: disparo a {shot_speed_kmh} km/h.",
                "fil": "{player}: tira na {shot_speed_kmh} km/h.",
            },
            single_metric={
                "en": "{shot_speed_kmh} km/h",
                "es": "{shot_speed_kmh} km/h",
                "fil": "{shot_speed_kmh} km/h",
            },
        ),
    ),
    "top_speed": RuleCopy(
        headline="{player} hits top speed",
        explanation="{player} reaches {top_speed_kmh} km/h.",
        modes=_modes(
            analyst={
                "en": "{player} top speed {top_speed_kmh} km/h.",
                "es": "Velocidad máxima de {player}: {top_speed_kmh} km/h.",
                "fil": "Pinakamabilis na takbo ni {player}: {top_speed_kmh} km/h.",
            },
            casual={
                "en": "{player} is flying: {top_speed_kmh} km/h!",
                "es": "¡{player} vuela: {top_speed_kmh} km/h!",
                "fil": "Lumilipad si {player}: {top_speed_kmh} km/h!",
            },
            club_focus={
                "en": "{team}'s {player} clocks {top_speed_kmh} km/h.",
                "es": "{player}, de {team}, alcanza {top_speed_kmh} km/h.",
                "fil": "Umabot sa {top_speed_kmh} km/h si {player} ng {team}.",
            },
            player_focus={
                "en": "{player}: {top_speed_kmh} km/h sprint.",
                "es": "{player}: sprint a {top_speed_kmh} km/h.",
                "fil": "{player}: sprint na {top_speed_kmh} km/h.",
            },
            single_metric={
                "en": "{top_speed_kmh} km/h",
                "es": "{top_speed_kmh} km/h",
                "fil": "{top_speed_kmh} km/h",
            },
        ),
    ),
}

NAME_FIELDS = frozenset({"team", "opp", "player", "home", "away"})
"""Placeholders filled with names from the roster rather than metrics."""

FALLBACK: dict[str, str] = {"momentum_shift/pressing": "momentum_shift/build_up"}
"""When a variant's numbers are missing (e.g. PPDA undefined), use this variant instead."""


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


def fill(template: str, values: Mapping[str, object], language: str) -> str:
    """Render one template; raises KeyError when a placeholder has no value."""
    return _Formatter(language).vformat(template, (), dict(values))


def resolve_key(key: str, values: Mapping[str, object]) -> str:
    """The variant to use: falls back (e.g. pressing -> build-up) when a number the variant's
    explanation needs is missing, so headline, explanation and every mode stay consistent."""
    rule = CATALOGUE[key]
    needed = template_fields(rule.explanation) - NAME_FIELDS
    if not needed <= set(values) and key in FALLBACK:
        return resolve_key(FALLBACK[key], values)
    return key


def template_fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def headline(key: str, values: Mapping[str, object]) -> str:
    return fill(CATALOGUE[resolve_key(key, values)].headline, values, "en")


def explanation(key: str, values: Mapping[str, object]) -> str:
    return fill(CATALOGUE[resolve_key(key, values)].explanation, values, "en")


def mode_text(key: str, mode: str, language: str, values: Mapping[str, object]) -> str:
    return fill(CATALOGUE[resolve_key(key, values)].modes[mode][language], values, language)


def used_metrics(key: str, metrics: Mapping[str, float]) -> list[str]:
    """Metric names the headline and explanation use (after any fallback)."""
    rule = CATALOGUE[resolve_key(key, metrics)]
    fields = (template_fields(rule.headline) | template_fields(rule.explanation)) - NAME_FIELDS
    return sorted(fields & set(metrics))
