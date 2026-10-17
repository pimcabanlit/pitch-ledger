"""The fan story behind "Tell me more": three short beats for one aired insight.

The buildup (what led to the moment), what just happened (the aired on-screen version for
the reader's audience mode and language, already number-checked) and what it could mean
(the stakes from here). Beats are written in code from templates in every language, like
the copy fallbacks in ``agents.copy``: every number comes from the match files or the
trigger's computed metrics, so there is nothing for the Auditor to catch and no model call.

Depth follows the audience: the analyst story carries the figures (score, shots, xG,
possession and control windows); every other mode reads in plain words with as few
numbers as possible. The outlook frames what to watch; it never predicts a figure.
"""

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pitch_ledger.agents.copy import LANGUAGES, fill
from pitch_ledger.api.match_centre import MatchData, TimelineEvent
from pitch_ledger.api.schemas import StoryNarrative
from pitch_ledger.engine.windows import match_minute
from pitch_ledger.ops.alerts import minute_label
from pitch_ledger.schema import Insight, Overlay, Trigger
from pitch_ledger.validation.insight_rules import unsupported_numbers

log = logging.getLogger(__name__)

STORY_MODES = ("casual", "analyst", "club_focus", "player_focus")
"""Audience modes a reader can pick for the story (single_metric is a graphic, not prose)."""
NUMBERS_MODE = "analyst"
FULL_TIME = 90
EARLY = 3  # minutes before which the buildup is just the kick-off
LATE = 5  # minutes left at which a goal reads as a possible winner
WINDOW_MIN = 5  # the Match Centre's rolling team-state window
MAX_LISTED = 3  # goals listed by name in the analyst buildup; beyond that, the latest
CLEAR_XG = 0.3  # xG gap that makes one side clearly the better one
MOMENTUM_KEYS = ("momentum_shift", "sending_off_impact")
PLAYER_FEATS = (
    "shot_speed",
    "top_speed",
    "distance_milestone",
    "pass_milestone",
    "pass_quality",
    "player_form",
)

Lang = dict[str, str]


def _l(en: str, es: str, fil: str) -> Lang:
    return {"en": en, "es": es, "fil": fil}


# Beat parts ---------------------------------------------------------------------------------

TIME_LEFT = _l(
    "with about {left} minutes left",
    "con unos {left} minutos por jugar",
    "may mga {left} minuto pa",
)
TIME_UP = _l("with time almost up", "con el tiempo casi cumplido", "halos ubos na ang oras")
AND = _l(" and ", " y ", " at ")

KICK_OFF = _l(
    "It had only just kicked off.", "El partido acababa de empezar.", "Kakasimula pa lang ng laro."
)
GOALLESS = _l("It was still goalless.", "Seguía sin goles.", "Wala pang goal.")
LEVEL = _l(
    "It was {bh}-{ba}, the latest goal from {last_scorer}.",
    "Iban {bh}-{ba}, con el último gol de {last_scorer}.",
    "Tabla sa {bh}-{ba}, huling goal ni {last_scorer}.",
)
LEADING = _l(
    "{leader} led {hi}-{lo}, the latest goal from {last_scorer}.",
    "{leader} ganaba {hi}-{lo}, con el último gol de {last_scorer}.",
    "Lamang ang {leader}, {hi}-{lo}, huling goal ni {last_scorer}.",
)
SCORE_BEFORE = _l(
    "Before {cut}': {home} {bh}-{ba} {away}.",
    "Antes del {cut}': {home} {bh}-{ba} {away}.",
    "Bago ang {cut}': {home} {bh}-{ba} {away}.",
)
SCORE_BEFORE_GOALS = _l(
    "Before {cut}': {home} {bh}-{ba} {away} (goals: {goal_list}).",
    "Antes del {cut}': {home} {bh}-{ba} {away} (goles: {goal_list}).",
    "Bago ang {cut}': {home} {bh}-{ba} {away} (mga goal: {goal_list}).",
)
SCORE_BEFORE_LATEST = _l(
    "Before {cut}': {home} {bh}-{ba} {away} (latest goal: {goal_list}).",
    "Antes del {cut}': {home} {bh}-{ba} {away} (último gol: {goal_list}).",
    "Bago ang {cut}': {home} {bh}-{ba} {away} (huling goal: {goal_list}).",
)
RED_BEFORE = _l(
    "{red_team} had been down to {red_left} since {red_player}'s red card.",
    "{red_team} jugaba con {red_left} desde la roja a {red_player}.",
    "{red_left} na lang ang {red_team} mula nang ma-red card si {red_player}.",
)
RARE = _l("Chances had been rare.", "Apenas había habido ocasiones.", "Bihira pa ang mga tsansa.")
BETTER = _l(
    "{better} had been creating the better chances.",
    "{better} generaba las mejores ocasiones.",
    "Mas magaganda ang tsansang nagagawa ng {better}.",
)
EVEN = _l(
    "There was little to choose between the sides.",
    "Había poca diferencia entre los dos equipos.",
    "Halos pantay ang dalawang koponan.",
)
SHOTS = _l(
    "Shots {hs}-{as}, xG {hx}-{ax}.",
    "Tiros {hs}-{as}, xG {hx}-{ax}.",
    "Tira {hs}-{as}, xG {hx}-{ax}.",
)
POSSESSION = _l(
    "Possession over the previous {win} minutes: {hp}%-{ap}%.",
    "Posesión en los {win} minutos previos: {hp}%-{ap}%.",
    "Possession sa nakaraang {win} minuto: {hp}%-{ap}%.",
)
ON_TOP = _l(
    "Before that, {team} had been in control, with most of the ball.",
    "Hasta entonces, {team} dominaba y tenía casi siempre el balón.",
    "Bago noon, kontrolado ng {team} ang laro at hawak nila ang bola.",
)
HOLDING = _l(
    "Before that, {team} had been holding their own.",
    "Hasta entonces, {team} aguantaba bien.",
    "Bago noon, nakakasabay ang {team}.",
)
WINDOW_BEFORE = _l(
    "From {bs}' to {be}', {team} had {possession_before}% of the ball and a control score of "
    "{control_before}.",
    "Del {bs}' al {be}', {team} tuvo un {possession_before}% de posesión y un control de "
    "{control_before}.",
    "Mula {bs}' hanggang {be}', {possession_before}% ang possession ng {team} at "
    "{control_before} ang control score.",
)
PASSING_BEFORE = _l(
    "They completed {pass_accuracy_before}% of their passes.",
    "Acertaba el {pass_accuracy_before}% de sus pases.",
    "{pass_accuracy_before}% ng kanilang pasa ang kumpleto.",
)
FIRST_HALF_GOALS = _l(
    "The first half brought {n_goals} goals: {goal_list}.",
    "La primera parte dejó {n_goals} goles: {goal_list}.",
    "{n_goals} goal sa first half: {goal_list}.",
)
FIRST_HALF_ONE = _l(
    "The only goal of the first half: {goal_list}.",
    "El único gol de la primera parte: {goal_list}.",
    "Iisang goal sa first half: {goal_list}.",
)
FIRST_HALF_NONE = _l(
    "The first half was goalless.",
    "La primera parte terminó sin goles.",
    "Walang goal sa first half.",
)
AT_HALF_TIME = _l(
    "It was {ht_home}-{ht_away} at half-time.",
    "Al descanso iban {ht_home}-{ht_away}.",
    "{ht_home}-{ht_away} sa half-time.",
)
SECOND_HALF_GOALS = _l(
    "After the break: {goal_list}.",
    "Tras el descanso: {goal_list}.",
    "Pagkatapos ng break: {goal_list}.",
)
SECOND_HALF_NONE = _l(
    "No goals after the break.", "Sin goles tras el descanso.", "Walang goal pagkatapos ng break."
)

# Outlook: what it could mean ------------------------------------------------------------------

ALL_SQUARE = _l(
    "It's all square again, {remaining} for either side to find a winner.",
    "Todo igualado, {remaining} para que cualquiera busque la victoria.",
    "Tabla na ulit, {remaining} para makahanap ng panalo ang alinmang koponan.",
)
LATE_LEVELLER = _l(
    "Level this late, it could be the goal that earns {team} a point.",
    "Empatar tan tarde puede valerle un punto a {team}.",
    "Sa ganitong kahuli, baka ito ang goal na magbibigay ng puntos sa {team}.",
)
NEED_ANSWER = _l(
    "{opp} need an answer, {remaining} to find one.",
    "{opp} necesita reaccionar, {remaining} para hacerlo.",
    "Kailangang sumagot ng {opp}, {remaining} para gawin ito.",
)
LATE_WINNER = _l(
    "With time almost up, this could be the winner.",
    "Con el tiempo casi cumplido, puede ser el gol de la victoria.",
    "Halos ubos na ang oras: baka ito na ang panalong goal.",
)
PULLING_CLEAR = _l(
    "{team} are pulling clear: {opp} now need {deficit} goals just to draw level.",
    "{team} se escapa: {opp} necesita {deficit} goles solo para empatar.",
    "Lumalayo na ang {team}: kailangan ng {opp} ng {deficit} goal para lang makapantay.",
)
BACK_IN_IT = _l(
    "{team} are back in it, one goal behind {remaining}.",
    "{team} vuelve a meterse, a un gol {remaining}.",
    "Buhay pa ang {team}, isang goal na lang ang agwat, {remaining}.",
)
STILL_BEHIND = _l(
    "{team} pull one back but still trail by {deficit}, {remaining}.",
    "{team} recorta, pero sigue {deficit} goles por detrás, {remaining}.",
    "Nakabawi ng isa ang {team} pero {deficit} goal pa ang agwat, {remaining}.",
)
HAT_TRICK_ON = _l(
    "One more and {player} has a hat-trick.",
    "Uno más y {player} firma un hat-trick.",
    "Isa pa at may hat-trick na si {player}.",
)
XG_FOR = _l(
    "On xG ({team_xg} v {opponent_xg}), {team} have made the better chances.",
    "En xG ({team_xg} frente a {opponent_xg}), {team} ha generado más.",
    "Sa xG ({team_xg} laban sa {opponent_xg}), mas maganda ang tsansa ng {team}.",
)
XG_AGAINST = _l(
    "On xG, though, {opp} have made more ({opponent_xg} v {team_xg}).",
    "En xG, sin embargo, {opp} ha generado más ({opponent_xg} frente a {team_xg}).",
    "Pero sa xG, mas marami ang nagawa ng {opp} ({opponent_xg} laban sa {team_xg}).",
)

PEN_LEAD = _l(
    "Score it and {team} take the lead.",
    "Si marca, {team} se pone por delante.",
    "Kapag naipasok, lalamang ang {team}.",
)
PEN_MORE = _l(
    "Score it and {team} extend their lead.",
    "Si marca, {team} amplía su ventaja.",
    "Kapag naipasok, lalaki ang lamang ng {team}.",
)
PEN_LEVEL = _l(
    "Score it and {team} draw level.",
    "Si marca, {team} empata.",
    "Kapag naipasok, makakapantay ang {team}.",
)
PEN_CLOSER = _l(
    "Score it and {team} pull one back.",
    "Si marca, {team} recorta distancias.",
    "Kapag naipasok, makakabawas ng isa ang {team}.",
)
PEN_SCORE = _l(
    "A goal would make it {home} {pen_home}-{pen_away} {away}.",
    "Un gol dejaría el marcador en {home} {pen_home}-{pen_away} {away}.",
    "Kapag naipasok: {home} {pen_home}-{pen_away} {away}.",
)

RED_CHASE = _l(
    "{team} must chase the game with {players_left} for the last {minutes_left} minutes.",
    "{team} tendrá que remontar con {players_left} durante los últimos {minutes_left} minutos.",
    "Kailangang humabol ng {team} na {players_left} lang sa huling {minutes_left} minuto.",
)
RED_HOLD = _l(
    "{team} must hold on with {players_left} for the last {minutes_left} minutes, and "
    "{opp} will sense a chance.",
    "{team} tendrá que resistir con {players_left} los últimos {minutes_left} minutos, "
    "y {opp} olerá su oportunidad.",
    "Kailangang kumapit ng {team} na {players_left} lang sa huling {minutes_left} "
    "minuto, at may pagkakataon ang {opp}.",
)
RED_PROTECT = _l(
    "{team} must protect their lead with {players_left} for the last {minutes_left} minutes.",
    "{team} tendrá que defender su ventaja con {players_left} durante los últimos "
    "{minutes_left} minutos.",
    "Kailangang ipagtanggol ng {team} ang lamang na {players_left} lang sa huling "
    "{minutes_left} minuto.",
)
EXTRA_MAN = _l(
    "Watch whether {opp} turn the extra player into more of the ball and more shots.",
    "Atentos a si {opp} convierte el jugador de más en más balón y más tiros.",
    "Abangan kung magagamit ng {opp} ang sobrang manlalaro para sa mas maraming bola at tira.",
)
TEN_MEN_RISK = _l(
    "If {team} can't get more of the ball, {opp} have the chance to make the extra "
    "player count, {remaining}.",
    "Si {team} no recupera el balón, {opp} tendrá ocasión de aprovechar el jugador "
    "de más, {remaining}.",
    "Kung hindi makakakuha ng bola ang {team}, may pagkakataon ang {opp} na sulitin "
    "ang sobrang manlalaro, {remaining}.",
)
GIVEAWAYS_RISK = _l(
    "If {team} can't stop giving the ball away, {opp} could make them pay, {remaining}.",
    "Si {team} no deja de perder el balón, {opp} podría castigarlo, {remaining}.",
    "Kung hindi titigil sa pagkawala ng bola ang {team}, puwede silang parusahan "
    "ng {opp}, {remaining}.",
)
LEAD_TESTED = _l(
    "{team} still lead, but that lead is being tested.",
    "{team} sigue por delante, pero esa ventaja está en juego.",
    "Lamang pa rin ang {team}, pero sinusubok na ang lamang na iyon.",
)
WAY_BACK = _l(
    "{team} are already behind, and this makes the way back harder.",
    "{team} ya va por detrás, y esto complica la remontada.",
    "Talo na ang {team} sa iskor, at lalong humihirap ang paghabol.",
)
LEVEL_DECIDER = _l(
    "With the score level, this spell could decide who wins it.",
    "Con el empate en el marcador, este tramo puede decidir el partido.",
    "Tabla ang iskor, kaya puwedeng ito ang yugtong magpapasya ng laro.",
)
OPP_OUTPUT = _l(
    "So far {opp} have turned it into {opponent_shots_after} {shot_word} worth "
    "{opponent_xg_after} xG; watch whether the chances start to come.",
    "Por ahora {opp} lo ha convertido en {opponent_shots_after} {shot_word} con "
    "{opponent_xg_after} xG; atentos a si llegan las ocasiones.",
    "Sa ngayon, {opponent_shots_after} tira na may {opponent_xg_after} xG pa lang ang "
    "nagagawa ng {opp}; abangan kung darating ang mga tsansa.",
)
SHOT_WORD = {"en": ("shot", "shots"), "es": ("tiro", "tiros"), "fil": ("tira", "tira")}

HT_OPEN = _l(
    "It's all to play for in the second half.",
    "Todo por decidir en la segunda parte.",
    "Bukas pa ang laban sa second half.",
)
HT_NEED_ONE = _l(
    "{trailer} need one goal after the break to draw level.",
    "{trailer} necesita un gol tras el descanso para empatar.",
    "Kailangan ng {trailer} ng isang goal pagkatapos ng break para makapantay.",
)
HT_NEED = _l(
    "{trailer} need {deficit} goals after the break to draw level.",
    "{trailer} necesita {deficit} goles tras el descanso para empatar.",
    "Kailangan ng {trailer} ng {deficit} goal pagkatapos ng break para makapantay.",
)
HT_XG = _l(
    "On xG ({xg_home}-{xg_away}), {better} have been the better side so far.",
    "En xG ({xg_home}-{xg_away}), {better} ha sido mejor hasta ahora.",
    "Sa xG ({xg_home}-{xg_away}), mas magaling ang {better} sa ngayon.",
)
HT_XG_EVEN = _l(
    "On xG ({xg_home}-{xg_away}) there has been little between them.",
    "En xG ({xg_home}-{xg_away}) apenas hay diferencia.",
    "Sa xG ({xg_home}-{xg_away}), halos pantay sila.",
)
FT_WIN = _l(
    "Three points for {winner}.", "Tres puntos para {winner}.", "Tatlong puntos para sa {winner}."
)
FT_DRAW = _l("The points are shared.", "Reparto de puntos.", "Hati ang puntos.")
FT_DESERVED = _l(
    "The xG ({xg_home}-{xg_away}) says {winner} deserved it.",
    "El xG ({xg_home}-{xg_away}) dice que {winner} lo mereció.",
    "Ayon sa xG ({xg_home}-{xg_away}), karapat-dapat ang panalo ng {winner}.",
)
FT_HARD_DONE = _l(
    "The xG ({xg_home}-{xg_away}) says {better} deserved more.",
    "El xG ({xg_home}-{xg_away}) dice que {better} mereció más.",
    "Ayon sa xG ({xg_home}-{xg_away}), mas karapat-dapat sana ang {better}.",
)

FEAT_OUTLOOK: dict[str, Lang] = {
    "shot_speed": _l(
        "Strikes like that test any goalkeeper: watch {player} whenever {team} get near goal.",
        "Disparos así ponen a prueba a cualquier portero: atentos a {player} cada vez que "
        "{team} se acerque al área.",
        "Mahirap saluhin ang ganitong tira: bantayan si {player} tuwing lalapit sa goal ang "
        "{team}.",
    ),
    "top_speed": _l(
        "Pace like that can turn a game: watch for {player} running in behind.",
        "Esa velocidad puede cambiar un partido: atentos a {player} a la espalda de la defensa.",
        "Kayang baguhin ng ganitong bilis ang laro: abangan si {player} na tatakbo sa likod ng "
        "depensa.",
    ),
    "distance_milestone": _l(
        "{player}'s running is keeping {team} going; the question is who has more left late on.",
        "El despliegue de {player} sostiene a {team}; la pregunta es quién llegará con más "
        "fuerzas al final.",
        "Ang takbo ni {player} ang nagpapatuloy sa {team}; ang tanong, sino ang may lakas pa sa "
        "dulo.",
    ),
    "pass_milestone": _l(
        "{team}'s passing runs through {player}: when they have the ball, look for {player}.",
        "El juego de {team} pasa por {player}: cuando {team} tenga el balón, atentos a {player}.",
        "Dumadaan kay {player} ang laro ng {team}: abangan siya tuwing hawak nila ang bola.",
    ),
    "pass_quality": _l(
        "Passes like that can open any defence: watch for {player} looking for the runners.",
        "Pases así abren cualquier defensa: atentos a {player} buscando los desmarques.",
        "Kayang buksan ng ganitong pasa ang kahit anong depensa: abangan si {player} na "
        "naghahanap ng tatakbo.",
    ),
    "player_form": _l(
        "{player} is the danger right now: if the shots keep coming, the goals could follow.",
        "{player} es el peligro ahora mismo: si siguen llegando los tiros, pueden llegar los "
        "goles.",
        "Si {player} ang delikado ngayon: kapag tuloy ang mga tira, puwedeng sumunod ang mga goal.",
    ),
}
SCORE_NOW = _l(
    "It's {home} {sh}-{sa} {away}, {remaining}.",
    "Va {home} {sh}-{sa} {away}, {remaining}.",
    "{home} {sh}-{sa} {away} ngayon, {remaining}.",
)

# Titles in Spanish and Filipino (English uses the audited headline) ---------------------------

TITLES: dict[str, Lang] = {
    "goal": _l(
        "", "Gol de {player}: {home} {sh}-{sa} {away}", "Goal ni {player}: {home} {sh}-{sa} {away}"
    ),
    "penalty_awarded": _l("", "Penalti para {team}", "Penalty para sa {team}"),
    "sending_off": _l(
        "",
        "Expulsado {player}: {team} se queda con {players_left}",
        "Red card kay {player}: {players_left} na lang ang {team}",
    ),
    "sending_off_impact": _l(
        "",
        "{team}, con {players_left}, pierde el balón",
        "Sa {players_left} manlalaro, nawawalan ng bola ang {team}",
    ),
    "momentum_shift": _l("", "{team} pierde el control", "Nawawalan ng kontrol ang {team}"),
    "half_time_recap": _l(
        "", "Descanso: {home} {sh}-{sa} {away}", "Half-time: {home} {sh}-{sa} {away}"
    ),
    "full_time_recap": _l(
        "", "Final: {home} {sh}-{sa} {away}", "Full-time: {home} {sh}-{sa} {away}"
    ),
    "shot_speed": _l("", "Disparo de {player}", "Malakas na tira ni {player}"),
    "top_speed": _l("", "{player}, a toda velocidad", "Ang bilis ni {player}"),
    "pass_milestone": _l("", "{player} mueve a {team}", "{player}, makina ng {team}"),
    "distance_milestone": _l("", "{player} no para de correr", "Hindi tumitigil si {player}"),
    "pass_quality": _l("", "Pase de precisión de {player}", "Tumpak na pasa ni {player}"),
    "player_form": _l("", "{player}, en racha", "Mainit si {player}"),
}
TITLE_FALLBACK = _l("", "Momento del partido", "Sandali ng laro")


# Facts --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Goal:
    scorer: str
    team_id: str | None
    period: int
    label: str  # "9'"


@dataclass
class Facts:
    """Everything a beat may say, computed from the match files and the trigger."""

    rule: str
    period: int
    minute: float
    values: dict[str, object] = field(default_factory=dict)
    goals_before: list[Goal] = field(default_factory=list)
    goals_all: list[Goal] = field(default_factory=list)  # up to the moment, the moment included
    side: str | None = None  # the subject team: "home" or "away"
    has_player: bool = False


def _when(e: TimelineEvent) -> tuple[int, int]:
    return e.period, e.clock_ms


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _whole(minute: float) -> int:
    """A match minute as the labels write it (68.86 -> 69, as in ``minute_label``)."""
    return int(f"{round(minute, 1):.0f}")


def _cutoff(insight: Insight, trigger: Trigger | None, data: MatchData) -> tuple[int, int]:
    """Where the buildup ends: the start of the compared window, else the moment's first
    evidence event, else the insight's own clock."""
    if trigger is not None:
        w = trigger.context.get("after_window")
        if isinstance(w, dict) and "period" in w and "start_ms" in w:
            return int(str(w["period"])), int(str(w["start_ms"]))
    at = (insight.period, insight.clock_ms)
    rule = trigger.rule_id if trigger else insight.insight_type.value
    if rule in PLAYER_FEATS:  # their evidence spans a spell or the whole match so far
        return at
    ids = set(insight.evidence_event_ids)
    rows = [(e["period"], e["clock_ms"]) for e in data.files.events if e["event_id"] in ids]
    return min([*rows, at])


def story_facts(data: MatchData, insight: Insight, trigger: Trigger | None) -> Facts:
    match = data.files.match
    names = {t.team_id: t.name for t in data.files.teams}
    names |= {p.player_id: p.display_name for p in data.files.players}
    rule = trigger.rule_id if trigger else insight.insight_type.value
    metrics: dict[str, int | float] = dict(trigger.metrics if trigger else insight.metrics)
    at = (insight.period, insight.clock_ms)
    cut = _cutoff(insight, trigger, data)
    minute = match_minute(*at)
    team_id = insight.subject.team_id
    player_id = insight.subject.player_id
    side = None if team_id is None else "home" if team_id == match.home_team_id else "away"
    opp_id = None if side is None else match.away_team_id if side == "home" else match.home_team_id
    events = data.timeline.events

    scored = [e for e in events if e.type == "shot" and e.outcome == "goal" and _when(e) <= at]
    goals = [Goal(names.get(e.player_id or "", "?"), e.team_id, e.period,
                  minute_label(e.period, e.clock_ms)) for e in scored]  # fmt: skip
    before = [e for e in events if _when(e) < cut]
    now = [e for e in events if _when(e) <= at]
    bh, ba = (before[-1].score_home, before[-1].score_away) if before else (0, 0)
    sh, sa = (now[-1].score_home, now[-1].score_away) if now else (0, 0)
    if "score_home" in metrics and "score_away" in metrics:  # the trigger's own score wins
        sh, sa = int(metrics["score_home"]), int(metrics["score_away"])
    goals_before = [g for g, e in zip(goals, scored, strict=True) if _when(e) < cut]

    values: dict[str, object] = {
        "home": names[match.home_team_id], "away": names[match.away_team_id],
        "cut": _whole(match_minute(*cut)), "bh": bh, "ba": ba, "sh": sh, "sa": sa,
        "left": max(0, FULL_TIME - _whole(minute)), "win": WINDOW_MIN,
        **metrics,
    }  # fmt: skip
    if team_id is not None and opp_id is not None:
        values |= {"team": names.get(team_id, team_id), "opp": names.get(opp_id, opp_id)}
    if player_id is not None:
        values["player"] = names.get(player_id, player_id)

    # Shots and xG before the cutoff, and the rolling possession just before it.
    shots = [s for s in data.analytics().shots if (s.period, s.clock_ms) < cut]
    for key, tid in (("h", match.home_team_id), ("a", match.away_team_id)):
        mine = [s for s in shots if s.team_id == tid]
        values[f"{key}s"] = mine[-1].team_shots if mine else 0
        values[f"{key}x"] = mine[-1].team_xg if mine else 0
        state = [r for r in data.analytics().team_state
                 if r.team_id == tid and r.period == cut[0]
                 and r.minute <= match_minute(*cut)]  # fmt: skip
        if state and state[-1].possession_pct is not None:
            values[f"{key}p"] = round(state[-1].possession_pct)

    first_half = [e for e in events if e.period == 1]
    if first_half:
        values["ht_home"], values["ht_away"] = first_half[-1].score_home, first_half[-1].score_away

    # A red card at the cutoff opened the compared window; the sending off itself is the moment.
    reds = [e for e in events if e.type == "card" and e.outcome == "red"
            and (_when(e) < cut or (_when(e) == cut and rule != "sending_off"))]  # fmt: skip
    if reds:
        red = reds[-1]
        sent_off = sum(e.team_id == red.team_id for e in reds)
        values |= {"red_team": names.get(red.team_id or "", "?"),
                   "red_player": names.get(red.player_id or "", "?"),
                   "red_left": 11 - sent_off}  # fmt: skip

    windows = {}
    if trigger is not None:
        for key in ("before_window", "after_window"):
            w = trigger.context.get(key)
            if isinstance(w, dict) and {"period", "start_ms", "end_ms"} <= w.keys():
                p = int(str(w["period"]))
                windows[key] = (_whole(match_minute(p, int(str(w["start_ms"])))),
                                _whole(match_minute(p, int(str(w["end_ms"])))))  # fmt: skip
    if "before_window" in windows:
        values["bs"], values["be"] = windows["before_window"]

    return Facts(rule=rule, period=insight.period, minute=minute, values=values,
                 goals_before=goals_before, goals_all=goals, side=side,
                 has_player=player_id is not None)  # fmt: skip


# Beats --------------------------------------------------------------------------------------


def _render(parts: Sequence[Lang], values: Mapping[str, object], language: str) -> str:
    return " ".join(fill(p[language], values, language) for p in parts if p[language])


def _goal_list(goals: Sequence[Goal], language: str) -> str:
    items = [f"{g.scorer} {g.label}" for g in goals]
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + AND[language] + items[-1]


def _for_against(facts: Facts, home: str, away: str) -> tuple[int, int] | None:
    h, a = _num(facts.values.get(home)), _num(facts.values.get(away))
    if h is None or a is None or facts.side is None:
        return None
    return (int(h), int(a)) if facts.side == "home" else (int(a), int(h))


def _better(values: Mapping[str, object], hx: str, ax: str) -> str | None:
    """The side with clearly more xG, or None when it is close."""
    h, a = _num(values.get(hx)) or 0.0, _num(values.get(ax)) or 0.0
    if abs(h - a) < CLEAR_XG:
        return None
    return str(values["home"] if h > a else values["away"])


def _buildup(facts: Facts, numbers: bool) -> tuple[list[Lang], dict[str, object]]:
    v = facts.values
    extra: dict[str, object] = {}
    if facts.rule == "half_time_recap":
        firsts = [g for g in facts.goals_all if g.period == 1]
        extra["n_goals"] = len(firsts)
        part = (
            FIRST_HALF_NONE
            if not firsts
            else FIRST_HALF_ONE
            if len(firsts) == 1
            else FIRST_HALF_GOALS
        )
        return [part], extra | {"goals": firsts}
    if facts.rule == "full_time_recap":
        seconds = [g for g in facts.goals_all if g.period == 2]
        return [AT_HALF_TIME, SECOND_HALF_GOALS if seconds else SECOND_HALF_NONE], {
            "goals": seconds
        }

    parts: list[Lang] = []
    bh, ba = int(str(v["bh"])), int(str(v["ba"]))
    goals = facts.goals_before
    if int(str(v["cut"])) < EARLY and bh == ba == 0:
        parts.append(KICK_OFF)
    elif numbers:
        parts.append(SCORE_BEFORE if not goals else SCORE_BEFORE_GOALS
                     if len(goals) <= MAX_LISTED else SCORE_BEFORE_LATEST)  # fmt: skip
        extra["goals"] = goals if len(goals) <= MAX_LISTED else goals[-1:]
    elif bh == ba == 0:
        parts.append(GOALLESS)
    else:
        extra["last_scorer"] = goals[-1].scorer if goals else "?"
        if bh == ba:
            parts.append(LEVEL)
        else:
            extra |= {"leader": v["home"] if bh > ba else v["away"],
                      "hi": max(bh, ba), "lo": min(bh, ba)}  # fmt: skip
            parts.append(LEADING)
    if "red_team" in v:
        parts.append(RED_BEFORE)
    if facts.rule in MOMENTUM_KEYS and "possession_before" in v and "team" in v:
        if numbers and "bs" in v and "control_before" in v:
            parts.append(WINDOW_BEFORE)
            if "pass_accuracy_before" in v:
                parts.append(PASSING_BEFORE)
        else:
            parts.append(ON_TOP if (_num(v["possession_before"]) or 0) >= 50 else HOLDING)
    elif int(str(v["cut"])) >= EARLY:
        if numbers:
            parts.append(SHOTS)
            if "hp" in v and "ap" in v and int(str(v["cut"])) >= WINDOW_MIN:
                parts.append(POSSESSION)
        elif int(str(v["hs"])) + int(str(v["as"])) <= 1:
            parts.append(RARE)
        elif better := _better(v, "hx", "ax"):
            parts.append(BETTER)
            extra["better"] = better
        else:
            parts.append(EVEN)
    return parts, extra


def _outlook(facts: Facts, numbers: bool) -> tuple[list[Lang], dict[str, object]]:
    v = facts.values
    left = int(str(v["left"]))
    extra: dict[str, object] = {}
    parts: list[Lang] = []
    rule = facts.rule
    score = _for_against(facts, "sh", "sa")

    if rule == "goal" and score is not None:
        f, a = score
        if f == a:
            parts.append(LATE_LEVELLER if left <= LATE else ALL_SQUARE)
        elif f - a == 1:
            parts.append(LATE_WINNER if left <= LATE else NEED_ANSWER)
        elif f > a:
            parts.append(PULLING_CLEAR)
            extra["deficit"] = f - a
        else:
            parts.append(BACK_IN_IT if a - f == 1 else STILL_BEHIND)
            extra["deficit"] = a - f
        if _num(v.get("player_goals")) == 2:
            parts.append(HAT_TRICK_ON)
        tx, ox = _num(v.get("team_xg")), _num(v.get("opponent_xg"))
        if numbers and tx is not None and ox is not None and tx != ox:
            parts.append(XG_FOR if tx > ox else XG_AGAINST)
    elif rule == "penalty_awarded" and score is not None:
        f, a = score
        parts.append(PEN_LEAD if f == a else PEN_MORE if f > a else PEN_LEVEL if a - f == 1
                     else PEN_CLOSER)  # fmt: skip
        if numbers:
            home_up = facts.side == "home"
            extra |= {"pen_home": int(str(v["sh"])) + home_up,
                      "pen_away": int(str(v["sa"])) + (not home_up)}  # fmt: skip
            parts.append(PEN_SCORE)
    elif rule == "sending_off" and score is not None:
        f, a = score
        extra.setdefault("minutes_left", v.get("minutes_left", left))
        extra.setdefault("players_left", v.get("players_left", 10))
        parts.append(RED_CHASE if f < a else RED_PROTECT if f > a else RED_HOLD)
        if numbers:
            parts.append(EXTRA_MAN)
    elif rule == "sending_off_impact" and "team" in v:
        parts.append(TEN_MEN_RISK)
        if numbers and {"opponent_shots_after", "opponent_xg_after"} <= v.keys():
            parts.append(OPP_OUTPUT)
    elif rule == "momentum_shift" and "team" in v:
        parts.append(GIVEAWAYS_RISK)
        if score is not None:
            f, a = score
            parts.append(LEAD_TESTED if f > a else WAY_BACK if f < a else LEVEL_DECIDER)
        if numbers and {"opponent_shots_after", "opponent_xg_after"} <= v.keys():
            parts.append(OPP_OUTPUT)
    elif rule == "half_time_recap":
        h, a = int(str(v["sh"])), int(str(v["sa"]))
        if h == a:
            parts.append(HT_OPEN)
        else:
            extra |= {"trailer": v["away"] if h > a else v["home"], "deficit": abs(h - a)}
            parts.append(HT_NEED_ONE if abs(h - a) == 1 else HT_NEED)
        if numbers and "xg_home" in v and "xg_away" in v:
            better = _better(v, "xg_home", "xg_away")
            extra["better"] = better or ""
            parts.append(HT_XG if better else HT_XG_EVEN)
    elif rule == "full_time_recap":
        h, a = int(str(v["sh"])), int(str(v["sa"]))
        winner = v["home"] if h > a else v["away"] if a > h else None
        extra["winner"] = winner or ""
        parts.append(FT_WIN if winner else FT_DRAW)
        if numbers and "xg_home" in v and "xg_away" in v:
            better = _better(v, "xg_home", "xg_away")
            if better and winner:
                extra["better"] = better
                parts.append(FT_DESERVED if better == winner else FT_HARD_DONE)
            elif better:
                extra["better"] = better
                parts.append(HT_XG)
    elif rule in FEAT_OUTLOOK and "player" in v and "team" in v:
        parts.append(FEAT_OUTLOOK[rule])
        if facts.period in (1, 2):
            parts.append(SCORE_NOW)
    elif "team" in v:
        parts.append(SCORE_NOW)
    return parts, extra


def _title(facts: Facts, insight: Insight, language: str) -> str:
    if language == "en":
        return insight.headline
    template = TITLES.get(facts.rule, TITLE_FALLBACK)[language]
    try:
        return fill(template, facts.values, language)
    except (KeyError, ValueError):
        return TITLE_FALLBACK[language]


def _language_values(facts: Facts, extra: Mapping[str, object], language: str) -> dict[str, object]:
    values = {**facts.values, **extra}
    left = int(str(values["left"]))
    values["remaining"] = fill(TIME_LEFT[language] if left >= 2 else TIME_UP[language], values,
                               language)  # fmt: skip
    goals = extra.get("goals")
    if isinstance(goals, list):
        values["goal_list"] = _goal_list(goals, language)
    shots = _num(values.get("opponent_shots_after"))
    values["shot_word"] = SHOT_WORD[language][0 if shots == 1 else 1]
    return values


def _allowed(values: Mapping[str, object], goals: Sequence[Goal]) -> list[float]:
    """Every number a beat may print: the computed values, and the goal minutes it lists."""
    found = [n for x in values.values() if (n := _num(x)) is not None]
    return found + [float(n) for g in goals for n in re.findall(r"\d+", g.label)]


def _checked(parts: Sequence[Lang], values: Mapping[str, object], facts: Facts,
             language: str) -> str:  # fmt: skip
    """The rendered beat, or "" when it would print a number nothing computed (a template
    bug, never a reader's problem)."""
    text = _render(parts, values, language)
    bad = unsupported_numbers(text, _allowed(values, facts.goals_all), language)
    if bad:
        log.warning("story beat for %s dropped: unsupported numbers %s", facts.rule, bad)
        return ""
    return text


def build_narratives(
    data: MatchData, insight: Insight, trigger: Trigger | None, overlays: Sequence[Overlay]
) -> list[StoryNarrative]:
    """One story per audience mode and language the insight aired in."""
    facts = story_facts(data, insight, trigger)
    texts = {(o.audience_mode.value, o.language): o.text for o in overlays}
    out = []
    for mode in STORY_MODES:
        numbers = mode == NUMBERS_MODE
        build_parts, build_extra = _buildup(facts, numbers)
        out_parts, out_extra = _outlook(facts, numbers)
        for language in LANGUAGES:
            moment = texts.get((mode, language)) or texts.get(("casual", language))
            if moment is None:
                if language != "en":
                    continue  # never aired in this language
                moment = insight.explanation
            try:
                buildup = _checked(build_parts, _language_values(facts, build_extra, language),
                                   facts, language)  # fmt: skip
                outlook = _checked(out_parts, _language_values(facts, out_extra, language),
                                   facts, language)  # fmt: skip
            except (KeyError, ValueError):  # a value this rule's trigger never recorded
                log.warning("story for %s: a template value is missing", insight.insight_id)
                buildup, outlook = "", ""
            out.append(StoryNarrative(mode=mode, language=language,
                                      title=_title(facts, insight, language),
                                      buildup=buildup, moment=moment, outlook=outlook))  # fmt: skip
    return out
