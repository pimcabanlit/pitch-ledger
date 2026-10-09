"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Card } from "@/components/ui/card";
import { api, type MatchSummary } from "@/lib/api";

export function MatchList() {
  const [matches, setMatches] = useState<MatchSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.matches().then(setMatches, (e: Error) => setError(e.message));
  }, []);

  if (error) return <p className="text-sm text-rose-700">Could not load matches: {error}</p>;
  if (!matches) return <p className="text-sm text-slate-500">Loading matches…</p>;
  if (matches.length === 0) return <p className="text-sm text-slate-500">No matches yet.</p>;

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {matches.map((m) => (
        <Link key={m.match_id} href={`/matches/${m.match_id}`}>
          <Card className="p-4 transition-shadow hover:shadow-md">
            <div className="text-xs text-slate-500">
              {m.match_id} · {new Date(m.kickoff_utc).toLocaleDateString()} · {m.venue}
            </div>
            <div className="mt-2 flex items-center gap-2 font-medium">
              <span className="h-3 w-3 rounded-full" style={{ background: m.home.primary_color }} />
              {m.home.name}
            </div>
            <div className="flex items-center gap-2 font-medium">
              <span className="h-3 w-3 rounded-full" style={{ background: m.away.primary_color }} />
              {m.away.name}
            </div>
          </Card>
        </Link>
      ))}
    </div>
  );
}
