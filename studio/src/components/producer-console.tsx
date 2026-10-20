"use client";

import { CheckCircle2, CircleAlert, XCircle } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { InsightProcess } from "@/components/insight-process";
import { OpsPanel, OpsSummary, openAlerts } from "@/components/ops-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import {
  ApiError,
  api,
  type AlertView,
  type Insight,
  type InsightDetail,
  type InsightStatus,
  type InsightView,
  type MatchSummary,
} from "@/lib/api";
import { useRealtime } from "@/lib/realtime";
import { matchMinute, minuteLabel } from "@/lib/utils";

const FILTERS: (InsightStatus | "all")[] = ["pending", "aired", "blocked", "all"];
/** Insight statuses in a producer's words. */
const STATUS_LABELS: Record<InsightStatus | "all", string> = {
  pending: "Needs review",
  aired: "On air",
  blocked: "Blocked",
  all: "All",
};
const STATUS_COLORS: Record<InsightStatus, string> = {
  pending: "text-amber-700",
  aired: "text-emerald-700",
  blocked: "text-rose-700",
};
const AUDIT_LABELS: Record<string, string> = {
  pending: "Auditor still checking",
  approved: "Auditor passed it",
  corrected: "Auditor passed it after a fix",
  blocked: "Auditor blocked it",
};
const POLL_MS = 10000; // without push

function signInMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) {
    return "Sign in as a producer to see the review queue.";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "This account does not have the Producer role.";
  }
  if (error instanceof ApiError && error.status === 503) {
    return "Producer sign-in is not configured on the API yet. For a local demo, run the API with PITCH_LEDGER_API_AUTH=dev.";
  }
  return error instanceof Error ? error.message : String(error);
}

/** The match minute an insight is about. */
function minuteOf(i: Insight): number {
  return matchMinute(i.period, i.clock_ms);
}

export function ProducerConsole() {
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [matchId, setMatchId] = useState("");
  const [filter, setFilter] = useState<InsightStatus | "all">("pending");
  const [insights, setInsights] = useState<InsightView[]>([]);
  const [alerts, setAlerts] = useState<AlertView[] | null>(null); // null: not loaded yet
  const [names, setNames] = useState<Record<string, string>>({}); // team and player IDs -> names
  const [selected, setSelected] = useState<InsightDetail | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [opsOpen, setOpsOpen] = useState<boolean | null>(null); // null: open only when something needs you

  useEffect(() => {
    api.matches().then((rows) => {
      setMatches(rows);
      if (rows.length) setMatchId((current) => current || rows[0].match_id);
    }, (e) => setError(signInMessage(e)));
  }, []);

  const loadInsights = useCallback(() => {
    if (!matchId) return;
    api.insights(matchId).then(
      (rows) => {
        setInsights(rows);
        setError(null);
      },
      (e) => setError(signInMessage(e)),
    );
  }, [matchId]);

  useEffect(loadInsights, [loadInsights]);

  useEffect(() => {
    if (!matchId) return;
    api.match(matchId).then(
      (m) =>
        setNames(
          Object.fromEntries([
            [m.home.team_id, m.home.name],
            [m.away.team_id, m.away.name],
            ...m.players.map((p) => [p.player_id, p.display_name]),
          ]),
        ),
      () => setNames({}), // IDs still show
    );
  }, [matchId]);

  const loadAlerts = useCallback(() => {
    if (!matchId) return;
    api.alerts(matchId).then(setAlerts, (e) => setError(signInMessage(e)));
  }, [matchId]);

  useEffect(loadAlerts, [loadAlerts]);

  const acknowledge = useCallback(
    async (alertId: string) => {
      try {
        await api.acknowledge(alertId);
        loadAlerts();
      } catch (e) {
        setError(signInMessage(e));
      }
    },
    [loadAlerts],
  );

  const open = useCallback(
    (insightId: string) =>
      api.insight(insightId).then(setSelected, (e) => setError(signInMessage(e))),
    [],
  );

  const selectedId = selected?.insight.insight_id;
  const pushed = useRealtime(matchId || null, true, (signal) => {
    if (signal.event === "alert_changed") return loadAlerts();
    if (signal.event !== "insight_changed") return;
    loadInsights();
    if (signal.insight_id && signal.insight_id === selectedId) open(signal.insight_id);
  });

  const decide = async (approve: boolean) => {
    if (!selected) return;
    setBusy(true);
    try {
      await api.decide(selected.insight.insight_id, approve, approve ? undefined : reason);
      setReason("");
      await open(selected.insight.insight_id);
      loadInsights();
    } catch (e) {
      setError(signInMessage(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (pushed) return; // push tells us when something changes
    const timer = setInterval(() => (loadInsights(), loadAlerts()), POLL_MS);
    return () => clearInterval(timer);
  }, [pushed, loadInsights, loadAlerts]);

  // newest first: the latest moment is at the top
  const shown = insights
    .filter((v) => filter === "all" || v.status === filter)
    .reverse()
    .sort((x, y) => minuteOf(y.insight) - minuteOf(x.insight));
  const { needYou } = openAlerts(alerts);
  const opsShown = opsOpen ?? needYou.length > 0; // opens by itself when something needs you

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center">
          <span className="sr-only">Match</span>
          <Select
            value={matchId}
            onChange={(e) => (setMatchId(e.target.value), setSelected(null), setAlerts(null), setOpsOpen(null))}
          >
            {matches.map((m) => (
              <option key={m.match_id} value={m.match_id}>
                {m.match_id} · {m.home.name} v {m.away.name}
              </option>
            ))}
          </Select>
        </label>
        <span className={`flex items-center gap-1.5 text-xs ${pushed ? "text-emerald-700" : "text-slate-500"}`}>
          <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${pushed ? "bg-emerald-500" : "bg-slate-400"}`} />
          {pushed ? "live" : `updates every ${POLL_MS / 1000} s`}
        </span>
        <div role="group" aria-label="Show" className="flex gap-0.5 rounded-lg bg-slate-100 p-0.5">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              aria-pressed={filter === f}
              className={`h-8 rounded-md px-3 text-xs font-medium text-slate-900 ${
                filter === f ? "bg-white shadow-sm" : "hover:bg-slate-200/60"
              }`}
            >
              {STATUS_LABELS[f]}{" "}
              <span className="font-normal text-slate-500">
                {f === "all" ? insights.length : insights.filter((v) => v.status === f).length}
              </span>
            </button>
          ))}
        </div>
        <div className="ml-auto">
          <OpsSummary alerts={alerts} open={opsShown} onToggle={() => setOpsOpen(!opsShown)} />
        </div>
      </div>

      {error && (
        <p className="flex items-center gap-2 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
          <CircleAlert className="h-4 w-4" aria-hidden /> {error}
        </p>
      )}

      {opsShown && <OpsPanel alerts={alerts} onAcknowledge={acknowledge} onOpenInsight={open} />}

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Card aria-label="Insights">
          <ul className="max-h-[48rem] overflow-y-auto py-1">
            {shown.length === 0 && (
              <li className="px-4 py-6 text-sm text-slate-500">Nothing here.</li>
            )}
            {shown.map(({ insight: i, status }) => (
              <li key={i.insight_id}>
                <button
                  onClick={() => open(i.insight_id)}
                  aria-current={selected?.insight.insight_id === i.insight_id}
                  className={`flex w-full items-start gap-3 px-4 py-2.5 text-left ${
                    selected?.insight.insight_id === i.insight_id ? "bg-sky-50" : "hover:bg-slate-50"
                  }`}
                >
                  <span className="w-9 shrink-0 pt-0.5 font-mono text-xs text-slate-500">
                    {minuteLabel(minuteOf(i))}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm leading-snug text-slate-900">{i.headline}</span>
                    <span className="block text-xs text-slate-500 first-letter:uppercase">
                      {i.insight_type.replaceAll("_", " ")}
                    </span>
                  </span>
                  {/* Aired is the normal state: only flag rows that need a look. */}
                  {status !== "aired" && <Badge tone={status}>{STATUS_LABELS[status]}</Badge>}
                </button>
              </li>
            ))}
          </ul>
        </Card>

        {selected ? (
          <InsightPanel
            detail={selected}
            names={names}
            busy={busy}
            reason={reason}
            onReason={setReason}
            onDecide={decide}
          />
        ) : (
          <Card className="flex items-center justify-center p-8 text-sm text-slate-500">
            Select an insight to see how it was made, stage by stage.
          </Card>
        )}
      </div>
    </div>
  );
}

function InsightPanel({
  detail,
  names,
  busy,
  reason,
  onReason,
  onDecide,
}: {
  detail: InsightDetail;
  names: Record<string, string>;
  busy: boolean;
  reason: string;
  onReason: (value: string) => void;
  onDecide: (approve: boolean) => void;
}) {
  const i = detail.insight;
  const facts = [
    AUDIT_LABELS[i.audit_status] ?? `Auditor: ${i.audit_status}`,
    i.approved_by &&
      (i.approved_by.startsWith("auto-approval rule") ? "Approved automatically (quick stat)" : "Approved by a producer"),
  ].filter(Boolean);
  const StatusIcon = detail.status === "aired" ? CheckCircle2 : detail.status === "blocked" ? XCircle : CircleAlert;
  return (
    <Card>
      <CardHeader className="space-y-2.5 px-5 py-5 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm text-slate-500">
            <span className="font-mono">{minuteLabel(minuteOf(i))}</span>
            {" · "}
            <span className="inline-block first-letter:uppercase">{i.insight_type.replaceAll("_", " ")}</span>
          </span>
          {detail.status === "aired" && (
            <Link
              href={`/matches/${i.match_id}?t=${minuteOf(i).toFixed(1)}&story=${i.insight_id}`}
              className="inline-flex h-8 items-center rounded-md border border-slate-200 px-3 text-xs font-medium text-sky-700 hover:bg-slate-50"
            >
              Open story
            </Link>
          )}
        </div>
        <h2 className="text-xl leading-snug font-semibold">{i.headline}</h2>
        <p className="max-w-prose text-[15px] leading-relaxed text-slate-700">{i.explanation}</p>
        <p className="flex items-start gap-2 text-sm text-slate-500">
          <StatusIcon className={`mt-0.5 h-4 w-4 shrink-0 ${STATUS_COLORS[detail.status]}`} aria-hidden />
          <span>
            <span className={`font-medium ${STATUS_COLORS[detail.status]}`}>{STATUS_LABELS[detail.status]}</span>
            {facts.map((fact) => ` · ${fact}`)}
          </span>
        </p>
      </CardHeader>

      {detail.status === "pending" && (
        <CardContent className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-5 sm:px-6">
          <Button variant="approve" disabled={busy} onClick={() => onDecide(true)}>
            <CheckCircle2 className="h-4 w-4" aria-hidden /> Approve and air
          </Button>
          <input
            value={reason}
            onChange={(e) => onReason(e.target.value)}
            maxLength={500}
            placeholder="Reason for blocking (optional)"
            className="h-9 min-w-48 flex-1 rounded-md border border-slate-300 px-2 text-sm"
          />
          <Button variant="block" disabled={busy} onClick={() => onDecide(false)}>
            <XCircle className="h-4 w-4" aria-hidden /> Block
          </Button>
          {busy && <span className="text-xs text-slate-500">Writing every mode and language…</span>}
        </CardContent>
      )}

      <CardContent className="px-5 py-5 sm:px-6">
        <InsightProcess detail={detail} names={names} />
      </CardContent>
    </Card>
  );
}
