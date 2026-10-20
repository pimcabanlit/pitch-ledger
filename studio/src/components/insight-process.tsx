"use client";

import {
  ArrowDown,
  CheckCircle2,
  ChevronRight,
  FlaskConical,
  PenLine,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { EvidenceTable } from "@/components/evidence-table";
import { Badge } from "@/components/ui/badge";
import {
  LANGUAGES,
  type AuditStep,
  type InsightDetail,
  type StageKey,
  type StoryBeats,
  type StoryStage,
  type StoryStep,
} from "@/lib/api";
import { metricLabel, metricUnit } from "@/lib/metrics";

const MODE_LABELS: Record<string, string> = {
  casual: "Casual fan",
  analyst: "Analyst",
  club_focus: "Club focus",
  player_focus: "Player focus",
  single_metric: "Single metric",
};
const LANGUAGE_NAMES: Record<string, string> = Object.fromEntries(LANGUAGES.map((l) => [l.code, l.label]));
const PRODUCER_ACTIONS = new Set(["producer_approve", "producer_block"]);
const COPY_CHECK = "copy_check";
const STORY_CHECK = "story_check";
const OUTPUT_CHECKS = new Set([COPY_CHECK, STORY_CHECK]); // fallbacks of published text, not drafts
const DEPTH_LABELS = { plain: "Fan story (every mode but the analyst)", numbers: "Analyst story" } as const;
const DRAFT_ACTIONS = new Set(["draft", "fallback"]);

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
  return (
    <>
      {text.split(pattern).map((part, i) =>
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

/**
 * How one insight was made, as a slim stepper: Detected, Analyzed, Explained, Audited,
 * Approved, Published. Picking a stage opens the records behind it underneath: the
 * trigger, the numbers and evidence, the drafts, the agent handoffs, the producer's
 * decision and every on-screen version. No stage starts open, so the flow reads as a
 * quick reference.
 */
export function InsightProcess({ detail, names }: { detail: InsightDetail; names: Record<string, string> }) {
  const [open, setOpen] = useState<StageKey | null>(null);
  const [technical, setTechnical] = useState(false);
  const process = detail.process;
  if (!process) return null;
  const stages = process.stages;
  const index = stages.findIndex((stage) => stage.key === open);
  const current = index >= 0 ? stages[index] : null;

  const body: Record<StageKey, ReactNode> = {
    detected: <Detected detail={detail} />,
    analyzed: <Analyzed detail={detail} names={names} />,
    explained: <Explained detail={detail} />,
    audited: <Audited detail={detail} technical={technical} onTechnical={() => setTechnical((v) => !v)} />,
    approved: <Approved detail={detail} />,
    published: <Published detail={detail} />,
  };

  return (
    <section aria-label="How this insight was made" className="space-y-3">
      <h3 className="text-sm font-medium text-slate-500">How it was made</h3>
      <ol className="grid grid-cols-3 gap-x-1 gap-y-3 sm:grid-cols-6">
        {stages.map((stage) => {
          const isOpen = stage.key === open;
          return (
            <li key={stage.key} className="flex flex-col gap-1.5">
              <span aria-hidden className={`h-1 rounded-full ${stage.done ? "bg-emerald-500" : "bg-slate-200"}`} />
              <button
                onClick={() => setOpen(isOpen ? null : stage.key)}
                aria-expanded={isOpen}
                aria-controls="stage-records"
                title={stage.detail}
                className={`rounded py-0.5 text-left text-sm hover:text-slate-900 ${
                  isOpen ? "font-semibold text-slate-900" : stage.done ? "text-slate-600" : "text-slate-400"
                }`}
              >
                {stage.label}
              </button>
            </li>
          );
        })}
      </ol>
      {current ? (
        <div id="stage-records" className="space-y-3 rounded-md bg-slate-50 p-3 sm:p-4">
          <div className="space-y-0.5">
            <p className="flex flex-wrap items-baseline gap-x-2 text-sm font-medium">
              {current.label}
              {delta(stages, index) && (
                <span className="font-mono text-[11px] font-normal text-slate-400">{delta(stages, index)}</span>
              )}
              {current.at && (
                <span className="ml-auto font-mono text-[11px] font-normal text-slate-400">{clock(current.at)}</span>
              )}
            </p>
            <p className="text-sm text-slate-600">{current.detail}</p>
          </div>
          {body[current.key]}
        </div>
      ) : (
        <p className="text-xs text-slate-500">Pick a stage to see the records behind it.</p>
      )}
    </section>
  );
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
      {rows.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-slate-500">{term}</dt>
          <dd className="text-slate-800">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Detected({ detail }: { detail: InsightDetail }) {
  const trigger = detail.process?.trigger;
  if (!trigger) return <p className="text-xs text-slate-600">No detector record: this insight came from a template rule.</p>;
  const rows: [string, ReactNode][] = [
    ["Rule", label(trigger.rule_id)],
    ["Match minute", `${trigger.minute.toFixed(1)}'`],
  ];
  if (trigger.detector) rows.push(["Detector", label(trigger.detector)]);
  if (trigger.windows.length) {
    rows.push([
      "Compared",
      trigger.windows.map((w) => `${w.label} ${w.start_minute.toFixed(0)}'–${w.end_minute.toFixed(0)}'`).join(" vs "),
    ]);
  }
  if (trigger.season_matches.length) rows.push(["Season facts from", trigger.season_matches.join(", ")]);
  rows.push(["Trigger", <span key="id" className="font-mono">{trigger.trigger_id}</span>]);
  return <Facts rows={rows} />;
}

function Analyzed({ detail, names }: { detail: InsightDetail; names: Record<string, string> }) {
  const process = detail.process;
  const metrics = process?.trigger?.metrics ?? detail.insight.metrics;
  const cited = new Set(Object.keys(detail.insight.metrics));
  return (
    <>
      <div>
        <h4 className="mb-1.5 text-xs font-semibold text-slate-600">
          Metrics computed in code ({Object.keys(metrics).length}; {cited.size} cited in the insight)
        </h4>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(metrics).map(([k, v]) => (
            <Badge key={k} tone={cited.has(k) ? "aired" : "neutral"} title={cited.has(k) ? "Cited in the insight" : undefined}>
              {metricLabel(k)}: <span className="ml-1 font-mono">{v}{metricUnit(k)}</span>
            </Badge>
          ))}
        </div>
      </div>
      <div>
        <h4 className="mb-1.5 text-xs font-semibold text-slate-600">
          Evidence: {detail.evidence.length} match events
        </h4>
        {process && Object.keys(process.evidence_counts).length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {Object.entries(process.evidence_counts).map(([type, n]) => (
              <Badge key={type}>
                {label(type)} × {n}
              </Badge>
            ))}
          </div>
        )}
        <EvidenceTable evidence={detail.evidence} names={names} maxHeight="max-h-64" />
      </div>
    </>
  );
}

function Explained({ detail }: { detail: InsightDetail }) {
  const i = detail.insight;
  const drafts = (detail.process?.steps ?? []).filter(
    (s) => DRAFT_ACTIONS.has(s.action) && !OUTPUT_CHECKS.has(s.agent) && (s.headline || s.explanation),
  );
  return (
    <>
      <Facts rows={[["Written by", `${label(i.created_by_agent)} (${i.model_version})`]]} />
      {drafts.length > 0 && (
        <ol className="space-y-1.5">
          {drafts.map((s) => (
            <li key={s.audit_id} className="rounded-md border border-slate-200 bg-white p-2.5 text-sm">
              <p className="mb-1 text-[11px] font-semibold tracking-wide text-slate-500 uppercase">
                Round {s.round} · {s.action === "fallback" ? "template copy" : "draft"}
              </p>
              <p className="leading-relaxed text-slate-700">
                <Highlighted text={[s.headline, s.explanation].filter(Boolean).join(" — ")} numbers={s.unsupported_numbers} />
              </p>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

function StepCard({ step, summary, technical }: { step: StoryStep; summary?: string; technical: boolean }) {
  const tone =
    step.role === "test"
      ? "border-amber-300 bg-amber-50"
      : step.passed === false
        ? "border-rose-200 bg-rose-50"
        : step.passed
          ? "border-emerald-200 bg-emerald-50/60"
          : "border-slate-200 bg-white";
  const icon =
    step.role === "test" ? (
      <FlaskConical className="h-4 w-4 shrink-0 text-amber-700" aria-hidden />
    ) : step.passed === false ? (
      <XCircle className="h-4 w-4 shrink-0 text-rose-600" aria-hidden />
    ) : step.passed ? (
      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" aria-hidden />
    ) : (
      <PenLine className="h-4 w-4 shrink-0 text-sky-600" aria-hidden />
    );
  const text = [step.headline, step.explanation].filter(Boolean).join(" — ");
  const showText = text && step.action === "inject_error"; // drafts show under Explained
  return (
    <li className={`rounded-md border p-2.5 ${tone}`}>
      <div className="flex items-start gap-2 text-sm">
        {icon}
        <span className="min-w-0 flex-1 text-slate-800">{summary ?? label(step.action)}</span>
        {step.recorded_at && <span className="font-mono text-[11px] text-slate-400">{clock(step.recorded_at)}</span>}
      </div>
      {showText && (
        <p className="mt-1.5 text-sm leading-relaxed text-slate-700">
          <Highlighted text={text} numbers={step.unsupported_numbers} />
        </p>
      )}
      {technical && (
        <div className="mt-1.5 space-y-0.5 font-mono text-[11px] break-words text-slate-500">
          <p>
            r{step.round} {step.agent} · {step.action}
          </p>
          {step.findings.map((f) => (
            <p key={f} className="flex gap-1">
              <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden /> {f}
            </p>
          ))}
        </div>
      )}
    </li>
  );
}

function Audited({
  detail,
  technical,
  onTechnical,
}: {
  detail: InsightDetail;
  technical: boolean;
  onTechnical: () => void;
}) {
  const summaries = useMemo(
    () => Object.fromEntries(detail.audits.map((a: AuditStep) => [a.audit_id, a.summary])),
    [detail.audits],
  );
  const rounds = useMemo(() => {
    const grouped = new Map<number, StoryStep[]>();
    for (const step of detail.process?.steps ?? []) {
      if (step.round === 0) continue; // the producer and the on-screen copy check have their own stages
      grouped.set(step.round, [...(grouped.get(step.round) ?? []), step]);
    }
    return [...grouped.entries()].sort(([a], [b]) => a - b);
  }, [detail.process]);
  if (!rounds.length) return <p className="text-xs text-slate-600">No agent rounds: template copy needs no Auditor check.</p>;
  return (
    <>
      <div className="flex items-center gap-2">
        <h4 className="text-xs font-semibold text-slate-600">Agent handoffs: how the claim was checked</h4>
        <button
          onClick={onTechnical}
          aria-expanded={technical}
          className="ml-auto flex items-center gap-0.5 text-xs text-slate-500 hover:text-slate-700"
        >
          <ChevronRight className={`h-3 w-3 transition-transform ${technical ? "rotate-90" : ""}`} aria-hidden />
          Technical details
        </button>
      </div>
      {rounds.map(([round, steps], i) => (
        <div key={round} className="space-y-1.5">
          <h5 className="text-[11px] font-semibold tracking-wide text-slate-500 uppercase">Round {round}</h5>
          <ol className="space-y-1.5">
            {steps.map((s) => (
              <StepCard key={s.audit_id} step={s} summary={summaries[s.audit_id]} technical={technical} />
            ))}
          </ol>
          {steps.some((s) => s.action === "revise") && i < rounds.length - 1 && (
            <p className="flex items-start gap-1.5 pl-1 text-xs font-medium text-rose-700">
              <ArrowDown className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              Handed back to the {label(detail.insight.created_by_agent)} with the finding, for a revised draft
            </p>
          )}
        </div>
      ))}
    </>
  );
}

function Approved({ detail }: { detail: InsightDetail }) {
  const decisions = detail.audits.filter((a) => PRODUCER_ACTIONS.has(a.action));
  const by = detail.insight.approved_by;
  if (detail.status === "pending") {
    return <p className="text-xs text-slate-700">Waiting for you: use Approve and air or Block above.</p>;
  }
  return (
    <ul className="space-y-1 text-xs text-slate-700">
      {by?.startsWith("auto-approval rule") && <li>Approved automatically: {by}.</li>}
      {decisions.map((a) => (
        <li key={a.audit_id}>
          {a.summary}
          {a.recorded_at && <span className="ml-1 font-mono text-slate-400">{clock(a.recorded_at)}</span>}
          {a.action === "producer_block" && a.findings.length > 0 && (
            <span className="block text-slate-500">Reason: {a.findings.join("; ")}</span>
          )}
        </li>
      ))}
      {!decisions.length && !by && <li>No decision recorded.</li>}
    </ul>
  );
}

function Published({ detail }: { detail: InsightDetail }) {
  const fallbacks = detail.audits.filter((a) => OUTPUT_CHECKS.has(a.actor));
  const byMode = useMemo(() => {
    const grouped = new Map<string, typeof detail.overlays>();
    for (const o of detail.overlays) grouped.set(o.audience_mode, [...(grouped.get(o.audience_mode) ?? []), o]);
    return [...grouped.entries()];
  }, [detail]);
  if (!detail.overlays.length) {
    return <p className="text-xs text-slate-600">Nothing on air yet: every audience and language version is written when it is approved.</p>;
  }
  return (
    <>
      {fallbacks.map((a) => (
        <p key={a.audit_id} className="text-xs text-amber-800">
          {a.summary}
        </p>
      ))}
      <div className="space-y-2">
        {byMode.map(([mode, overlays]) => (
          <div key={mode}>
            <h4 className="mb-1 text-xs font-semibold text-slate-600">{MODE_LABELS[mode] ?? label(mode)}</h4>
            <ul className="space-y-1">
              {overlays.map((o) => (
                <li key={o.overlay_id} className="flex gap-2 text-sm">
                  <span className="w-16 shrink-0 text-xs leading-5 text-slate-500">
                    {LANGUAGE_NAMES[o.language] ?? o.language}
                  </span>
                  <span className="min-w-0 text-slate-800">{o.text}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {detail.process?.story && <StoryBeatsList beats={detail.process.story.beats} />}
    </>
  );
}

/** The stored "Tell me more" beats, by depth then language; template beats are marked. */
function StoryBeatsList({ beats }: { beats: StoryBeats[] }) {
  return (
    <div className="space-y-2 border-t border-slate-200 pt-2">
      {(["plain", "numbers"] as const).map((depth) => (
        <div key={depth}>
          <h4 className="mb-1 text-xs font-semibold text-slate-600">{DEPTH_LABELS[depth]}</h4>
          <ul className="space-y-1.5">
            {beats
              .filter((b) => b.depth === depth)
              .map((b) => (
                <li key={b.language} className="flex gap-2 text-sm">
                  <span className="w-16 shrink-0 text-xs leading-5 text-slate-500">
                    {LANGUAGE_NAMES[b.language] ?? b.language}
                  </span>
                  <div className="min-w-0 space-y-0.5 text-slate-800">
                    {(["buildup", "outlook"] as const).map((beat) =>
                      b[beat] ? (
                        <p key={beat}>
                          <span className="text-xs text-slate-500">{beat === "buildup" ? "Buildup" : "Outlook"}: </span>
                          {b[beat]}
                          {b.sources[beat] === "template" && (
                            <Badge tone="warning" className="ml-1 align-middle text-[10px]">
                              template
                            </Badge>
                          )}
                        </p>
                      ) : null,
                    )}
                  </div>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
