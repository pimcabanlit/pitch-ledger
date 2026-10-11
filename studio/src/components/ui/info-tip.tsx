import { Info } from "lucide-react";

import { GLOSSARY, type GlossaryKey } from "@/lib/metrics";
import { cn } from "@/lib/utils";

/**
 * A small "i" that explains a term on hover, keyboard focus or tap. The text is also the
 * button's accessible description, so screen readers get it without opening anything.
 */
export function InfoTip({
  term,
  align = "center",
  className,
}: {
  term: GlossaryKey;
  align?: "start" | "center" | "end";
  className?: string;
}) {
  const id = `tip-${term}`;
  return (
    <span className={cn("group relative inline-flex align-middle", className)}>
      <button
        type="button"
        aria-label={`About ${term.replaceAll("_", " ")}`}
        aria-describedby={id}
        className="rounded-full text-slate-400 hover:text-slate-700 focus-visible:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
      >
        <Info className="h-3.5 w-3.5" aria-hidden />
      </button>
      <span
        id={id}
        role="tooltip"
        className={cn(
          "absolute top-full z-30 mt-1.5 hidden w-64 max-w-[calc(100vw-2rem)] rounded-md bg-slate-900 px-3 py-2 text-left text-xs leading-snug font-normal text-white shadow-lg group-focus-within:block group-hover:block",
          align === "start" && "left-0",
          align === "center" && "left-1/2 -translate-x-1/2",
          align === "end" && "right-0",
        )}
      >
        {GLOSSARY[term]}
      </span>
    </span>
  );
}
