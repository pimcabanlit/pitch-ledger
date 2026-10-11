"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { BookCheck, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, type MatchSummary, type Team } from "@/lib/api";

function TeamRow({ team, score }: { team: Team; score: number | null }) {
  return (
    <div className="flex items-center gap-2 font-medium">
      <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: team.primary_color }} />
      <span className="truncate">{team.name}</span>
      <span className="ml-auto text-lg tabular-nums">{score ?? "–"}</span>
    </div>
  );
}

function MatchCardSkeleton() {
  return (
    <Card className="animate-pulse space-y-3 p-4" aria-hidden>
      <div className="h-3 w-1/2 rounded bg-slate-100" />
      <div className="h-4 w-3/4 rounded bg-slate-100" />
      <div className="h-4 w-2/3 rounded bg-slate-100" />
      <div className="h-3 w-1/3 rounded bg-slate-100" />
    </Card>
  );
}

export function MatchList() {
  const [matches, setMatches] = useState<MatchSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.matches().then(setMatches, (e: Error) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  const retry = () => {
    setError(null);
    load();
  };

  if (error) {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-rose-700">
        Could not load matches: {error}
        <Button variant="outline" size="sm" onClick={retry}>
          Try again
        </Button>
      </div>
    );
  }
  if (!matches) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true">
        <span className="sr-only">Loading matches…</span>
        {[0, 1, 2].map((i) => (
          <MatchCardSkeleton key={i} />
        ))}
      </div>
    );
  }
  if (matches.length === 0) return <p className="text-sm text-slate-500">No matches yet.</p>;

  return (
    <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {matches.map((m) => (
        <li key={m.match_id}>
          <Link href={`/matches/${m.match_id}`} className="group block h-full">
            <Card className="flex h-full flex-col gap-2 p-4 transition-shadow group-hover:shadow-md">
              <div className="text-xs text-slate-500">
                {m.match_id} · {new Date(m.kickoff_utc).toLocaleDateString()} · {m.venue}
              </div>
              <TeamRow team={m.home} score={m.score_home} />
              <TeamRow team={m.away} score={m.score_away} />
              <div className="mt-auto flex items-center gap-1 pt-1 text-xs text-slate-500">
                {m.aired_insights !== null && (
                  <>
                    <BookCheck className="h-3.5 w-3.5 text-emerald-600" aria-hidden />
                    {m.aired_insights} aired {m.aired_insights === 1 ? "insight" : "insights"}
                  </>
                )}
                <span className="ml-auto flex items-center text-slate-600 group-hover:text-slate-900">
                  Watch <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                </span>
              </div>
            </Card>
          </Link>
        </li>
      ))}
    </ul>
  );
}
