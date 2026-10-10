"use client";

import {
  ArrowDown,
  ArrowLeft,
  Calculator,
  CheckCircle2,
  Download,
  FlaskConical,
  PenLine,
  Radar,
  Radio,
  ShieldAlert,
  ShieldCheck,
  UserCheck,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { use, useEffect, useMemo, useState, type ReactNode } from "react";

import { EvidenceTable } from "@/components/evidence-table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  ApiError,
  api,
  LANGUAGES,
  type InsightStory as Story,
  type Overlay,
  type StageKey,
  type StoryStage,
  type StoryStep,
} from "@/lib/api";
import { minuteLabel } from "@/lib/utils";

const STAGE_ICONS: Record<StageKey, typeof Radar> = {
  detected: Radar,
  analyzed: Calculator,
  explained: PenLine,
  audited: ShieldCheck,
  approved: UserCheck,
  published: Radio,
};

const COMPARE_MODES = [
  { mode: "casual", label: "Casual fan" },
  { mode: "analyst", label: "Analyst" },
  { mode: "player_focus", label: "Player focus" },
] as const;

const ACTION_LABELS: Record<string, string> = {
  draft: "wrote a draft",
  fallback: "used template copy (model unavailable)",
  inject_error: "planted a wrong number (demo test)",
  check: "recomputed every number",
  review: "reviewed claims and tone",
  revise: "sent it back with findings",
  approve: "approved it",
  correct: "approved the corrected draft",
  block: "blocked it",
  producer_approve: "approved it for air",
  producer_block: "kept it off air",
};

function label(text: string): string {
  return text.replaceAll("_", " ");
}

function clock(iso: string | null): string | null {
  return iso ? new Date(iso).toLocaleTimeString([], { hour12: false }) : null;
}

/** Seconds from the previous timestamped stage, e.g. "+1.4 s". */
function delta(stages: StoryStage[], index: number): string | null {
  const at = stages[index].at;
  if (!at) return null;
  for (let j = index - 1; j >= 0; j--) {
    const before = stages[j].at;
    if (before) {
      const s = (new Date(at).getTime() - new Date(before).getTime()) / 1000;
      if (s < 0.1) return "<0.1 s";
      return `+${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
    }
  }
  return null;
}

/** The text with each number no computed metric supports marked, as the Auditor saw it. */
function Highlighted({ text, numbers }: { text: string; numbers: string[] }) {
  if (!numbers.length) return <>{text}</>;
  const escaped = numbers.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(?<![\\d.,])(${escaped.join("|")})(?![\\d])`, "g");
  const parts = text.split(pattern);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded bg-rose-100 px-0.5 font-semibold text-rose-800 ring-1 ring-rose-300">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

export function InsightStory({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [story, setStory] = useState<Story | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.story(id).then(setStory, (e: unknown) =>
      setError(
        e instanceof ApiError && e.status === 404
          ? "This insight isn't on air, so its story isn't public."
          : e instanceof Error
            ? e.message
            : String(e),
      ),
    );
  }, [id]);

  if (error) return <p className="text-sm text-rose-700">{error}</p>;
  if (!story) return <p className="text-sm text-slate-500">Loading the story…</p>;

  return (
    <div className="space-y-5">
      <StoryHeader story={story} />
      <PipelineStepper stages={story.stages} />
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <AgentTimeline story={story} />
        <NumbersPanel story={story} />
      </div>
      <ModeCompare overlays={story.overlays} />
      <BroadcastJson insightId={story.insight.insight_id} />
    </div>
  );
}

function StoryHeader({ story }: { story: Story }) {
  const { insight, match } = story;
  const [busy, setBusy] = useState(false);
  const download = async () => {
    setBusy(true);
    try {
      const json = await api.broadcast(insight.insight_id);
      const url = URL.createObjectURL(new Blob([json], { type: "application/json" }));
      const a = Object.assign(document.createElement("a"), {
        href: url,
        download: `${insight.insight_id}.broadcast.json`,
      });
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <CardHeader className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <Link href={`/matches/${match.match_id}`} className="flex items-center gap-1 hover:text-slate-900">
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> {match.home.name} v {match.away.name}
          </Link>
          <span>·</span>
          <span className="font-mono">{minuteLabel(story.minute)}</span>
          <span>{label(insight.insight_type)}</span>
          <span className="font-mono">{insight.insight_id}</span>
          <Badge tone={story.status} className="ml-auto">
            {story.status}
          </Badge>
          <Badge tone={insight.audit_status === "corrected" ? "corrected" : "neutral"}>
            audit: {insight.audit_status}
          </Badge>
        </div>
        <h1 className="text-xl font-semibold sm:text-2xl">{insight.headline}</h1>
        <p className="text-sm leading-relaxed text-slate-700">{insight.explanation}</p>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
        <span>
          Every number above was computed in code and recomputed by the Auditor from the cited events
          before it aired.
        </span>
        <button
          onClick={download}
          disabled={busy}
          className="ml-auto inline-flex items-center gap-1 rounded-md border border-slate-300 px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50"
        >
          <Download className="h-3.5 w-3.5" aria-hidden /> Broadcast JSON
        </button>
      </CardContent>
    </Card>
  );
}

function PipelineStepper({ stages }: { stages: StoryStage[] }) {
  return (
    <Card aria-label="Pipeline">
      <ol className="grid gap-0 p-4 md:grid-cols-6 md:gap-3">
        {stages.map((stage, i) => {
          const Icon = STAGE_ICONS[stage.key];
          const step = delta(stages, i);
          return (
            <li key={stage.key} className="relative flex gap-3 pb-4 md:flex-col md:gap-2 md:pb-0">
              {i < stages.length - 1 && (
                <span
                  aria-hidden
                  className="absolute top-9 left-[17px] h-[calc(100%-2.25rem)] w-px bg-slate-200 md:top-[17px] md:left-10 md:h-px md:w-[calc(100%-1.75rem)]"
                />
              )}
              <span
                className={`relative z-10 flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${
                  stage.done ? "bg-emerald-600 text-white" : "bg-slate-200 text-slate-500"
                }`}
              >
                <Icon className="h-4 w-4" aria-hidden />
              </span>
              <div className="min-w-0 space-y-0.5">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-semibold">{stage.label}</span>
                  {step && <span className="font-mono text-[11px] text-slate-400">{step}</span>}
                </div>
                <p className="text-xs leading-snug text-slate-600">{stage.detail}</p>
                {stage.at && <p className="font-mono text-[11px] text-slate-400">{clock(stage.at)}</p>}
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

function StepCard({ step, repeated = false }: { step: StoryStep; repeated?: boolean }) {
  const tone =
    step.role === "test"
      ? "border-amber-300 bg-amber-50"
      : step.role === "producer"
        ? "border-violet-200 bg-violet-50"
        : step.passed === false
          ? "border-rose-200 bg-rose-50"
          : step.passed
            ? "border-emerald-200 bg-emerald-50/50"
            : "border-slate-200 bg-white";
  const icon =
    step.role === "test" ? (
      <FlaskConical className="h-4 w-4 text-amber-700" aria-hidden />
    ) : step.passed === false ? (
      <XCircle className="h-4 w-4 text-rose-600" aria-hidden />
    ) : step.passed ? (
      <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden />
    ) : (
      <PenLine className="h-4 w-4 text-sky-600" aria-hidden />
    );
  const text = [step.headline, step.explanation].filter(Boolean).join(" — ");
  const showText = text && ["draft", "fallback", "inject_error"].includes(step.action);
  return (
    <li className={`rounded-md border p-3 ${tone}`}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {icon}
        <span className="font-semibold capitalize">{step.role === "test" ? "Demo test" : label(step.agent)}</span>
        <span className="text-slate-600">
          {repeated ? "sent it back with the finding above" : (ACTION_LABELS[step.action] ?? label(step.action))}
        </span>
        {step.recorded_at && (
          <span className="ml-auto font-mono text-[11px] text-slate-400">{clock(step.recorded_at)}</span>
        )}
      </div>
      {showText && (
        <p className="mt-2 text-sm leading-relaxed text-slate-800">
          <Highlighted text={text} numbers={step.unsupported_numbers} />
        </p>
      )}
      {step.findings.length > 0 && !repeated && (
        <ul className="mt-2 space-y-1 text-xs text-slate-700">
          {step.findings.map((f) => (
            <li key={f} className="flex gap-1.5">
              <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />
              <span className="font-mono">{f}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function AgentTimeline({ story }: { story: Story }) {
  const rounds = useMemo(() => {
    const grouped = new Map<number, StoryStep[]>();
    for (const step of story.steps) {
      const key = step.round === 0 ? Number.MAX_SAFE_INTEGER : step.round; // producer last
      grouped.set(key, [...(grouped.get(key) ?? []), step]);
    }
    return [...grouped.entries()].sort(([a], [b]) => a - b);
  }, [story.steps]);
  const modes = new Set(story.overlays.map((o) => o.audience_mode)).size;
  const languages = new Set(story.overlays.map((o) => o.language)).size;
  const handoffs: ReactNode[] = rounds.map(([round, steps], i) => {
    const sentBack = steps.find((s) => s.action === "revise");
    return (
      <div key={round} className="space-y-2">
        <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
          {round === Number.MAX_SAFE_INTEGER ? "Producer" : `Round ${round}`}
        </h3>
        <ol className="space-y-2">
          {steps.map((s, j) => (
            <StepCard
              key={s.audit_id}
              step={s}
              repeated={
                s.action === "revise" && j > 0 && steps[j - 1].findings.join() === s.findings.join()
              }
            />
          ))}
        </ol>
        {sentBack && i < rounds.length - 1 && (
          <p className="flex items-start gap-1.5 pl-1 text-xs font-medium text-rose-700">
            <ArrowDown className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span>
              Handoff: Auditor → <span className="capitalize">{story.insight.created_by_agent}</span>, with the
              finding, for a revised draft
            </span>
          </p>
        )}
      </div>
    );
  });
  return (
    <Card aria-label="Agent handoffs">
      <CardHeader className="text-sm font-medium">Agent handoffs: how the claim was checked</CardHeader>
      <CardContent className="space-y-4">
        {handoffs}
        {story.overlays.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Personalize and translate</h3>
            <p className="rounded-md border border-sky-200 bg-sky-50 p-3 text-sm text-slate-700">
              <span className="font-semibold">Personalizer and Translator</span> wrote {story.overlays.length} versions (
              {modes} audience modes × {languages} languages) from the same computed metrics; each version was checked
              for unsupported numbers before air.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function NumbersPanel({ story }: { story: Story }) {
  const windows = story.trigger?.windows ?? [];
  return (
    <Card aria-label="The numbers">
      <CardHeader className="text-sm font-medium">The numbers (computed in code)</CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {Object.entries(story.insight.metrics).map(([k, v]) => (
            <Badge key={k}>
              {label(k)}: <span className="ml-1 font-mono">{v}</span>
            </Badge>
          ))}
        </div>
        {windows.length > 0 && (
          <p className="text-xs text-slate-600">
            Compared windows:{" "}
            {windows.map((w) => `${w.label} ${w.start_minute.toFixed(0)}'–${w.end_minute.toFixed(0)}'`).join(" vs ")}
            {story.trigger?.detector && <> · detector: {label(story.trigger.detector)}</>}
          </p>
        )}
        <div>
          <h3 className="mb-2 text-xs font-semibold text-slate-600">
            Evidence: {story.evidence.length} feed events
          </h3>
          <div className="mb-2 flex flex-wrap gap-1.5">
            {Object.entries(story.evidence_counts).map(([type, n]) => (
              <Badge key={type} tone="neutral">
                {label(type)} × {n}
              </Badge>
            ))}
          </div>
          <EvidenceTable evidence={story.evidence} maxHeight="max-h-72" />
        </div>
      </CardContent>
    </Card>
  );
}

function ModeCompare({ overlays }: { overlays: Overlay[] }) {
  const [language, setLanguage] = useState("en");
  return (
    <Card aria-label="Same moment, three audiences">
      <CardHeader className="flex flex-row flex-wrap items-center gap-2">
        <span className="text-sm font-medium">Same moment, three audiences</span>
        <div className="ml-auto flex gap-1" role="group" aria-label="Language">
          {LANGUAGES.map((l) => (
            <button
              key={l.code}
              onClick={() => setLanguage(l.code)}
              aria-pressed={language === l.code}
              className={`rounded-md px-2.5 py-1 text-xs ${
                language === l.code ? "bg-slate-900 text-white" : "border border-slate-300 text-slate-700"
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 md:grid-cols-3">
        {COMPARE_MODES.map(({ mode, label: name }) => {
          const overlay = overlays.find((o) => o.audience_mode === mode && o.language === language);
          return (
            <div key={mode} className="rounded-md border border-slate-200 p-3">
              <div className="mb-2 flex items-center gap-2">
                <span className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{name}</span>
                {overlay && <Badge className="ml-auto">{label(overlay.template)}</Badge>}
              </div>
              <p className="text-sm leading-relaxed">{overlay?.text ?? "No version in this language."}</p>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

function BroadcastJson({ insightId }: { insightId: string }) {
  const [json, setJson] = useState<string | null>(null);
  return (
    <details
      className="rounded-lg border border-slate-200 bg-white shadow-sm"
      onToggle={(e) => {
        if ((e.target as HTMLDetailsElement).open && json === null) {
          api.broadcast(insightId).then(setJson, () => setJson("Could not load the broadcast package."));
        }
      }}
    >
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
        Broadcast package (JSON): what a graphics system would receive
      </summary>
      <pre className="max-h-96 overflow-auto border-t border-slate-100 bg-slate-950 p-4 text-xs text-slate-100">
        {json ?? "Loading…"}
      </pre>
    </details>
  );
}
