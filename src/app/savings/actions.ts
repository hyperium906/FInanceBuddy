"use server";

import { revalidateTag } from "next/cache";
import { iso, parseDay } from "@/lib/dates";
import { TAB } from "@/lib/models";
import { parseMoney } from "@/lib/money";
import { readGoals } from "@/lib/savings";
import { loadData, SHEET_TAG } from "@/lib/server/data";
import { appendRecords, newId, updateRecord } from "@/lib/server/sheets";

export type GoalState = { status: "idle" } | { status: "ok" | "error"; message: string };

/** Create a goal, or change one — the target especially — matched by name. */
export async function saveGoal(_prev: GoalState, form: FormData): Promise<GoalState> {
  const name = String(form.get("name") ?? "").trim();
  const target = parseMoney(form.get("target")) ?? 0;
  const saved = parseMoney(form.get("saved")) ?? 0;
  const deadline = parseDay(form.get("deadline"));
  const bucket = String(form.get("bucket") ?? "").trim();

  if (!name) return { status: "error", message: "It needs a name." };
  if (target <= 0) return { status: "error", message: "It needs a target above zero." };

  try {
    const existing = readGoals((await loadData()).goals).find((goal) => goal.name === name);
    const fields = {
      "Target Amount": target,
      "Saved Amount": saved,
      "Target Date": deadline === null ? "" : iso(deadline),
      Bucket: bucket,
    };
    if (existing?.goalId) {
      await updateRecord(TAB.goals, "Goal ID", existing.goalId, fields, `No goal with Goal ID '${existing.goalId}'.`);
    } else {
      await appendRecords(TAB.goals, [{ "Goal ID": newId("g"), Name: name, "Account ID": "", Notes: "", ...fields }]);
    }
    revalidateTag(SHEET_TAG, { expire: 0 });
    return { status: "ok", message: `${existing?.goalId ? "Updated" : "Added"} ${name}.` };
  } catch (error) {
    return { status: "error", message: (error as Error).message };
  }
}
