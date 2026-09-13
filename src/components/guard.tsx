/**
 * The error boundary every page renders inside.
 *
 * A Sheets failure reaches the reader as a sentence and a retry button rather
 * than a stack trace. It is caught here, in the server component, because a
 * production build replaces thrown messages with an opaque digest — and on
 * this app a raw trace would print cell values, which is to say balances.
 */

import { unstable_rethrow } from "next/navigation";
import { connection } from "next/server";
import type { ReactNode } from "react";
import { refreshData } from "@/app/actions";
import { PayScheduleError } from "@/lib/periods";
import { ConfigError } from "@/lib/server/config";
import { loadData, type SheetData } from "@/lib/server/data";
import { SheetsError } from "@/lib/server/sheets";
import { Callout, PageTitle } from "./ui";

export async function withSheet(title: string, render: (data: SheetData) => ReactNode | Promise<ReactNode>) {
  // Every page is about today and the sheet as it is now. Without this, a page
  // with no request-specific input is prerendered at build time — a snapshot
  // of balances baked into the deployment, with "today" frozen in it.
  await connection();
  try {
    const data = await loadData();
    return await render(data);
  } catch (error) {
    unstable_rethrow(error);
    return <PageError title={title} error={error} />;
  }
}

function PageError({ title, error }: { title: string; error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  let hint = "This is a bug. The rest of the app still works.";
  let shown = `${title} could not be drawn: ${message}`;
  if (error instanceof SheetsError) {
    hint = "The sheet could not be read. Check that it is shared with the service account, and the tab names.";
    shown = message;
  } else if (error instanceof PayScheduleError) {
    hint = "Set it in the _Config tab, then refresh.";
    shown = message;
  } else if (error instanceof ConfigError) {
    hint = "Fix the environment variables, then restart the dev server (or redeploy).";
    shown = message;
  }
  if (process.env.NODE_ENV !== "production") console.error(error);

  return (
    <div>
      <PageTitle>{title}</PageTitle>
      <div className="space-y-3">
        <Callout tone="error">{shown}</Callout>
        <p className="text-sm text-[var(--muted)]">{hint}</p>
        <form action={refreshData}>
          <button className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--panel)]">Try again</button>
        </form>
      </div>
    </div>
  );
}
