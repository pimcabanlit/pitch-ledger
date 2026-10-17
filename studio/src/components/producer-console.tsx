"use client";

import { CheckCircle2, CircleAlert, XCircle } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { InsightProcess } from "@/components/insight-process";
import { OpsPanel } from "@/components/ops-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import {
  ApiError,
  api,
  type AlertView,
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
const AUDIT_LABELS: Record<string, string> = {
  pending: "Auditor: still checking",
  approved: "Auditor: passed",
  corrected: "Auditor: passed after a fix",
  blocked: "Auditor: blocked",
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

function auditTone(status: string) {
  return status === "corrected" ? "corrected" : status === "blocked" ? "blocked" : "neutral";
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

  const shown = insights.filter((v) => filter === "all" || v.status === filter);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-600">
          Match
          <Select
            value={matchId}
            onChange={(e) => (setMatchId(e.target.value), setSelected(null), setAlerts(null))}
          >
            {matches.map((m) => (
              <option key={m.match_id} value={m.match_id}>
                {m.match_id} · {m.home.name} v {m.away.name}
              </option>
            ))}
          </Select>
        </label>
        <span className={`text-xs ${pushed ? "text-emerald-700" : "text-slate-500"}`}>
          {pushed ? "● live" : `○ updates every ${POLL_MS / 1000} s`}
        </span>
        <div className="flex gap-1">
          {FILTERS.map((f) => (
            <Button
              key={f}
              size="sm"
              variant={filter === f ? "default" : "outline"}
              onClick={() => setFilter(f)}
            >
              {STATUS_LABELS[f]} ({f === "all" ? insights.length : insights.filter((v) => v.status === f).length})
            </Button>
          ))}
        </div>
      </div>

      {error && (
        <p className="flex items-center gap-2 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
          <CircleAlert className="h-4 w-4" aria-hidden /> {error}
        </p>
      )}

      <OpsPanel alerts={alerts} onAcknowledge={acknowledge} onOpenInsight={open} />

      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Card>
          <CardHeader className="text-sm font-medium">Insights</CardHeader>
          <ul className="divide-y divide-slate-100">
            {shown.length === 0 && (
              <li className="px-4 py-3 text-sm text-slate-500">Nothing here.</li>
            )}
            {shown.map(({ insight: i, status }) => (
              <li key={i.insight_id}>
                <button
                  onClick={() => open(i.insight_id)}
                  className={`w-full px-4 py-3 text-left hover:bg-slate-50 ${
                    selected?.insight.insight_id === i.insight_id ? "bg-sky-50" : ""
                  }`}
                >
                  <div className="flex items-center gap-2 text-xs text-slate-500">
                    <span className="font-mono">{minuteLabel(matchMinute(i.period, i.clock_ms))}</span>
                    <span>{i.insight_type.replaceAll("_", " ")}</span>
                    <Badge tone={status} className="ml-auto">
                      {STATUS_LABELS[status]}
                    </Badge>
                  </div>
                  <div className="mt-1 text-sm font-medium">{i.headline}</div>
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
  return (
    <Card>
      <CardHeader className="space-y-1">
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span className="font-mono">{minuteLabel(matchMinute(i.period, i.clock_ms))}</span>
          <span>{i.insight_type.replaceAll("_", " ")}</span>
          <Badge tone={detail.status}>{STATUS_LABELS[detail.status]}</Badge>
          <Badge tone={auditTone(i.audit_status)}>{AUDIT_LABELS[i.audit_status] ?? `Auditor: ${i.audit_status}`}</Badge>
          {i.approved_by && (
            <span>
              {i.approved_by.startsWith("auto-approval rule") ? "approved automatically (quick stat)" : "approved by a producer"}
            </span>
          )}
          {detail.status === "aired" && (
            <Link href={`/insights/${i.insight_id}`} className="ml-auto text-sky-700 hover:underline">
              Open story →
            </Link>
          )}
        </div>
        <h2 className="text-lg font-semibold">{i.headline}</h2>
        <p className="text-sm leading-relaxed text-slate-700">{i.explanation}</p>
      </CardHeader>

      {detail.status === "pending" && (
        <CardContent className="flex flex-wrap items-center gap-2 border-b border-slate-100">
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

      <CardContent>
        <InsightProcess detail={detail} names={names} />
      </CardContent>
    </Card>
  );
}
