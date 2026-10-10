import type { TimelineEvent } from "@/lib/api";

import { type Axis, toAxis } from "@/components/match-centre/replay";

/**
 * The ball has no tracking of its own: its path is built from the events. Passes, shots and
 * clearances fly from start to end over their travel time; a player who has the ball keeps it
 * at their feet (their tracking position); a loose ball rolls to where it is next played; and
 * while play is stopped (ball out, foul, goal, substitution) it rests faded until the restart.
 */
interface Point {
  x: number;
  y: number;
}

export type Segment =
  | { kind: "flight"; t0: number; t1: number; a: Point; b: Point }
  | { kind: "hold"; t0: number; t1: number; a: Point; b: Point; player: string; dir: number }
  | { kind: "dead"; t0: number; t1: number; a: Point; b: Point; switchAt: number };

export interface BallState extends Point {
  dead: boolean;
}

/** Events where a player plays or collects the ball at the event location. */
const TOUCHES = new Set([
  "pass", "shot", "clearance", "carry", "dribble", "ball_receipt", "ball_recovery",
  "interception", "goalkeeper", "block", "tackle",
]);  // prettier-ignore
const FLIGHTS = new Set(["pass", "shot", "clearance"]);
const RUNS = new Set(["carry", "dribble"]);
const STOPPAGES = new Set(["out_of_play", "foul", "card", "substitution", "period_end"]);
/** After these the player still has the ball (unless the outcome says they lost it). */
const KEEPS = new Set(["ball_receipt", "ball_recovery", "interception", "goalkeeper", "tackle", "carry", "dribble"]);
const LOST = new Set(["miscontrol", "dispossessed", "fail", "lost"]);
const LONG_GAP_S = 20; // no event for this long: play has stopped
const SET_UP_S = 5; // a dead ball moves to the restart spot this long before the restart
const PITCH_X = 105;
const NET_DEPTH = 1; // a goal rests just behind the goal line
const FEET_M = 1.9; // a held ball sits this far in front of the player (toward the goal they attack)
const TOUCH_S = 0.4; // ...easing out and back over this long, so it starts and ends at the event spots

interface Touch {
  e: TimelineEvent;
  t: number;
  start: Point;
  end: Point;
  stopAfter: boolean; // play stops between this touch and the next
}

export function ballPath(axis: Axis, events: TimelineEvent[], homeTeamId: string): Segment[] {
  const segments: Segment[] = [];
  // Broadcast frame: the home side attacks toward x = 105 in the first half.
  const dirOf = (team: string | null, period: number) => ((team === homeTeamId) === (period === 1) ? 1 : -1);
  const periods = [...new Set(events.map((e) => e.period))];
  for (const period of periods) {
    const touches: Touch[] = [];
    for (const e of events) {
      if (e.period !== period) continue;
      if (STOPPAGES.has(e.type) && touches.length) touches[touches.length - 1].stopAfter = true;
      if (!TOUCHES.has(e.type) || e.x === null || e.y === null) continue;
      const start = { x: e.x, y: e.y };
      const goal = e.type === "shot" && e.outcome === "goal";
      let end = e.end_x !== null && e.end_y !== null ? { x: e.end_x, y: e.end_y } : start;
      if (goal) end = { x: end.x < PITCH_X / 2 ? -NET_DEPTH : PITCH_X + NET_DEPTH, y: end.y }; // in the net
      touches.push({ e, t: toAxis(axis, e.period, e.clock_ms), start, end, stopAfter: goal });
    }
    if (!touches.length) continue;
    const kickOff = axis.starts[period] ?? 0;
    segments.push({ kind: "flight", t0: kickOff, t1: touches[0].t, a: touches[0].start, b: touches[0].start });

    touches.forEach((touch, k) => {
      const { e, t, start, end, stopAfter } = touch;
      const next = touches[k + 1];
      const nextT = next?.t ?? axis.ends[period] ?? t;
      const travel = Math.min((e.duration_ms ?? 0) / 1000, Math.max(0, nextT - t));
      let rest = start;
      let restT = t;
      if (FLIGHTS.has(e.type)) {
        // A pass that does not arrive, with play going on, is cut out where the next touch is.
        const cutOut = e.type === "pass" && e.outcome !== "complete" && next && !stopAfter;
        rest = cutOut ? next.start : end;
        restT = cutOut ? nextT : t + travel;
        segments.push({ kind: "flight", t0: t, t1: restT, a: start, b: rest });
      } else if (RUNS.has(e.type) && e.player_id) {
        rest = end;
        restT = t + travel;
        segments.push({ kind: "hold", t0: t, t1: restT, a: start, b: end, player: e.player_id, dir: dirOf(e.team_id, period) });
      }
      if (!next) {
        segments.push({ kind: "dead", t0: restT, t1: nextT, a: rest, b: rest, switchAt: nextT });
        return;
      }
      if (stopAfter || nextT - restT > LONG_GAP_S) {
        const switchAt = Math.max(restT, nextT - SET_UP_S);
        segments.push({ kind: "dead", t0: restT, t1: nextT, a: rest, b: next.start, switchAt });
      } else if (
        KEEPS.has(e.type) && !LOST.has(e.outcome ?? "") && e.player_id && e.player_id === next.e.player_id
      ) {
        const dir = dirOf(e.team_id, period);
        segments.push({ kind: "hold", t0: restT, t1: nextT, a: rest, b: next.start, player: e.player_id, dir });
      } else if (restT < nextT) {
        segments.push({ kind: "flight", t0: restT, t1: nextT, a: rest, b: next.start }); // loose ball
      }
    });
  }
  return segments;
}

const lerp = (a: Point, b: Point, f: number): Point => ({ x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f });

/**
 * The ball at replay time `t`. `playerAt` gives a player's tracking position at a replay
 * time (undefined when that stretch of tracking is not loaded yet).
 */
export function ballAt(
  path: Segment[],
  t: number,
  playerAt: (pid: string, t: number) => Point | undefined,
): BallState | null {
  let lo = 0;
  let hi = path.length - 1;
  let i = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (path[mid].t0 <= t) {
      i = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  const seg = path[Math.max(0, i)];
  if (!seg) return null;
  const f = Math.min(1, Math.max(0, (t - seg.t0) / Math.max(0.001, seg.t1 - seg.t0)));
  if (seg.kind === "dead") return { ...(t < seg.switchAt ? seg.a : seg.b), dead: true };
  if (seg.kind === "flight") return { ...lerp(seg.a, seg.b, f), dead: false };
  // At the player's feet: their movement plus an offset that blends from where they took the
  // ball to where they play it next, so the ball never jumps at either end.
  const now = playerAt(seg.player, t);
  if (!now) return { ...lerp(seg.a, seg.b, f), dead: false };
  const from = playerAt(seg.player, seg.t0) ?? now;
  const to = playerAt(seg.player, seg.t1) ?? now;
  const offset = lerp({ x: seg.a.x - from.x, y: seg.a.y - from.y }, { x: seg.b.x - to.x, y: seg.b.y - to.y }, f);
  const feet = FEET_M * seg.dir * Math.max(0, Math.min(1, (t - seg.t0) / TOUCH_S, (seg.t1 - t) / TOUCH_S));
  return { x: now.x + offset.x + feet, y: now.y + offset.y, dead: false };
}
