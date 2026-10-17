"use client";

import { PlayCircle, X } from "lucide-react";
import { useRouter } from "next/navigation";
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
 * It opens in the Match Centre, under the replay controls; both choices are the same ones the
 * Match Centre remembers on this device.
 */
export function StoryPanel({
  insightId,
  onWatch,
  onClose,
}: {
  insightId: string;
  onWatch: (minute: number) => void;
  onClose: () => void;
}) {
  const [loaded, setLoaded] = useState<{ id: string; story?: Story; error?: string } | null>(null);
  const [prefs, setPrefs] = usePreferences();

  useEffect(() => {
    let current = true; // a slow reply for a story the reader has left is ignored
    api.story(insightId).then(
      (story) => current && setLoaded({ id: insightId, story }),
      (e: unknown) => current && setLoaded({ id: insightId, error: storyError(e) }),
    );
    return () => {
      current = false;
    };
  }, [insightId]);

  const close = (
    <button
      onClick={onClose}
      className="-m-1 rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-900"
      aria-label="Close the story"
    >
      <X className="h-4 w-4" aria-hidden />
    </button>
  );
  if (loaded?.id !== insightId || (!loaded.story && !loaded.error)) {
    return (
      <div className="flex items-start justify-between gap-3 p-4 sm:p-5">
        <p className="text-sm text-slate-500">Loading the story…</p>
        {close}
      </div>
    );
  }
  if (!loaded.story) {
    return (
      <div className="flex items-start justify-between gap-3 p-4 sm:p-5">
        <p className="text-sm text-rose-700">{loaded.error}</p>
        {close}
      </div>
    );
  }
  const { story } = loaded;

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

  return (
    <article className="space-y-4 p-4 sm:p-5" lang={narrative.language} aria-labelledby="story-title">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
            <span className="font-mono">{minuteLabel(story.minute)}</span>
            <button
              onClick={() => onWatch(story.minute)}
              className="flex items-center gap-1 text-sky-700 hover:underline"
            >
              <PlayCircle className="h-4 w-4" aria-hidden /> {words(WATCH, narrative.language)}
            </button>
          </div>
          <h2 id="story-title" className="text-xl leading-tight font-semibold text-balance sm:text-2xl">
            {narrative.title}
          </h2>
        </div>
        {close}
      </div>

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

      <ol className="space-y-4">
        {BEATS.filter(({ key }) => narrative[key]).map(({ key, label }) => (
          <li
            key={key}
            className={
              key === "moment"
                ? "rounded-lg border border-sky-200 bg-sky-50 p-4"
                : "border-l-2 border-slate-200 pl-4"
            }
          >
            <h3 className={`mb-1 text-xs font-semibold tracking-wide uppercase ${key === "moment" ? "text-sky-800" : "text-slate-500"}`}>
              {words(label, narrative.language)}
            </h3>
            <p className={key === "moment" ? "text-lg leading-relaxed font-medium text-slate-900" : "leading-relaxed text-slate-700"}>
              {narrative[key]}
            </p>
          </li>
        ))}
      </ol>
    </article>
  );
}

/**
 * The old "Tell me more" page: stories now open inside the Match Centre, so a link to one goes
 * to its match at that moment with the story open.
 */
export function StoryRedirect({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.story(id).then(
      (story) =>
        router.replace(`/matches/${story.match.match_id}?t=${story.minute.toFixed(1)}&story=${encodeURIComponent(id)}`),
      (e: unknown) => setError(storyError(e)),
    );
  }, [id, router]);

  if (error) return <p className="text-sm text-rose-700">{error}</p>;
  return <p className="text-sm text-slate-500">Opening the story…</p>;
}

function storyError(e: unknown): string {
  return e instanceof ApiError && e.status === 404
    ? "This moment isn't on air, so its story isn't public."
    : e instanceof Error
      ? e.message
      : String(e);
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
