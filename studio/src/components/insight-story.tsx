"use client";

import { ArrowLeft, PlayCircle } from "lucide-react";
import Link from "next/link";
import { use, useEffect, useState, type ReactNode } from "react";

import { ApiError, api, LANGUAGES, type InsightStory as Story, type Mode, type StoryNarrative } from "@/lib/api";
import { usePreferences } from "@/lib/preferences";
import { minuteLabel } from "@/lib/utils";

/** Audience modes a reader can pick for the story (single metric is a graphic, not prose). */
const STORY_MODES = ["casual", "analyst", "club_focus", "player_focus"] as const;
type StoryMode = (typeof STORY_MODES)[number];

type Words = Record<"en" | "es" | "fil", string>;
const MODE_LABELS: Record<StoryMode, Words> = {
  casual: { en: "Casual fan", es: "Aficionado", fil: "Casual na fan" },
  analyst: { en: "Analyst", es: "Analista", fil: "Analyst" },
  club_focus: { en: "Club focus", es: "Mi club", fil: "Para sa club" },
  player_focus: { en: "Player focus", es: "Jugador", fil: "Para sa player" },
};
const BEATS: { key: "buildup" | "moment" | "outlook"; label: Words }[] = [
  { key: "buildup", label: { en: "The buildup", es: "La previa", fil: "Ang pinagmulan" } },
  { key: "moment", label: { en: "What just happened", es: "Lo que acaba de pasar", fil: "Ang nangyari" } },
  { key: "outlook", label: { en: "What it could mean", es: "Lo que puede significar", fil: "Ano ang ibig sabihin" } },
];
const WATCH: Words = { en: "Watch the moment", es: "Ver la jugada", fil: "Panoorin ang sandali" };
const READ_AS: Words = { en: "Read as", es: "Leer como", fil: "Basahin bilang" };

function words(text: Words, language: string): string {
  return text[language as keyof Words] ?? text.en;
}

/**
 * "Tell me more": the story of one aired moment for fans, in three short beats (the buildup,
 * what just happened, what it could mean), told for the reader's audience mode and language.
 * Both choices are the same ones the Match Centre remembers on this device.
 */
export function InsightStory({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [story, setStory] = useState<Story | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [prefs, setPrefs] = usePreferences();

  useEffect(() => {
    api.story(id).then(setStory, (e: unknown) =>
      setError(
        e instanceof ApiError && e.status === 404
          ? "This moment isn't on air, so its story isn't public."
          : e instanceof Error
            ? e.message
            : String(e),
      ),
    );
  }, [id]);

  if (error) return <p className="text-sm text-rose-700">{error}</p>;
  if (!story) return <p className="text-sm text-slate-500">Loading the story…</p>;

  const mode: StoryMode = (STORY_MODES as readonly string[]).includes(prefs.mode) ? (prefs.mode as StoryMode) : "casual";
  const told = new Set(story.narratives.map((n) => n.language));
  const language = told.has(prefs.language) ? prefs.language : "en";
  const pick = (m: string, l: string) => story.narratives.find((n) => n.mode === m && n.language === l);
  const narrative: StoryNarrative = pick(mode, language) ??
    pick(mode, "en") ?? {
      mode,
      language: "en",
      title: story.insight.headline,
      buildup: "",
      moment: story.insight.explanation,
      outlook: "",
    };
  const { match } = story;

  return (
    <article className="mx-auto max-w-2xl space-y-6" lang={narrative.language}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-500">
        <Link href={`/matches/${match.match_id}`} className="flex items-center gap-1 hover:text-slate-900">
          <ArrowLeft className="h-4 w-4" aria-hidden /> {match.home.name} v {match.away.name}
        </Link>
        <span className="font-mono">{minuteLabel(story.minute)}</span>
        <Link
          href={`/matches/${match.match_id}?t=${story.minute.toFixed(1)}`}
          className="flex items-center gap-1 text-sky-700 hover:underline"
        >
          <PlayCircle className="h-4 w-4" aria-hidden /> {words(WATCH, narrative.language)}
        </Link>
      </div>

      <h1 className="text-2xl leading-tight font-semibold text-balance sm:text-3xl">{narrative.title}</h1>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-500">{words(READ_AS, narrative.language)}</span>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Audience">
          {STORY_MODES.map((m) => (
            <Chip key={m} active={mode === m} onClick={() => setPrefs({ mode: m as Mode })}>
              {words(MODE_LABELS[m], narrative.language)}
            </Chip>
          ))}
        </div>
        <div className="flex gap-1 sm:ml-auto" role="group" aria-label="Language">
          {LANGUAGES.filter((l) => told.has(l.code)).map((l) => (
            <Chip key={l.code} active={language === l.code} onClick={() => setPrefs({ language: l.code })}>
              {l.label}
            </Chip>
          ))}
        </div>
      </div>

      <ol className="space-y-5">
        {BEATS.filter(({ key }) => narrative[key]).map(({ key, label }) => (
          <li
            key={key}
            className={
              key === "moment"
                ? "rounded-lg border border-sky-200 bg-sky-50 p-4 sm:p-5"
                : "border-l-2 border-slate-200 pl-4"
            }
          >
            <h2 className={`mb-1 text-xs font-semibold tracking-wide uppercase ${key === "moment" ? "text-sky-800" : "text-slate-500"}`}>
              {words(label, narrative.language)}
            </h2>
            <p className={key === "moment" ? "text-lg leading-relaxed font-medium text-slate-900" : "leading-relaxed text-slate-700"}>
              {narrative[key]}
            </p>
          </li>
        ))}
      </ol>
    </article>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full px-3 py-1 ${
        active ? "bg-slate-900 text-white" : "border border-slate-300 text-slate-700 hover:bg-slate-50"
      }`}
    >
      {children}
    </button>
  );
}
