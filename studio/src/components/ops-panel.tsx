"use client";

import { Check, CheckCircle2, Info, OctagonAlert, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import type { AlertSeverity, OpsAlert } from "@/lib/api";

const ICONS: Record<AlertSeverity, typeof Info> = {
  info: Info,
  warning: TriangleAlert,
  critical: OctagonAlert,
};
const ICON_COLORS: Record<AlertSeverity, string> = {
  info: "text-sky-600",
  warning: "text-amber-600",
  critical: "text-rose-600",
};
const MAX_REFS = 4;

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour12: false });
}

/**
 * Ops alerts for the selected match (feed gaps and lag, validation, blocked insights, dead
 * letters, model fallbacks), newest first. Open alerts stay on top until a producer
 * acknowledges them; insight references open that insight in the console.
 */
export function OpsPanel({
  alerts,
  onAcknowledge,
  onOpenInsight,
}: {
  alerts: OpsAlert[] | null;
  onAcknowledge: (alertId: string) => Promise<void>;
  onOpenInsight: (insightId: string) => void;
}) {
  const [showDone, setShowDone] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const open = (alerts ?? []).filter((a) => !a.acknowledged_by);
  const done = (alerts ?? []).filter((a) => a.acknowledged_by);
  const shown = showDone ? [...open, ...done] : open;
  const critical = open.some((a) => a.severity === "critical");
  const tone = critical ? "critical" : open.length ? "warning" : "aired";

  const acknowledge = async (alertId: string) => {
    setBusy(alertId);
    try {
      await onAcknowledge(alertId);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card aria-label="Ops alerts">
      <CardHeader className="flex flex-row flex-wrap items-center gap-2 text-sm font-medium">
        Ops alerts
        {alerts !== null && <Badge tone={tone}>{open.length} open</Badge>}
        {done.length > 0 && (
          <button
            onClick={() => setShowDone((v) => !v)}
            className="ml-auto text-xs font-normal text-slate-500 underline-offset-2 hover:underline"
          >
            {showDone ? "Hide" : "Show"} acknowledged ({done.length})
          </button>
        )}
      </CardHeader>
      {alerts === null ? (
        <p className="px-4 py-3 text-sm text-slate-500">Loading ops alerts…</p>
      ) : shown.length === 0 ? (
        <p className="flex items-center gap-2 px-4 py-3 text-sm text-slate-500">
          <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden /> All clear: no open ops
          alerts for this match.
        </p>
      ) : (
        <ul className="max-h-72 divide-y divide-slate-100 overflow-auto">
          {shown.map((a) => {
            const Icon = ICONS[a.severity];
            const acked = Boolean(a.acknowledged_by);
            return (
              <li
                key={a.alert_id}
                className={`flex gap-3 px-4 py-3 ${acked ? "opacity-60" : ""}`}
              >
                <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${ICON_COLORS[a.severity]}`} aria-hidden />
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                    <Badge tone={a.severity}>{a.severity}</Badge>
                    <span className="font-medium text-slate-700">{a.kind.replaceAll("_", " ")}</span>
                    <span className="font-mono">{time(a.raised_at)}</span>
                    <span className="font-mono">{a.alert_id}</span>
                  </div>
                  <p className="text-sm text-slate-800">{a.message}</p>
                  {a.refs.length > 0 && (
                    <div className="flex flex-wrap gap-1 text-xs">
                      {a.refs.slice(0, MAX_REFS).map((ref) =>
                        /-I\d{4}$/.test(ref) ? (
                          <button
                            key={ref}
                            onClick={() => onOpenInsight(ref)}
                            className="rounded bg-sky-50 px-1.5 font-mono text-sky-800 hover:bg-sky-100"
                          >
                            {ref}
                          </button>
                        ) : (
                          <span key={ref} className="rounded bg-slate-100 px-1.5 font-mono text-slate-600">
                            {ref}
                          </span>
                        ),
                      )}
                      {a.refs.length > MAX_REFS && (
                        <span className="text-slate-500">+{a.refs.length - MAX_REFS} more</span>
                      )}
                    </div>
                  )}
                  {acked && a.acknowledged_at && (
                    <p className="text-xs text-slate-500">
                      Acknowledged by {a.acknowledged_by} at {time(a.acknowledged_at)}
                    </p>
                  )}
                </div>
                {!acked && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy === a.alert_id}
                    onClick={() => acknowledge(a.alert_id)}
                    className="self-start px-2 sm:px-3"
                    aria-label="Acknowledge"
                  >
                    <Check className="h-4 w-4" aria-hidden />
                    <span className="hidden sm:inline">Acknowledge</span>
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
