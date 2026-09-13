"use client";

import { useActionState, useState } from "react";
import { Field, inputClass, primaryButton } from "@/components/form";
import { Callout, StatusChip } from "@/components/ui";
import { iso, parseDay, type Day } from "@/lib/dates";
import { formatCurrency as fc } from "@/lib/money";
import { Goal, planFor } from "@/lib/savings";
import { saveGoal, type GoalState } from "./actions";
import { VERDICT_STATUS, VERDICT_WORDS } from "./verdicts";

export interface GoalSeed {
  name: string;
  target: number;
  saved: number;
  deadline: Day | null;
  bucket: string;
}

/**
 * The preview is the point of the form: change the target and see what it
 * asks of each check before anything is written down.
 */
export function GoalForm({ goals, anchor, cadence, spare, ratePerCheck, today }: {
  goals: GoalSeed[]; anchor: Day; cadence: string; spare: number; ratePerCheck: number; today: Day;
}) {
  const [state, action, pending] = useActionState<GoalState, FormData>(saveGoal, { status: "idle" });
  const [name, setName] = useState("Car");
  const [target, setTarget] = useState("4000");
  const [saved, setSaved] = useState("0");
  const [deadline, setDeadline] = useState("2026-12-31");
  const [bucket, setBucket] = useState("Chase Savings");

  function pickName(next: string) {
    setName(next);
    const match = goals.find((goal) => goal.name === next.trim());
    if (match) {
      setTarget(String(match.target));
      setSaved(String(match.saved));
      if (match.deadline !== null) setDeadline(iso(match.deadline));
      setBucket(match.bucket);
    }
  }

  const preview = planFor(
    new Goal(name.trim() || "Goal", Number(target) || 0, Number(saved) || 0, parseDay(deadline)),
    anchor, spare, ratePerCheck, cadence, today,
  );

  return (
    <details open={!goals.length} className="rounded-lg border border-[var(--border)] p-4">
      <summary className="cursor-pointer font-medium">Set a goal</summary>
      <form action={action} className="mt-4 space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="What for" hint={goals.length ? `Existing: ${goals.map((g) => g.name).join(", ")} — type one to change it.` : undefined}>
            <input name="name" value={name} onChange={(e) => pickName(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Target" hint="A guess is fine — change it and the numbers below follow.">
            <input name="target" type="number" min="0" step="500" value={target} onChange={(e) => setTarget(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Already put aside for it">
            <input name="saved" type="number" min="0" step="100" value={saved} onChange={(e) => setSaved(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Wanted by">
            <input name="deadline" type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} className={inputClass} />
          </Field>
          <Field label="Funded from" hint="Which savings bucket this draws on. Free text.">
            <input name="bucket" value={bucket} onChange={(e) => setBucket(e.target.value)} className={inputClass} />
          </Field>
        </div>

        <p className="flex flex-wrap items-center gap-3">
          <span>
            <strong>{fc(preview.requiredPerCheck)} per check</strong> over {preview.checksLeft} checks — against {fc(spare)} spare.
          </span>
          <StatusChip status={VERDICT_STATUS[preview.verdict]}>{VERDICT_WORDS[preview.verdict]}</StatusChip>
        </p>

        {state.status !== "idle" && <Callout tone={state.status === "ok" ? "success" : "error"}>{state.message}</Callout>}
        <button className={primaryButton} disabled={pending}>{pending ? "Saving…" : "Save this goal"}</button>
      </form>
    </details>
  );
}
