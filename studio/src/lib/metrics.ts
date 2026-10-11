/**
 * The metrics a viewer can choose to follow. Every value comes from the API (computed by the
 * engine in Python); the Studio only picks which one to draw and which insights carry it.
 */
export const METRIC_KEYS = ["control", "possession", "pass_accuracy", "tempo", "ppda", "chaos", "xg", "speed"] as const;
export type MetricKey = (typeof METRIC_KEYS)[number];

export interface MetricDef {
  label: string;
  unit: string;
  term: GlossaryKey;
  source: "team" | "match" | "shots" | "insights"; // team state per minute, match chaos, shots, insight figures
  max: number | null; // fixed scale top, null: from the data
  carries: (key: string) => boolean; // insight metric keys that report it
}

export const METRICS: Record<MetricKey, MetricDef> = {
  control: { label: "Control", unit: "", term: "control", source: "team", max: 100, carries: (k) => k.startsWith("control") },
  possession: { label: "Possession", unit: "%", term: "possession", source: "team", max: 100, carries: (k) => k.startsWith("possession") },
  pass_accuracy: { label: "Pass accuracy", unit: "%", term: "pass_accuracy", source: "team", max: 100, carries: (k) => k.includes("pass_accuracy") },
  tempo: { label: "Tempo", unit: "/min", term: "tempo", source: "team", max: null, carries: (k) => k.startsWith("tempo") },
  ppda: { label: "PPDA", unit: "", term: "ppda", source: "team", max: null, carries: (k) => k.startsWith("ppda") },
  chaos: { label: "Chaos", unit: "", term: "chaos", source: "match", max: 100, carries: (k) => k.startsWith("chaos") },
  xg: { label: "xG", unit: "", term: "xg", source: "shots", max: null, carries: (k) => /(^|_)(np)?xg(_|$)/.test(k) },
  speed: { label: "Speed", unit: " km/h", term: "speed", source: "insights", max: null, carries: (k) => k.endsWith("speed_kmh") },
};

/** Team-state field behind each team metric. */
export const TEAM_FIELD = {
  control: "control",
  possession: "possession_pct",
  pass_accuracy: "pass_accuracy",
  tempo: "tempo",
  ppda: "ppda",
} as const;

export const GLOSSARY = {
  control: "Control (0–100 per team): possession share, passes per possession, pass accuracy and few turnovers in the team's own half, over the last five minutes. The two teams' scores do not add up to 100.",
  chaos: "Chaos (0–100, the whole match): how often the ball changes hands, how much play is under pressure and how many duels there are, over the last five minutes. High chaos means an open, scrappy spell.",
  tempo: "Tempo: passes per minute while the team has the ball, over the last five minutes. A higher tempo means quicker passing.",
  ppda: "PPDA (passes per defensive action): opponent passes allowed in their own 60% of the pitch for each tackle, interception or foul there. Lower means more pressing.",
  possession: "Possession: the share of the time each team had the ball, over the last five minutes.",
  pass_accuracy: "Pass accuracy: the share of a team's passes that reached a teammate, over the last five minutes (throw-ins left out).",
  xg: "xG (expected goals): how likely each shot was to score, from where it was taken, whether it was a header, a penalty or set up by a through ball; summed for a team. 0.3 means about one goal in three from such chances.",
  speed: "Speed: top running speeds from player tracking and ball speeds of shots and passes, from the badges and insights that aired.",
  modes: "Modes: Casual fan (short and plain), Analyst (more numbers), Club focus (for a club's supporters), Player focus (follows the player in each moment), Single metric (one number per moment).",
  statuses: "Every insight is checked by the Auditor, which recomputes its numbers from the match events. Only aired insights reach the fan view: a producer approved them, or a fixed rule did for quick stat badges.",
  follow: "Follow a player: their running totals up to the replay clock, a ring on their dot, and the moments that featured them. Totals come from the match events and tracking.",
} as const;
export type GlossaryKey = keyof typeof GLOSSARY;

/** "shot_speed_kmh" -> "shot speed", "xg_home" -> "xG home". */
export function metricLabel(key: string): string {
  return key
    .replace(/_kmh$/, "")
    .replace(/_km$/, "")
    .replace(/(^|_)xg(?=_|$)/g, "$1xG")
    .replaceAll("_", " ");
}

export function metricUnit(key: string): string {
  if (key.endsWith("_kmh")) return " km/h";
  if (key.endsWith("_km")) return " km";
  return "";
}
