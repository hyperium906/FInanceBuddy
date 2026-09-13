"use client";

import { useActionState } from "react";
import { Field, inputClass, primaryButton } from "@/components/form";
import { Callout } from "@/components/ui";
import { login, type LoginState } from "./actions";

export function LoginForm({ next }: { next: string }) {
  const [state, action, pending] = useActionState<LoginState, FormData>(login, null);
  return (
    <form action={action} className="max-w-sm space-y-4">
      <input type="hidden" name="next" value={next} />
      <Field label="Password">
        <input type="password" name="password" autoComplete="current-password" autoFocus required className={inputClass} />
      </Field>
      {state?.error && <Callout tone="error">{state.error}</Callout>}
      <button className={primaryButton} disabled={pending}>{pending ? "Checking…" : "Sign in"}</button>
    </form>
  );
}
