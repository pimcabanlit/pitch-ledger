import { MatchList } from "@/components/match-list";

export default function Home() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Matches</h1>
        <p className="text-sm text-slate-600">
          Pick a match to follow its audited insights, in your mode and language.
        </p>
      </div>
      <MatchList />
    </div>
  );
}
