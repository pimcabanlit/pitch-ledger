"use client";

import { CheckCircle2, CircleAlert, ShieldCheck, XCircle } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { EvidenceTable } from "@/components/evidence-table";
import { OpsPanel } from "@/components/ops-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import {
  ApiError,
  api,
  type InsightDetail,
  type InsightStatus,
  type InsightView,
  type MatchSummary,
  type OpsAlert,
} from "@/lib/api";
import { useRealtime } from "@/lib/realtime";
import { matchMinute, minuteLabel } from "@/lib/utils";

const FILTERS: (InsightStatus | "all")[] = ["pending", "aired", "blocked", "all"];
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
  const [alerts, setAlerts] = useState<OpsAlert[] | null>(null); // null: not loaded yet
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
              {f} ({f === "all" ? insights.length : insights.filter((v) => v.status === f).length})
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

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
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
                      {status}
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
            busy={busy}
            reason={reason}
            onReason={setReason}
            onDecide={decide}
          />
        ) : (
          <Card className="flex items-center justify-center p-8 text-sm text-slate-500">
            Select an insight to review its audit trail and evidence.
          </Card>
        )}
      </div>
    </div>
  );
}

function InsightPanel({
  detail,
  busy,
  reason,
  onReason,
  onDecide,
}: {
  detail: InsightDetail;
  busy: boolean;
  reason: string;
  onReason: (value: string) => void;
  onDecide: (approve: boolean) => void;
}) {
  const i = detail.insight;
  const preview = detail.overlays.filter(
    (o) => ["analyst", "casual"].includes(o.audience_mode) && ["en", "es"].includes(o.language),
  );
  return (
    <Card>
      <CardHeader className="space-y-1">
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span className="font-mono">{i.insight_id}</span>
          <Badge tone={detail.status}>{detail.status}</Badge>
          <Badge tone={auditTone(i.audit_status)}>audit: {i.audit_status}</Badge>
          {i.approved_by && <span>approved by {i.approved_by}</span>}
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

      <CardContent className="space-y-4">
        <section>
          <h3 className="mb-2 flex items-center gap-1 text-sm font-medium">
            <ShieldCheck className="h-4 w-4 text-emerald-600" aria-hidden /> Audit trail
          </h3>
          <ol className="space-y-1">
            {detail.audits.map((a) => (
              <li key={a.audit_id} className="flex gap-2 text-xs">
                <span
                  className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
                    a.passed === false ? "bg-rose-500" : a.passed ? "bg-emerald-500" : "bg-slate-300"
                  }`}
                />
                <span className="w-40 shrink-0 font-mono text-slate-500">
                  r{a.round} {a.actor} · {a.action}
                </span>
                <span className="text-slate-700">{a.findings.join("; ")}</span>
              </li>
            ))}
          </ol>
        </section>

        <section>
          <h3 className="mb-2 text-sm font-medium">Metrics (computed in code)</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(i.metrics).map(([k, v]) => (
              <Badge key={k}>
                {k.replaceAll("_", " ")}: {v}
              </Badge>
            ))}
          </div>
        </section>

        <section>
          <h3 className="mb-2 text-sm font-medium">Evidence events ({detail.evidence.length})</h3>
          <EvidenceTable evidence={detail.evidence} />
        </section>

        {preview.length > 0 && (
          <section>
            <h3 className="mb-2 text-sm font-medium">On air (analyst and casual, en and es)</h3>
            <ul className="space-y-1">
              {preview.map((o) => (
                <li key={o.overlay_id} className="text-sm">
                  <Badge className="mr-2">
                    {o.audience_mode}/{o.language}
                  </Badge>
                  {o.text}
                </li>
              ))}
            </ul>
          </section>
        )}
      </CardContent>
    </Card>
  );
}
