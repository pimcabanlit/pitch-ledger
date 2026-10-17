import { ArrowLeftRight, Flag, Goal, RectangleVertical, Sparkles, Target } from "lucide-react";

import { minuteLabel } from "@/lib/utils";

export interface FeedItem {
  id: string;
  t: number; // replay clock (s)
  order: number; // feed order: period, then clock (an insight after its events)
  minute: number;
  kind: "goal" | "shot" | "card" | "substitution" | "period" | "insight";
  title: string;
  text?: string;
  color: string;
  insightId?: string;
  mine?: boolean; // about the followed player
}

const ICONS = {
  goal: Goal,
  shot: Target,
  card: RectangleVertical,
  substitution: ArrowLeftRight,
  period: Flag,
  insight: Sparkles,
};

/**
 * What has happened up to the replay clock, newest first; a click on the minute jumps the clock
 * there, and "Tell me more" opens an insight's story.
 */
export function Feed({
  items,
  onJump,
  onStory,
}: {
  items: FeedItem[];
  onJump: (t: number) => void;
  onStory: (insightId: string) => void;
}) {
  if (!items.length) return <p className="px-4 py-3 text-sm text-slate-500">Nothing yet: press play.</p>;
  return (
    <ol className="divide-y divide-slate-100">
      {items.map((item) => {
        const Icon = ICONS[item.kind];
        return (
          <li
            key={item.id}
            className={`${item.kind === "insight" ? "bg-sky-50/60" : ""} ${item.mine ? "border-l-2 border-sky-400" : ""}`}
          >
            <div className="flex gap-3 px-4 py-2.5">
              <button
                onClick={() => onJump(item.t)}
                className="w-9 shrink-0 text-left font-mono text-xs text-slate-500 hover:text-slate-900"
                title="Jump here"
              >
                {minuteLabel(item.minute)}
              </button>
              <Icon
                className="mt-0.5 h-4 w-4 shrink-0"
                style={{ color: item.kind === "card" ? "#eab308" : item.color }}
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <p className={`text-sm ${item.kind === "goal" ? "font-semibold" : ""}`}>{item.title}</p>
                {item.text && <p className="text-sm leading-snug text-slate-700">{item.text}</p>}
                {item.insightId && (
                  <button onClick={() => item.insightId && onStory(item.insightId)} className="text-xs text-sky-700 hover:underline">
                    Tell me more →
                  </button>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
