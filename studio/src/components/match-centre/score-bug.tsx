import type { Team } from "@/lib/api";

/**
 * The broadcast score bug: a slim bar centred at the bottom of the pitch, as on a TV picture.
 * Club colour, name and score for each side, then the match minute and the half. Short club
 * names on phones.
 */
export function ScoreBug({
  home,
  away,
  scoreHome,
  scoreAway,
  minute,
  half,
}: {
  home: Team;
  away: Team;
  scoreHome: number;
  scoreAway: number;
  minute: string;
  half: string;
}) {
  const name = (team: Team) => (
    <>
      <span className="sm:hidden">{team.short_name}</span>
      <span className="hidden sm:inline">{team.name}</span>
    </>
  );
  return (
    <div
      className="pointer-events-none absolute bottom-1.5 left-1/2 z-10 -translate-x-1/2 sm:bottom-2.5"
      role="group"
      aria-label={`Score: ${home.name} ${scoreHome}, ${away.name} ${scoreAway}, ${minute}, ${half}`}
    >
      <div
        className="flex items-stretch overflow-hidden rounded-md bg-slate-950/90 text-[11px] whitespace-nowrap text-white shadow-lg ring-1 ring-white/15 sm:text-sm"
        aria-hidden
      >
        <span className="w-1.5 shrink-0" style={{ background: home.primary_color }} />
        <span className="flex items-center px-2 py-1 font-semibold tracking-wide uppercase sm:px-3">
          {name(home)}
        </span>
        <span className="flex items-center bg-white px-2 font-mono font-bold text-slate-950 tabular-nums sm:px-3">
          {scoreHome} – {scoreAway}
        </span>
        <span className="flex items-center px-2 py-1 font-semibold tracking-wide uppercase sm:px-3">
          {name(away)}
        </span>
        <span className="w-1.5 shrink-0" style={{ background: away.primary_color }} />
        <span className="flex items-center gap-1.5 border-l border-white/20 px-2 font-mono sm:px-3">
          <span className="font-semibold">{minute}</span>
          <span className="text-[10px] text-white/70 sm:text-xs">{half}</span>
        </span>
      </div>
    </div>
  );
}
