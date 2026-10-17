"use client";

import { Check, CheckCircle2, ChevronRight, Info, OctagonAlert, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import type { AlertView } from "@/lib/api";

const MAX_REFS = 4;

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit" });
}

/** How urgent an alert is, in a producer's words: act now, needs you, or just so you know. */
function urgency(a: AlertView) {
  if (a.severity === "critical") return { label: "Act now", tone: "critical", Icon: OctagonAlert, color: "text-rose-600" } as const;
  if (a.needs_action) return { label: "Needs you", tone: "warning", Icon: TriangleAlert, color: "text-amber-600" } as const;
  return { label: "FYI", tone: "info", Icon: Info, color: "text-sky-600" } as const;
}

/**
 * Ops alerts for the selected match, newest first: one plain headline per alert (what
 * happened) and what to do about it, with the details and the technical log behind a
 * toggle. Open alerts stay on top until a producer acknowledges them. The panel starts
 * collapsed to its header, whose badges still say whether anything needs the producer.
 */
export function OpsPanel({
  alerts,
  onAcknowledge,
  onOpenInsight,
}: {
  alerts: AlertView[] | null;
  onAcknowledge: (alertId: string) => Promise<void>;
  onOpenInsight: (insightId: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(true); // details on demand; the badges say if anything needs you
  const [showDone, setShowDone] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const open = (alerts ?? []).filter((a) => !a.acknowledged_by);
  const done = (alerts ?? []).filter((a) => a.acknowledged_by);
  const shown = showDone ? [...open, ...done] : open;
  const needYou = open.filter((a) => a.needs_action || a.severity === "critical");
  const fyi = open.filter((a) => !a.needs_action && a.severity !== "critical");

  const acknowledge = async (...alertIds: string[]) => {
    setBusy(alertIds.length > 1 ? "all" : alertIds[0]);
    try {
      for (const id of alertIds) await onAcknowledge(id);
    } finally {
      setBusy(null);
    }
  };

  const toggle = (alertId: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(alertId)) next.delete(alertId);
      else next.add(alertId);
      return next;
    });

  return (
    <Card aria-label="Ops alerts">
      <CardHeader className={`flex flex-row flex-wrap items-center gap-2 text-sm font-medium ${collapsed ? "border-b-0" : ""}`}>
        <button
          onClick={() => setCollapsed((v) => !v)}
          aria-expanded={!collapsed}
          className="flex items-center gap-1 hover:text-slate-700"
        >
          <ChevronRight className={`h-4 w-4 transition-transform ${collapsed ? "" : "rotate-90"}`} aria-hidden />
          Ops alerts
        </button>
        {alerts !== null &&
          (needYou.length ? (
            <Badge tone={needYou.some((a) => a.severity === "critical") ? "critical" : "warning"}>
              {needYou.length} need{needYou.length === 1 ? "s" : ""} you
            </Badge>
          ) : (
            <Badge tone="aired">Nothing needs you</Badge>
          ))}
        {fyi.length > 0 && <span className="text-xs font-normal text-slate-500">{fyi.length} FYI</span>}
        {collapsed ? (
          <button
            onClick={() => setCollapsed(false)}
            className="ml-auto text-xs font-normal text-sky-700 underline-offset-2 hover:underline"
          >
            Show details
          </button>
        ) : (
        <span className="ml-auto flex items-center gap-3">
          {fyi.length > 1 && (
            <button
              onClick={() => acknowledge(...fyi.map((a) => a.alert_id))}
              disabled={busy !== null}
              className="text-xs font-normal text-sky-700 underline-offset-2 hover:underline disabled:opacity-50"
            >
              Acknowledge all FYI
            </button>
          )}
          {done.length > 0 && (
            <button
              onClick={() => setShowDone((v) => !v)}
              className="text-xs font-normal text-slate-500 underline-offset-2 hover:underline"
            >
              {showDone ? "Hide" : "Show"} acknowledged ({done.length})
            </button>
          )}
        </span>
        )}
      </CardHeader>
      {collapsed ? null : alerts === null ? (
        <p className="px-4 py-3 text-sm text-slate-500">Loading ops alerts…</p>
      ) : shown.length === 0 ? (
        <p className="flex items-center gap-2 px-4 py-3 text-sm text-slate-500">
          <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden /> All clear: no open ops
          alerts for this match.
        </p>
      ) : (
        <ul className="max-h-96 divide-y divide-slate-100 overflow-auto">
          {shown.map((a) => {
            const { label, tone, Icon, color } = urgency(a);
            const acked = Boolean(a.acknowledged_by);
            const isOpen = expanded.has(a.alert_id);
            const insightId = a.refs.find((ref) => /-I\d{4}$/.test(ref));
            return (
              <li key={a.alert_id} className={`flex gap-3 px-4 py-3 ${acked ? "opacity-60" : ""}`}>
                <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${color}`} aria-hidden />
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="text-sm font-medium text-slate-900">
                    <Badge tone={tone} className="mr-2 align-[1px]">
                      {label}
                    </Badge>
                    {a.headline}
                  </p>
                  <p className={`text-sm ${a.needs_action ? "font-medium text-slate-800" : "text-slate-600"}`}>
                    {a.action}
                  </p>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
                    <span>{time(a.raised_at)}</span>
                    {insightId && (
                      <button onClick={() => onOpenInsight(insightId)} className="text-sky-700 hover:underline">
                        Open insight
                      </button>
                    )}
                    <button
                      onClick={() => toggle(a.alert_id)}
                      aria-expanded={isOpen}
                      className="flex items-center gap-0.5 hover:text-slate-700"
                    >
                      <ChevronRight className={`h-3 w-3 transition-transform ${isOpen ? "rotate-90" : ""}`} aria-hidden />
                      Details
                    </button>
                    {acked && a.acknowledged_at && (
                      <span>
                        Acknowledged by {a.acknowledged_by} at {time(a.acknowledged_at)}
                      </span>
                    )}
                  </div>
                  {isOpen && (
                    <div className="mt-1 space-y-2 rounded-md bg-slate-50 p-2 text-xs text-slate-600">
                      {a.details.map((line) => (
                        <p key={line}>{line}</p>
                      ))}
                      <div className="space-y-1 border-t border-slate-200 pt-2">
                        <p className="font-medium text-slate-500">
                          Technical log · {a.kind.replaceAll("_", " ")} · {a.alert_id}
                        </p>
                        <p className="font-mono break-words text-slate-500">{a.message}</p>
                        {a.refs.length > 0 && (
                          <p className="font-mono text-slate-400">
                            {a.refs.slice(0, MAX_REFS).join(" · ")}
                            {a.refs.length > MAX_REFS && ` +${a.refs.length - MAX_REFS} more`}
                          </p>
                        )}
                      </div>
                    </div>
                  )}
                </div>
                {!acked && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy !== null}
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
