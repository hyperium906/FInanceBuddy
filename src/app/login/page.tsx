import { PageTitle } from "@/components/ui";
import { safeNext } from "@/lib/auth";
import { LoginForm } from "./login-form";

export const metadata = { title: "Sign in · FinanceBuddy" };

export default async function LoginPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { next } = await searchParams;
  return (
    <div>
      <PageTitle caption="This app reads your balances and writes to your spreadsheet, so it sits behind a password.">Sign in</PageTitle>
      <LoginForm next={safeNext(next)} />
    </div>
  );
}
