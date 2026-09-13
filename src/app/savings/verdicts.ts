import type { GoalVerdict } from "@/lib/savings";
import type { Status } from "@/lib/spending";

export const VERDICT_STATUS: Record<GoalVerdict, Status> = {
  met: "green", "on-track": "green", stretch: "amber", missing: "red", "no-date": "none",
};

export const VERDICT_WORDS: Record<GoalVerdict, string> = {
  met: "already there",
  "on-track": "reachable out of what is spare",
  stretch: "needs more than is spare — something has to give",
  missing: "not reachable by that date on this income",
  "no-date": "no deadline set",
};
