"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { safeEqual, safeNext, SESSION_COOKIE, SESSION_DAYS, sessionToken } from "@/lib/auth";

export type LoginState = { error: string } | null;

export async function login(_prev: LoginState, form: FormData): Promise<LoginState> {
  const password = process.env.APP_PASSWORD ?? "";
  if (!password) redirect(safeNext(form.get("next")));

  const given = String(form.get("password") ?? "");
  const [expected, attempt] = await Promise.all([sessionToken(password), sessionToken(given)]);
  if (!safeEqual(attempt, expected)) return { error: "That is not the password." };

  (await cookies()).set(SESSION_COOKIE, expected, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_DAYS * 24 * 60 * 60,
  });
  redirect(safeNext(form.get("next")));
}
