"""Phrase fields: the words that turn a statistic into context, chosen in code.

A copy template may contain ``{state}``, ``{shots_outcome}`` and similar fields. Each one
names a selector here that reads the trigger's metrics (and the subject's side) and
returns a phrase per language. A phrase may itself hold placeholders (names or metric
numbers), so every number still comes from the metrics and passes the number checks.

A selector returns ``None`` (rendered as nothing) when the metrics it needs are missing,
so triggers recorded before a metric existed still render, just with less context.
Phrases describe what the numbers show; they never claim causes, history or the future.
"""

from collections.abc import Callable, Mapping

Phrase = dict[str, str]  # language -> template text
Values = Mapping[str, object]


def _p(en: str, es: str, fil: str) -> Phrase:
    return {"en": en, "es": es, "fil": fil}


def _num(values: Values, key: str) -> float | None:
    v = values.get(key)
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _for_against(values: Values) -> tuple[float, float] | None:
    """The subject team's score and the opponent's, after the moment."""
    home, away, side = _num(values, "score_home"), _num(values, "score_away"), values.get("side")
    if home is None or away is None or side not in ("home", "away"):
        return None
    return (home, away) if side == "home" else (away, home)


# Goals ------------------------------------------------------------------------------------


def _goal_situation(values: Values) -> str | None:
    score = _for_against(values)
    if score is None:
        return None
    mine, theirs = score
    if mine == 1 and theirs == 0:
        return "opener"
    if mine == theirs:
        return "equaliser"
    if mine == theirs + 1:
        return "lead"
    if mine > theirs + 1:
        return "extend"
    return "reduce"


def state(values: Values) -> Phrase | None:
    """What the goal does to the match."""
    return {
        "opener": _p("opens the scoring", "abre el marcador", "nagbukas ng iskor"),
        "equaliser": _p("levels the score", "iguala el partido", "nagpantay ng iskor"),
        "lead": _p(
            "puts {team} ahead", "pone por delante a {team}", "nagbigay ng lamang sa {team}"
        ),
        "extend": _p(
            "extends {team}'s lead", "amplía la ventaja de {team}", "nagpalaki ng lamang ng {team}"
        ),
        "reduce": _p(
            "pulls one back for {team}",
            "recorta distancias para {team}",
            "nagbawas ng agwat para sa {team}",
        ),
    }.get(
        _goal_situation(values) or "",
        _p("scores for {team}", "marca para {team}", "umiskor para sa {team}"),
    )


def goal_before(values: Values) -> Phrase | None:
    """The score before the goal, in words (a signal for the models: it settles whether a
    goal opened, levelled, led or extended)."""
    before = {
        "opener": "the match was goalless before it",
        "equaliser": "{team} were one goal behind before it",
        "lead": "the score was level before it",
        "extend": "{team} were already ahead before it",
        "reduce": "{team} were behind before it and still are",
    }.get(_goal_situation(values) or "")
    return _p(before, before, before) if before else None  # signals are English only


def club_goal(values: Values) -> Phrase | None:
    """The goal as the subject team's supporters would hear it."""
    return {
        "opener": _p(
            "{team} strike first: {player} scores on {minute:0}'.",
            "{team} golpea primero: marca {player} en el {minute:0}'.",
            "Unang tumama ang {team}: umiskor si {player} sa {minute:0}'.",
        ),
        "equaliser": _p(
            "{team} are level! {player} makes it {score_home}-{score_away}.",
            "¡{team} empata! {player} pone el {score_home}-{score_away}.",
            "Pantay na ang {team}! {player} para sa {score_home}-{score_away}.",
        ),
        "lead": _p(
            "{team} lead! {player} makes it {score_home}-{score_away}.",
            "¡{team} manda! {player} pone el {score_home}-{score_away}.",
            "Lamang na ang {team}! {player} para sa {score_home}-{score_away}.",
        ),
        "extend": _p(
            "Breathing room for {team}: {player} makes it {score_home}-{score_away}.",
            "Más margen para {team}: {player} pone el {score_home}-{score_away}.",
            "Mas maluwag ang {team}: {player} para sa {score_home}-{score_away}.",
        ),
        "reduce": _p(
            "Hope for {team}: {player} pulls it back to {score_home}-{score_away}.",
            "Esperanza para {team}: {player} recorta al {score_home}-{score_away}.",
            "May pag-asa ang {team}: {player}, {score_home}-{score_away} na.",
        ),
    }.get(
        _goal_situation(values) or "",
        _p(
            "{team} goal: {player} on {minute:0}'.",
            "Gol de {team}: {player} en el {minute:0}'.",
            "Goal ng {team}: {player} sa {minute:0}'.",
        ),
    )


def move(values: Values) -> Phrase | None:
    """A goal scored soon after winning the ball."""
    if _num(values, "seconds_from_regain") is None:
        return None
    return _p(
        " {seconds_from_regain} seconds after {team} won the ball",
        " {seconds_from_regain} segundos después de recuperar el balón",
        " {seconds_from_regain} segundo matapos mabawi ng {team} ang bola",
    )


def _season_tally(values: Values) -> Phrase | None:
    """The goal in the scorer's league season (only with season facts from earlier matches)."""
    goals, season = _num(values, "player_goals"), _num(values, "season_goals")
    if goals is None or season is None:
        return None
    if goals == 3 and _num(values, "season_hat_tricks_before") == 0:
        return _p(
            "It completes the first hat-trick of the league season",
            "Con él completa el primer hat-trick de la temporada en la liga",
            "Kumpleto na ang unang hat-trick ng season sa liga",
        )
    if season == 1:
        return _p(
            "It is the scorer's first league goal of the season",
            "Es su primer gol de liga en la temporada",
            "Ito ang unang goal niya sa liga ngayong season",
        )
    if season <= goals:  # every league goal this season came today: the match tally says it
        return None
    if goals == 2:
        return _p(
            "It is the scorer's second goal of the match and takes them to {season_goals} "
            "league goals this season",
            "Es su segundo gol del partido y ya suma {season_goals} goles de liga esta temporada",
            "Ikalawang goal na niya ito sa laban, {season_goals} na sa liga ngayong season",
        )
    if goals == 1:
        return _p(
            "It takes the scorer to {season_goals} league goals this season",
            "Ya suma {season_goals} goles de liga esta temporada",
            "{season_goals} goal na niya sa liga ngayong season",
        )
    return None


def _tally(values: Values) -> Phrase | None:
    if (season := _season_tally(values)) is not None:
        return season
    goals = _num(values, "player_goals")
    if goals is None or goals < 2:
        return None
    if goals == 2:
        return _p(
            "It is the scorer's second goal of the match",
            "Es su segundo gol del partido",
            "Ikalawang goal na niya ito sa laban",
        )
    if goals == 3:
        return _p(
            "It completes a hat-trick",
            "Con él completa un hat-trick",
            "Kumpleto na ang hat-trick niya",
        )
    return _p(
        "The scorer now has {player_goals} goals in the match",
        "Ya suma {player_goals} goles en el partido",
        "{player_goals} goal na niya sa laban",
    )


def goal_headline(values: Values) -> Phrase | None:
    """A hat-trick leads the headline; otherwise what the goal does to the match."""
    if _num(values, "player_goals") == 3:
        return _p(
            "{player} completes a hat-trick: {score_home}-{score_away}",
            "{player} completa un hat-trick: {score_home}-{score_away}",
            "Hat-trick ni {player}: {score_home}-{score_away}",
        )
    return _p(
        "{player} {state} at {score_home}-{score_away}",
        "{player} {state}: {score_home}-{score_away}",
        "{player}, {state}: {score_home}-{score_away}",
    )


def _chances(values: Values) -> Phrase | None:
    mine, theirs = _num(values, "team_xg"), _num(values, "opponent_xg")
    if mine is None or theirs is None:
        return None
    if mine > theirs:
        return _p(
            "{team} have had the better chances so far, {team_xg} xG to {opponent_xg}",
            "{team} ha generado más peligro: {team_xg} xG contra {opponent_xg}",
            "Mas maganda ang tsansa ng {team} sa ngayon, {team_xg} xG laban sa {opponent_xg}",
        )
    if mine < theirs:
        return _p(
            "{opp} have still created more, {opponent_xg} xG to {team_xg}",
            "{opp} sigue generando más: {opponent_xg} xG contra {team_xg}",
            "Mas marami pa ring tsansa ang {opp}, {opponent_xg} xG laban sa {team_xg}",
        )
    return _p(
        "the chances are level at {team_xg} xG each",
        "las ocasiones están igualadas, {team_xg} xG cada uno",
        "pantay ang tsansa, {team_xg} xG bawat isa",
    )


def goal_context(values: Values) -> Phrase | None:
    """One sentence on what the goal means beyond the score: tally, then chances."""
    tally, chances = _tally(values), _chances(values)
    if tally and chances:
        return _p(
            f"{tally['en']}, and {chances['en']}.",
            f"{tally['es']}; {chances['es']}.",
            f"{tally['fil']}, at {chances['fil']}.",
        )
    if tally:
        return {k: v + "." for k, v in tally.items()}
    if chances:
        return {k: v[0].upper() + v[1:] + "." for k, v in chances.items()}
    return None


def casual_tally(values: Values) -> Phrase | None:
    goals = _num(values, "player_goals")
    if goals == 2:
        return _p(" with a second goal", " con su segundo gol", " sa ikalawang goal niya")
    if goals is not None and goals >= 3:
        return _p(" to complete a hat-trick", " y completa un hat-trick", " para sa hat-trick")
    return None


def player_goal(values: Values) -> Phrase | None:
    """The goal from the scorer's side: their tally (in the match, or the league season when
    earlier matches add to it), the chance and the move."""
    goals, season = _num(values, "player_goals"), _num(values, "season_goals")
    if goals == 3 and season is not None and _num(values, "season_hat_tricks_before") == 0:
        return _p(
            "the first hat-trick of the league season, completed on {minute:0}' from a {xg} xG "
            "chance.",
            "el primer hat-trick de la temporada en la liga, completado en el {minute:0}' con "
            "una ocasión de {xg} xG.",
            "ang unang hat-trick ng season sa liga, kumpleto sa {minute:0}' mula sa {xg} xG na "
            "tsansa.",
        )
    if goals == 1 and season == 1:
        return _p(
            "a first league goal of the season, on {minute:0}' from a {xg} xG chance.",
            "primer gol de liga de la temporada, en el {minute:0}' con una ocasión de {xg} xG.",
            "unang goal sa liga ngayong season, sa {minute:0}' mula sa {xg} xG na tsansa.",
        )
    if goals == 1 and season is not None and season > 1:
        return _p(
            "scores on {minute:0}' from a {xg} xG chance: {season_goals} league goals this season.",
            "marca en el {minute:0}' con una ocasión de {xg} xG: {season_goals} goles de liga esta "
            "temporada.",
            "umiskor sa {minute:0}' mula sa {xg} xG na tsansa: {season_goals} goal na sa liga "
            "ngayong season.",
        )
    if goals == 3:
        return _p(
            "hat-trick completed on {minute:0}' from a {xg} xG chance{move}.",
            "hat-trick completado en el {minute:0}' con una ocasión de {xg} xG.",
            "kumpleto ang hat-trick sa {minute:0}' mula sa {xg} xG na tsansa.",
        )
    if goals == 2:
        return _p(
            "a second goal of the match, on {minute:0}' from a {xg} xG chance{move}.",
            "segundo gol del partido, en el {minute:0}' con una ocasión de {xg} xG.",
            "ikalawang goal sa laban, sa {minute:0}' mula sa {xg} xG na tsansa.",
        )
    return _p(
        "scores on {minute:0}' from a {xg} xG chance{move}.",
        "marca en el {minute:0}' con una ocasión de {xg} xG.",
        "umiskor sa {minute:0}' mula sa {xg} xG na tsansa.",
    )


def match_xg(values: Values) -> Phrase | None:
    if _num(values, "team_xg") is None or _num(values, "opponent_xg") is None:
        return None
    return _p(
        " Match xG: {team} {team_xg}, {opp} {opponent_xg}.",
        " xG del partido: {team} {team_xg}, {opp} {opponent_xg}.",
        " xG ng laban: {team} {team_xg}, {opp} {opponent_xg}.",
    )


# Spells (momentum shifts and sending-off impact) ---------------------------------------------


def _opp_shots(values: Values) -> float | None:
    return _num(values, "opponent_shots_after")


def shots_outcome(values: Values) -> Phrase | None:
    """Whether the opponent turned the spell into shots."""
    n = _opp_shots(values)
    if n is None:
        return None
    if n == 0:
        return _p(
            "{opp} have not yet turned it into a shot.",
            "{opp} todavía no lo ha convertido en un disparo.",
            "Wala pang naitirang shot ang {opp} mula rito.",
        )
    if n == 1:
        return _p(
            "{opp} have had only one shot from it so far ({opponent_xg_after} xG).",
            "{opp} solo ha disparado una vez hasta ahora ({opponent_xg_after} xG).",
            "Isang shot pa lang ang {opp} mula rito ({opponent_xg_after} xG).",
        )
    if n <= 2:
        return _p(
            "{opp} have had only {opponent_shots_after} shots from it so far "
            "({opponent_xg_after} xG).",
            "{opp} solo ha disparado {opponent_shots_after} veces ({opponent_xg_after} xG).",
            "{opponent_shots_after} shot pa lang ang {opp} mula rito ({opponent_xg_after} xG).",
        )
    return _p(
        "{opp} have taken {opponent_shots_after} shots worth {opponent_xg_after} xG in that time.",
        "{opp} ha disparado {opponent_shots_after} veces, {opponent_xg_after} xG, en ese tramo.",
        "{opponent_shots_after} shot na ang {opp}, {opponent_xg_after} xG, sa yugtong iyon.",
    )


def casual_outcome(values: Values) -> Phrase | None:
    n = _opp_shots(values)
    if n is None:
        return None
    if n == 0:
        return _p(
            ", but {opp} haven't had a shot yet",
            ", pero {opp} aún no ha disparado",
            ", pero wala pang shot ang {opp}",
        )
    if n <= 2:
        return _p(
            ", though {opp} have barely threatened",
            ", aunque {opp} apenas ha inquietado",
            ", pero halos hindi pa nakakabanta ang {opp}",
        )
    return _p(
        " and {opp} are getting shots away",
        " y {opp} ya encadena disparos",
        " at sunod-sunod ang shot ng {opp}",
    )


def club_outcome(values: Values) -> Phrase | None:
    n = _opp_shots(values)
    if n is None:
        return None
    if n == 0:
        return _p(
            ", though {opp} have not yet had a shot",
            ", aunque {opp} aún no ha disparado",
            ", pero wala pang shot ang {opp}",
        )
    if n == 1:
        return _p(
            ", though {opp} have had only one shot",
            ", aunque {opp} solo ha disparado una vez",
            ", pero isang shot pa lang ang {opp}",
        )
    return _p(
        " and {opp} have had {opponent_shots_after} shots",
        " y {opp} ya suma {opponent_shots_after} disparos",
        " at {opponent_shots_after} shot na ang {opp}",
    )


def ppda_change(values: Values) -> Phrase | None:
    before, after = _num(values, "opponent_ppda_before"), _num(values, "opponent_ppda_after")
    if before is None or after is None:
        return None
    if after < before:
        return _p(
            "{opp} now allow {opponent_ppda_after} passes per defensive action, down from "
            "{opponent_ppda_before}",
            "{opp} ahora permite {opponent_ppda_after} pases por acción defensiva (antes "
            "{opponent_ppda_before})",
            "{opponent_ppda_after} pasa bawat defensive action na lang ang pinapayagan ng {opp} "
            "(dati {opponent_ppda_before})",
        )
    return _p(
        "{opp} allow {opponent_ppda_after} passes per defensive action (it was "
        "{opponent_ppda_before})",
        "{opp} permite {opponent_ppda_after} pases por acción defensiva (antes "
        "{opponent_ppda_before})",
        "{opponent_ppda_after} pasa bawat defensive action ang pinapayagan ng {opp} (dati "
        "{opponent_ppda_before})",
    )


def ball_share(values: Values) -> Phrase | None:
    before, after = _num(values, "possession_before"), _num(values, "possession_after")
    if before is None or after is None:
        return None
    if after < before:
        return _p("seeing less of the ball", "viendo menos el balón", "mas bihirang may bola")
    return _p(
        "still holding their share of the ball",
        "mantiene su parte del balón",
        "hawak pa rin ang bahagi nila ng bola",
    )


# Penalties and sending-offs ---------------------------------------------------------------


def _level_state(values: Values) -> str | None:
    """Before the moment's consequence: level, ahead or behind (by one or more)."""
    score = _for_against(values)
    if score is None:
        return None
    mine, theirs = score
    if mine == theirs:
        return "level"
    if mine > theirs:
        return "ahead"
    return "behind1" if theirs - mine == 1 else "behind"


def score_note(values: Values) -> Phrase | None:
    return {
        "level": _p(
            " with the score level at {score_home}-{score_away}",
            " con empate a {score_home}-{score_away}",
            " habang pantay sa {score_home}-{score_away}",
        ),
        "ahead": _p(
            " with {team} ahead at {score_home}-{score_away}",
            " con {team} por delante, {score_home}-{score_away}",
            " habang lamang ang {team}, {score_home}-{score_away}",
        ),
        "behind1": _p(
            " with {team} behind at {score_home}-{score_away}",
            " con {team} por detrás, {score_home}-{score_away}",
            " habang talo ang {team}, {score_home}-{score_away}",
        ),
        "behind": _p(
            " with {team} behind at {score_home}-{score_away}",
            " con {team} por detrás, {score_home}-{score_away}",
            " habang talo ang {team}, {score_home}-{score_away}",
        ),
    }.get(_level_state(values) or "")


def at_score(values: Values) -> Phrase | None:
    if _for_against(values) is None:
        return None
    return _p(
        " at {score_home}-{score_away}",
        " con {score_home}-{score_away}",
        " sa {score_home}-{score_away}",
    )


def pen_stakes(values: Values) -> Phrase | None:
    """What scoring the penalty would change."""
    return {
        "level": _p(
            "Scoring it would put {team} ahead.",
            "Si marca, {team} se pondrá por delante.",
            "Kapag naipasok, lalamang ang {team}.",
        ),
        "ahead": _p(
            "Scoring it would extend {team}'s lead.",
            "Si marca, {team} ampliará su ventaja.",
            "Kapag naipasok, lalaki ang lamang ng {team}.",
        ),
        "behind1": _p(
            "Scoring it would draw {team} level.",
            "Si marca, {team} empatará.",
            "Kapag naipasok, papantay ang {team}.",
        ),
        "behind": _p(
            "Scoring it would cut the gap for {team}.",
            "Si marca, {team} recortará distancias.",
            "Kapag naipasok, liliit ang agwat para sa {team}.",
        ),
    }.get(_level_state(values) or "")


def casual_pen(values: Values) -> Phrase | None:
    return {
        "level": _p(" to take the lead", " para adelantarse", " para lumamang"),
        "ahead": _p(
            " to stretch the lead", " para ampliar la ventaja", " para palakihin ang lamang"
        ),
        "behind1": _p(" to draw level", " para empatar", " para pumantay"),
        "behind": _p(" to close the gap", " para recortar", " para lumapit"),
    }.get(_level_state(values) or "")


def time_left(values: Values) -> Phrase | None:
    if _num(values, "minutes_left") is None:
        return None
    return _p(
        " and {minutes_left} minutes of normal time to play",
        " y {minutes_left} minutos de tiempo reglamentario por jugar",
        " at {minutes_left} minuto pa ng regular na oras",
    )


# Player form -------------------------------------------------------------------------------


def trend(values: Values) -> Phrase | None:
    shots, before = _num(values, "shots"), _num(values, "shots_previous")
    if shots is None or before is None:
        return None
    if before < shots:
        return _p(
            " (up from {shots_previous} in the {window_min} minutes before)",
            " (frente a {shots_previous} en los {window_min} minutos anteriores)",
            " (mula {shots_previous} sa naunang {window_min} minuto)",
        )
    return _p(
        " ({shots_previous} in the {window_min} minutes before)",
        " ({shots_previous} en los {window_min} minutos anteriores)",
        " ({shots_previous} sa naunang {window_min} minuto)",
    )


def goal_part(values: Values) -> Phrase | None:
    goals = _num(values, "goals")
    if goals is None:
        return None
    if goals == 0:
        return _p(" but no goal yet", " pero aún sin gol", " pero wala pang goal")
    if goals == 1:
        return _p(" and one goal", " y un gol", " at isang goal")
    return _p(" and {goals} goals", " y {goals} goles", " at {goals} goal")


def form_goals(values: Values) -> Phrase | None:
    goals = _num(values, "goals")
    if goals is None:
        return None
    if goals == 0:
        return _p(", none scored", ", ninguno dentro", ", wala pang pumasok")
    return _p(", {goals} scored", ", {goals} dentro", ", {goals} pumasok")


def share_sentence(values: Values) -> Phrase | None:
    if _num(values, "team_shot_share") is None:
        return None
    return _p(
        "That is {team_shot_share:0}% of {team}'s shots in the spell.",
        "Son el {team_shot_share:0} % de los disparos de {team} en ese tramo.",
        "{team_shot_share:0}% iyon ng mga shot ng {team} sa yugtong iyon.",
    )


def club_form(values: Values) -> Phrase | None:
    if _num(values, "team_shot_share") is not None:
        return _p(
            "{team} keep looking for {player}: {team_shot_share:0}% of their shots lately.",
            "{team} busca a {player}: el {team_shot_share:0} % de sus disparos últimamente.",
            "Kay {player} dumadaan ang {team}: {team_shot_share:0}% ng mga shot nila.",
        )
    return _p(
        "{team} keep looking for {player}: {shots} shots in {window_min} minutes.",
        "{team} busca a {player}: {shots} disparos en {window_min} minutos.",
        "Kay {player} dumadaan ang {team}: {shots} shot sa {window_min} minuto.",
    )


def casual_goal(values: Values) -> Phrase | None:
    goals = _num(values, "goals")
    if goals is None:
        return None
    if goals == 0:
        return _p(", still looking for a goal", ", aún buscando el gol", ", naghahanap pa ng goal")
    return _p(" and has found the net", " y ya ha marcado", " at nakaiskor na")


# Recaps ------------------------------------------------------------------------------------

_CLOSE_XG = 0.3


def _xg_leader(values: Values, home_key: str, away_key: str) -> tuple[str, str, str] | None:
    """('home'|'away', leader xG key, other xG key), or None when close or missing."""
    home, away = _num(values, home_key), _num(values, away_key)
    if home is None or away is None or abs(home - away) < _CLOSE_XG:
        return None
    return ("home", home_key, away_key) if home > away else ("away", away_key, home_key)


def verdict_ft(values: Values) -> Phrase | None:
    """Full time: did the chances back up the result?"""
    if _num(values, "xg_home") is None or _num(values, "xg_away") is None:
        return None
    lead = _xg_leader(values, "xg_home", "xg_away")
    if lead is None:
        return _p(
            "There was little between them on chances, {xg_home} xG to {xg_away}.",
            "Hubo pocas diferencias en ocasiones: {xg_home} xG contra {xg_away}.",
            "Halos pantay sila sa tsansa, {xg_home} xG laban sa {xg_away}.",
        )
    side, mine, theirs = lead
    name = "{" + side + "}"
    h, a = _num(values, "score_home") or 0, _num(values, "score_away") or 0
    won = (h > a) if side == "home" else (a > h)
    if won:
        return _p(
            f"{name} won and had the better chances, {{{mine}}} xG to {{{theirs}}}.",
            f"{name} ganó y tuvo las mejores ocasiones: {{{mine}}} xG contra {{{theirs}}}.",
            f"Nanalo ang {name} at mas maganda ang tsansa, {{{mine}}} xG laban sa {{{theirs}}}.",
        )
    if h == a:
        return _p(
            f"{name} created more, {{{mine}}} xG to {{{theirs}}}, but could not win it.",
            f"{name} generó más, {{{mine}}} xG contra {{{theirs}}}, pero no ganó.",
            f"Mas maraming tsansa ang {name}, {{{mine}}} xG laban sa {{{theirs}}}, pero tabla.",
        )
    return _p(
        f"{name} lost despite creating more, {{{mine}}} xG to {{{theirs}}}.",
        f"{name} perdió pese a generar más: {{{mine}}} xG contra {{{theirs}}}.",
        f"Natalo ang {name} kahit mas marami ang tsansa, {{{mine}}} xG laban sa {{{theirs}}}.",
    )


def casual_verdict_ft(values: Values) -> Phrase | None:
    if _num(values, "xg_home") is None or _num(values, "xg_away") is None:
        return None
    lead = _xg_leader(values, "xg_home", "xg_away")
    if lead is None:
        return _p(
            " Little to choose between them on chances.",
            " Muy igualados en ocasiones.",
            " Halos pantay sa tsansa.",
        )
    name = "{" + lead[0] + "}"
    return _p(
        f" {name} had the better chances.",
        f" {name} tuvo las mejores ocasiones.",
        f" Mas maganda ang naging tsansa ng {name}.",
    )


def verdict(values: Values) -> Phrase | None:
    """Do the chances back up the scoreline?"""
    if _num(values, "xg_home") is None or _num(values, "xg_away") is None:
        return None
    lead = _xg_leader(values, "xg_home", "xg_away")
    if lead is None:
        return _p(
            "There has been little between them on chances, {xg_home} xG to {xg_away}.",
            "Pocas diferencias en ocasiones: {xg_home} xG contra {xg_away}.",
            "Halos pantay sa tsansa, {xg_home} xG laban sa {xg_away}.",
        )
    side, mine, theirs = lead
    name = "{" + side + "}"
    h, a = _num(values, "score_home") or 0, _num(values, "score_away") or 0
    ahead = (h > a) if side == "home" else (a > h)
    level = h == a
    if ahead:
        return _p(
            f"{name} lead on the scoreboard and on chances, {{{mine}}} xG to {{{theirs}}}.",
            f"{name} manda en el marcador y en ocasiones: {{{mine}}} xG contra {{{theirs}}}.",
            f"Lamang ang {name} sa iskor at sa tsansa, {{{mine}}} xG laban sa {{{theirs}}}.",
        )
    if level:
        return _p(
            f"{name} have created more, {{{mine}}} xG to {{{theirs}}}, without making it count.",
            f"{name} ha generado más, {{{mine}}} xG contra {{{theirs}}}, sin reflejarlo.",
            f"Mas maraming tsansa ang {name}, {{{mine}}} xG laban sa {{{theirs}}}, pero pantay pa.",
        )
    return _p(
        f"{name} trail despite creating more, {{{mine}}} xG to {{{theirs}}}.",
        f"{name} pierde pese a generar más: {{{mine}}} xG contra {{{theirs}}}.",
        f"Talo ang {name} kahit mas marami ang tsansa, {{{mine}}} xG laban sa {{{theirs}}}.",
    )


def casual_verdict(values: Values) -> Phrase | None:
    if _num(values, "xg_home") is None or _num(values, "xg_away") is None:
        return None
    lead = _xg_leader(values, "xg_home", "xg_away")
    if lead is None:
        return _p(
            " Little to choose between them on chances.",
            " Muy igualados en ocasiones.",
            " Halos pantay sa tsansa.",
        )
    name = "{" + lead[0] + "}"
    return _p(
        f" {name} have had the better chances.",
        f" {name} ha tenido las mejores ocasiones.",
        f" Mas magaganda ang tsansa ng {name}.",
    )


def second_half(values: Values) -> Phrase | None:
    """Full time: who had the better of the second half on xG."""
    if _num(values, "xg_second_half_home") is None:
        return None
    lead = _xg_leader(values, "xg_second_half_home", "xg_second_half_away")
    if lead is None:
        return _p(
            "The second half was close on xG, {xg_second_half_home} to {xg_second_half_away}.",
            "La segunda parte fue pareja en xG: {xg_second_half_home} contra "
            "{xg_second_half_away}.",
            "Halos pantay ang second half sa xG, {xg_second_half_home} laban sa "
            "{xg_second_half_away}.",
        )
    side, mine, theirs = lead
    name = "{" + side + "}"
    return _p(
        f"After the break {name} created more, {{{mine}}} xG to {{{theirs}}}.",
        f"Tras el descanso {name} generó más: {{{mine}}} xG contra {{{theirs}}}.",
        f"Pagkatapos ng break, mas maraming tsansa ang {name}, {{{mine}}} xG laban sa "
        f"{{{theirs}}}.",
    )


def _h2h(values: Values) -> Phrase | None:
    """The last earlier meeting this season, from today's home club's side (a clause)."""
    mine, theirs = _num(values, "h2h_home_goals"), _num(values, "h2h_away_goals")
    if mine is None or theirs is None:
        return None
    score = "{h2h_home_goals}-{h2h_away_goals}"
    if mine > theirs:
        return _p(
            "{home} won the last meeting this season " + score,
            "{home} ganó el último duelo de esta temporada por " + score,
            "nanalo ang {home} sa huling laban nila ngayong season, " + score,
        )
    if mine < theirs:
        return _p(
            "{home} lost the last meeting this season " + score,
            "{home} perdió el último duelo de esta temporada por " + score,
            "natalo ang {home} sa huling laban nila ngayong season, " + score,
        )
    return _p(
        "the last meeting this season ended " + score,
        "el último duelo de esta temporada acabó " + score,
        score + " ang huling laban nila ngayong season",
    )


def ft_tail(values: Values) -> Phrase | None:
    """Full time, after the verdict: the second half on xG and, when the clubs met earlier
    this season, how that meeting ended (one sentence)."""
    half, meeting = second_half(values), _h2h(values)
    if half is None or meeting is None:
        return half
    return {k: half[k].rstrip(".") + "; " + meeting[k] + "." for k in half}


def home_form_note(values: Values) -> Phrase | None:
    """Full time, for the home club's supporters: its last league matches, today included."""
    if _num(values, "home_form_played") is None:
        return None
    return _p(
        " Last {home_form_played} league matches: won {home_form_wins}, drew {home_form_draws}, "
        "lost {home_form_losses}.",
        " Últimos {home_form_played} partidos de liga: ganó {home_form_wins}, empató "
        "{home_form_draws}, perdió {home_form_losses}.",
        " Huling {home_form_played} laban sa liga: {home_form_wins} panalo, {home_form_draws} "
        "tabla, {home_form_losses} talo.",
    )


def form_note(values: Values) -> Phrase | None:
    """Both clubs' last league matches, today included (a signal for the models)."""
    parts = []
    for side in ("home", "away"):
        if _num(values, f"{side}_form_played") is not None:
            parts.append(
                f"{{{side}}} won {{{side}_form_wins}}, drew {{{side}_form_draws}} and lost "
                f"{{{side}_form_losses}} of their last {{{side}_form_played}} league matches, "
                "today included"
            )
    if not parts:
        return None
    text = "; ".join(parts) + "."
    return _p(text, text, text)  # signals are English only


def h2h_note(values: Values) -> Phrase | None:
    """The earlier meeting this season, as a sentence (a signal for the models)."""
    meeting = _h2h(values)
    if meeting is None:
        return None
    text = meeting["en"][0].upper() + meeting["en"][1:] + "."
    return _p(text, text, text)


def possession_note(values: Values) -> Phrase | None:
    home = _num(values, "possession_home")
    if home is None:
        return None
    side = "home" if home >= 50 else "away"
    return _p(
        f"{{{side}}} have had {{possession_{side}:0}}% of the ball.",
        f"{{{side}}} ha tenido el {{possession_{side}:0}} % del balón.",
        f"Hawak ng {{{side}}} ang bola {{possession_{side}:0}}% ng oras.",
    )


def on_target_pair(values: Values) -> Phrase | None:
    if _num(values, "on_target_home") is None:
        return None
    return _p(
        " ({on_target_home}-{on_target_away} on target)",
        " ({on_target_home}-{on_target_away} a puerta)",
        " ({on_target_home}-{on_target_away} on target)",
    )


# Speed --------------------------------------------------------------------------------------


def speed_chance(values: Values) -> Phrase | None:
    if _num(values, "xg") is None:
        return None
    return _p(
        ", from a chance worth {xg} xG", ", en una ocasión de {xg} xG", ", mula sa tsansang {xg} xG"
    )


SELECTORS: dict[str, Callable[[Values], Phrase | None]] = {
    "state": state,
    "goal_headline": goal_headline,
    "club_goal": club_goal,
    "move": move,
    "goal_context": goal_context,
    "goal_before": goal_before,
    "casual_tally": casual_tally,
    "player_goal": player_goal,
    "match_xg": match_xg,
    "shots_outcome": shots_outcome,
    "casual_outcome": casual_outcome,
    "club_outcome": club_outcome,
    "ppda_change": ppda_change,
    "ball_share": ball_share,
    "score_note": score_note,
    "at_score": at_score,
    "pen_stakes": pen_stakes,
    "casual_pen": casual_pen,
    "time_left": time_left,
    "trend": trend,
    "goal_part": goal_part,
    "form_goals": form_goals,
    "share_sentence": share_sentence,
    "club_form": club_form,
    "casual_goal": casual_goal,
    "verdict": verdict,
    "verdict_ft": verdict_ft,
    "casual_verdict": casual_verdict,
    "casual_verdict_ft": casual_verdict_ft,
    "second_half": second_half,
    "ft_tail": ft_tail,
    "home_form_note": home_form_note,
    "form_note": form_note,
    "h2h_note": h2h_note,
    "possession_note": possession_note,
    "on_target_pair": on_target_pair,
    "speed_chance": speed_chance,
}
"""Every phrase field a template may use."""

SIGNALS = (
    "state",
    "goal_context",
    "move",
    "shots_outcome",
    "ppda_change",
    "score_note",
    "pen_stakes",
    "time_left",
    "trend",
    "share_sentence",
    "verdict",
    "verdict_ft",
    "second_half",
    "ft_tail",
)
"""Phrase fields that, filled in English, tell a model the context of a moment."""

SEASON_SIGNALS = ("form_note", "h2h_note")
"""Season context a model gets whenever its metrics are there, used in a template or not."""


def choose(field: str, values: Values, language: str) -> str:
    """The phrase template for a field in a language ('' when it does not apply)."""
    phrase = SELECTORS[field](values)
    return phrase[language] if phrase else ""
