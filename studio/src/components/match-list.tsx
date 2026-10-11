"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { BookCheck, ChevronRight, Star } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, type MatchSummary, type Team } from "@/lib/api";
import { usePreferences } from "@/lib/preferences";
import { cn } from "@/lib/utils";

function TeamRow({
  team,
  score,
  favourite,
  onToggle,
}: {
  team: Team;
  score: number | null;
  favourite: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="flex items-center gap-2 font-medium">
      <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: team.primary_color }} />
      <span className="truncate">{team.name}</span>
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={favourite}
        aria-label={`Favourite club: ${team.name}`}
        title={favourite ? "Favourite club (listed first)" : "Mark as a favourite club"}
        className="relative z-10 -m-1 shrink-0 rounded p-1 text-slate-300 hover:text-amber-500 focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none"
      >
        <Star className={cn("h-3.5 w-3.5", favourite && "fill-amber-400 text-amber-500")} aria-hidden />
      </button>
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
  const [{ clubs }, setPrefs] = usePreferences();
  const toggle = (team: string) =>
    setPrefs({ clubs: clubs.includes(team) ? clubs.filter((c) => c !== team) : [...clubs, team] });
  const isFavourite = (m: MatchSummary) => clubs.includes(m.home.team_id) || clubs.includes(m.away.team_id);

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
      {[...matches.filter(isFavourite), ...matches.filter((m) => !isFavourite(m))].map((m) => (
        <li key={m.match_id}>
          <Card className="relative flex h-full flex-col gap-2 p-4 transition-shadow has-[a:hover]:shadow-md">
            <div className="text-xs text-slate-500">
              {m.match_id} · {new Date(m.kickoff_utc).toLocaleDateString()} · {m.venue}
            </div>
            <TeamRow
              team={m.home}
              score={m.score_home}
              favourite={clubs.includes(m.home.team_id)}
              onToggle={() => toggle(m.home.team_id)}
            />
            <TeamRow
              team={m.away}
              score={m.score_away}
              favourite={clubs.includes(m.away.team_id)}
              onToggle={() => toggle(m.away.team_id)}
            />
            <div className="mt-auto flex items-center gap-1 pt-1 text-xs text-slate-500">
              {m.aired_insights !== null && (
                <>
                  <BookCheck className="h-3.5 w-3.5 text-emerald-600" aria-hidden />
                  {m.aired_insights} aired {m.aired_insights === 1 ? "insight" : "insights"}
                </>
              )}
              {/* The whole card opens the match; the stars sit above this link. */}
              <Link
                href={`/matches/${m.match_id}`}
                className="ml-auto flex items-center text-slate-600 after:absolute after:inset-0 after:rounded-lg hover:text-slate-900 focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-slate-400"
              >
                Watch<span className="sr-only">: {m.home.name} v {m.away.name}</span>
                <ChevronRight className="h-3.5 w-3.5" aria-hidden />
              </Link>
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
}
