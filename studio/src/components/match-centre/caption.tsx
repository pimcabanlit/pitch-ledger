import Link from "next/link";

import type { Overlay } from "@/lib/api";
import { minuteLabel, matchMinute } from "@/lib/utils";

/**
 * The broadcast lower-third for one aired insight: minute, subject and the overlay text in
 * the viewer's mode and language, with a link to how it was checked.
 */
export function Caption({
  overlay,
  subject,
  color,
  inline = false,
}: {
  overlay: Overlay;
  subject: string;
  color: string;
  inline?: boolean; // phones: below the pitch instead of over it
}) {
  return (
    <div
      className={
        inline
          ? "sm:hidden"
          : "pointer-events-none absolute inset-x-6 bottom-4 hidden sm:block"
      }
    >
      <div
        key={overlay.overlay_id}
        className="pointer-events-auto flex animate-[caption-in_300ms_ease-out] items-stretch overflow-hidden rounded-md bg-slate-950 text-white shadow-lg ring-1 ring-white/10"
      >
        <div className="w-1.5 shrink-0" style={{ background: color }} />
        <div className="flex items-center px-3 font-mono text-lg font-semibold sm:text-2xl">
          {minuteLabel(matchMinute(overlay.period, overlay.trigger_clock_ms))}
        </div>
        <div className="min-w-0 flex-1 border-l border-white/10 px-3 py-2">
          <div className="flex items-center gap-2 text-[10px] font-semibold tracking-wider text-emerald-300 uppercase sm:text-xs">
            <span>{overlay.template.replaceAll("_", " ")}</span>
            <span className="text-white/50">·</span>
            <span className="truncate text-white/80">{subject}</span>
          </div>
          <p className="text-sm leading-snug sm:text-base">{overlay.text}</p>
        </div>
        <Link
          href={`/insights/${overlay.insight_id}`}
          className="flex shrink-0 items-center border-l border-white/10 px-3 text-xs text-sky-300 hover:bg-white/5"
        >
          How we know →
        </Link>
      </div>
    </div>
  );
}
