"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, type Timeline, type TrackingWindow } from "@/lib/api";

/**
 * One replay clock across both halves, in seconds: the first half runs from 0 to its end,
 * the second half starts where the first ended. The API supplies every position and number;
 * this module only places them on the clock (and interpolates positions between samples).
 */
export interface Axis {
  starts: Record<number, number>; // period -> axis seconds at kick-off
  ends: Record<number, number>; // period -> axis seconds at the final whistle
  total: number;
}

export function buildAxis(timeline: Timeline): Axis {
  const starts: Record<number, number> = {};
  const ends: Record<number, number> = {};
  let offset = 0;
  for (const { period, end_ms } of timeline.periods) {
    starts[period] = offset;
    offset += end_ms / 1000;
    ends[period] = offset;
  }
  return { starts, ends, total: offset };
}

export function toAxis(axis: Axis, period: number, clockMs: number): number {
  return (axis.starts[period] ?? 0) + clockMs / 1000;
}

export function fromAxis(axis: Axis, t: number): { period: number; clockMs: number } {
  const periods = Object.keys(axis.starts).map(Number).sort();
  let period = periods[0] ?? 1;
  for (const p of periods) if (t >= axis.starts[p]) period = p;
  return { period, clockMs: Math.max(0, (t - axis.starts[period]) * 1000) };
}

/** Match minute as the engine counts it (period 2 clock 15:00 is minute 60). */
export function minuteAt(axis: Axis, t: number): number {
  const { period, clockMs } = fromAxis(axis, t);
  return (period === 2 ? 45 : 0) + clockMs / 60000;
}

/** A match minute (e.g. from `?t=68.5`) on the replay clock. */
export function axisFromMinute(axis: Axis, minute: number): number {
  const period = minute > 45 && axis.starts[2] !== undefined ? 2 : 1;
  const clock = (minute - (period === 2 ? 45 : 0)) * 60;
  return Math.min(axis.total, Math.max(0, (axis.starts[period] ?? 0) + clock));
}

/** Index of the last event at or before `t` (events are in feed order). */
export function lastIndexAt(times: number[], t: number): number {
  let lo = 0;
  let hi = times.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

/** The replay clock: advances `speed` match seconds per real second while playing. */
export function useReplayClock(total: number, speed: number, playing: boolean, onEnd: () => void) {
  const [t, setState] = useState(0);
  const current = useRef(0);
  const end = useRef(onEnd);
  useEffect(() => {
    end.current = onEnd;
  }, [onEnd]);
  const setT = useCallback((value: number) => {
    current.current = value;
    setState(value);
  }, []);
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const next = Math.min(total, current.current + ((now - last) / 1000) * speed);
      last = now;
      setT(next);
      if (next >= total) end.current();
      else frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, speed, total, setT]);
  return [t, setT] as const;
}

const CHUNK_S = 300;

/**
 * Player positions near the clock: five-minute chunks from the API, fetched for the
 * current chunk and the next one. Returns positions interpolated between seconds.
 */
export function useTracking(matchId: string, period: number, second: number) {
  const [chunks, setChunks] = useState<Record<string, TrackingWindow>>({});
  const [retry, setRetry] = useState(0);
  const pending = useRef(new Set<string>());

  const ensure = useCallback(
    (p: number, index: number) => {
      const key = `${p}:${index}`;
      if (index < 0 || pending.current.has(key)) return;
      pending.current.add(key);
      api.tracking(matchId, p, index * CHUNK_S, index * CHUNK_S + CHUNK_S - 1).then(
        (w) => setChunks((c) => ({ ...c, [key]: w })),
        () => {
          pending.current.delete(key); // a failed chunk is fetched again shortly
          setTimeout(() => setRetry((r) => r + 1), 3000);
        },
      );
    },
    [matchId],
  );

  const index = Math.floor(second / CHUNK_S);
  useEffect(() => {
    ensure(period, index);
    ensure(period, index + 1);
  }, [ensure, period, index, retry]);

  return useCallback(
    (p: number, s: number): Map<string, { x: number; y: number }> => {
      const w = chunks[`${p}:${Math.floor(s / CHUNK_S)}`];
      const positions = new Map<string, { x: number; y: number }>();
      if (!w) return positions;
      const i = Math.floor(s) - w.from_s;
      const a = w.frames[i];
      const b = w.frames[i + 1] ?? a;
      if (!a) return positions;
      const f = s - Math.floor(s);
      w.players.forEach((pid, k) => {
        const ax = a[2 * k];
        const ay = a[2 * k + 1];
        if (ax === null || ay === null) return;
        const bx = b?.[2 * k] ?? ax;
        const by = b?.[2 * k + 1] ?? ay;
        positions.set(pid, { x: ax + ((bx as number) - ax) * f, y: ay + ((by as number) - ay) * f });
      });
      return positions;
    },
    [chunks],
  );
}
