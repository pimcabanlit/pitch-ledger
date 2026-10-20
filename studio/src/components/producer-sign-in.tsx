"use client";

import { LogIn, LogOut, ShieldCheck } from "lucide-react";

import { ProducerConsole } from "@/components/producer-console";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ProducerAuthProvider, useProducerAuth } from "@/lib/auth";

/** The producer console behind Microsoft Entra ID sign-in (when the deployment has it). */
export function ProducerArea() {
  return (
    <ProducerAuthProvider>
      <Gate />
    </ProducerAuthProvider>
  );
}

/** The page title, with who is signed in and Sign out on the right. */
function Header() {
  const auth = useProducerAuth();
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-xl font-semibold">Producer console</h1>
      {auth.status === "signed_in" && (
        <div className="flex items-center gap-3 text-sm text-slate-500">
          <span>{auth.name}</span>
          <button
            onClick={auth.signOut}
            className="flex items-center gap-1 font-medium text-sky-700 hover:text-sky-900"
          >
            <LogOut className="h-3.5 w-3.5" aria-hidden /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

function Gate() {
  return (
    <div className="space-y-5">
      <Header />
      <Body />
    </div>
  );
}

function Body() {
  const auth = useProducerAuth();
  if (auth.status === "loading") {
    return <Card className="p-6 text-sm text-slate-500">Checking sign-in…</Card>;
  }
  if (auth.status === "error") {
    return (
      <Card className="p-6 text-sm text-amber-900">Sign-in could not start: {auth.error}</Card>
    );
  }
  if (auth.status === "signed_out") {
    return (
      <Card className="flex flex-col items-start gap-3 p-6">
        <div className="flex items-center gap-2 text-base font-semibold">
          <ShieldCheck className="h-5 w-5 text-emerald-700" aria-hidden /> Sign in to review insights
        </div>
        <p className="max-w-2xl text-sm text-slate-600">
          {auth.access === "any_account"
            ? "Use any Microsoft account (work, school or personal). This public demo runs on synthetic data; your name is recorded with every decision you make, like a producer's would be."
            : "Producers sign in with their organisation account; only accounts with the Producer role can approve or block insights."}
        </p>
        <Button onClick={auth.signIn}>
          <LogIn className="h-4 w-4" aria-hidden /> Sign in with Microsoft
        </Button>
      </Card>
    );
  }
  return <ProducerConsole />;
}
