import { ProducerConsole } from "@/components/producer-console";

export default function ProducerPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Producer console</h1>
        <p className="text-sm text-slate-600">
          Every insight has been checked by the Auditor against its evidence events. Approve to
          air it in every mode and language, or block it.
        </p>
      </div>
      <ProducerConsole />
    </div>
  );
}
