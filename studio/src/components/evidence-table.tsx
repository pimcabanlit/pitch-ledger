import type { EvidenceEvent } from "@/lib/api";

/** The feed events an insight cites, in citation order (minute, type, team, player, outcome). */
export function EvidenceTable({ evidence, maxHeight = "max-h-48" }: { evidence: EvidenceEvent[]; maxHeight?: string }) {
  return (
    <div className={`${maxHeight} overflow-auto rounded border border-slate-100`}>
      <table className="w-full text-xs whitespace-nowrap">
        <thead className="sticky top-0 bg-slate-50 text-left text-slate-500">
          <tr>
            <th className="px-2 py-1">Min</th>
            <th className="px-2 py-1">Event</th>
            <th className="px-2 py-1">Team</th>
            <th className="px-2 py-1">Player</th>
            <th className="px-2 py-1">Outcome</th>
            <th className="hidden px-2 py-1 sm:table-cell">Event ID</th>
          </tr>
        </thead>
        <tbody>
          {evidence.map((e) => (
            <tr key={e.event_id} className="border-t border-slate-100">
              <td className="px-2 py-1 font-mono">{e.minute.toFixed(1)}</td>
              <td className="px-2 py-1">{e.event_type.replaceAll("_", " ")}</td>
              <td className="px-2 py-1">{e.team_id}</td>
              <td className="px-2 py-1">{e.player_id}</td>
              <td className="px-2 py-1">{e.outcome}</td>
              <td className="hidden px-2 py-1 font-mono text-slate-400 sm:table-cell">{e.event_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
