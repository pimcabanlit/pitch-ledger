"use client";

import { useCallback, useSyncExternalStore } from "react";

import { LANGUAGES, MODES, type Mode } from "@/lib/api";
import { METRIC_KEYS, type MetricKey } from "@/lib/metrics";

/**
 * The viewer's choices, kept in this browser only (localStorage). Storage can be missing or
 * blocked (private windows, previews): every read and write is guarded and the defaults apply.
 * Player and club IDs are stable across matches, so a favourite carries over.
 */
export interface Preferences {
  mode: Mode;
  language: string;
  metric: MetricKey;
  player: string | null; // followed player, e.g. "T01-P09"
  clubs: string[]; // favourite clubs, e.g. ["T01", "T04"]
}

const KEY = "pitch-ledger:preferences:v1";
const CHANGED = "pitch-ledger:preferences";
export const DEFAULTS: Preferences = { mode: "casual", language: "en", metric: "control", player: null, clubs: [] };

const PLAYER = /^T\d{2}-P\d{2}$/;
const CLUB = /^T\d{2}$/;

function parse(raw: string | null): Preferences {
  if (!raw) return DEFAULTS;
  try {
    const v = JSON.parse(raw) as Record<string, unknown>;
    return {
      mode: MODES.includes(v.mode as Mode) ? (v.mode as Mode) : DEFAULTS.mode,
      language: LANGUAGES.some((l) => l.code === v.language) ? (v.language as string) : DEFAULTS.language,
      metric: METRIC_KEYS.includes(v.metric as MetricKey) ? (v.metric as MetricKey) : DEFAULTS.metric,
      player: typeof v.player === "string" && PLAYER.test(v.player) ? v.player : null,
      clubs: Array.isArray(v.clubs) ? v.clubs.filter((c): c is string => typeof c === "string" && CLUB.test(c)) : [],
    };
  } catch {
    return DEFAULTS;
  }
}

function readRaw(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

let cache: { raw: string | null; value: Preferences } = { raw: null, value: DEFAULTS };
let memory: Preferences | null = null; // when storage is blocked, choices last for the visit

function snapshot(): Preferences {
  if (memory) return memory;
  const raw = readRaw();
  if (raw !== cache.raw) cache = { raw, value: parse(raw) };
  return cache.value;
}

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange); // other tabs
  window.addEventListener(CHANGED, onChange); // this tab
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

/** The viewer's preferences and a setter that merges a change and remembers it. */
export function usePreferences(): [Preferences, (change: Partial<Preferences>) => void] {
  const prefs = useSyncExternalStore(subscribe, snapshot, () => DEFAULTS);
  const update = useCallback((change: Partial<Preferences>) => {
    const next = { ...snapshot(), ...change };
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
      memory = null;
    } catch {
      memory = next;
    }
    window.dispatchEvent(new Event(CHANGED));
  }, []);
  return [prefs, update];
}
