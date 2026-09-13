import { NextResponse, type NextRequest } from "next/server";
import { safeEqual, SESSION_COOKIE, sessionToken } from "@/lib/auth";

/**
 * Every page and server action sits behind APP_PASSWORD. Locally an unset
 * password leaves the app open; deployed, an unset password refuses to serve
 * at all rather than publishing balances to anyone holding the URL.
 */
export async function proxy(request: NextRequest) {
  const password = process.env.APP_PASSWORD ?? "";
  if (!password) {
    if (process.env.VERCEL) {
      return new NextResponse("APP_PASSWORD is not set, so FinanceBuddy will not serve your finances. Add it in the Vercel project settings.", {
        status: 503,
      });
    }
    return NextResponse.next();
  }

  const token = request.cookies.get(SESSION_COOKIE)?.value ?? "";
  if (token && safeEqual(token, await sessionToken(password))) return NextResponse.next();

  const login = new URL("/login", request.url);
  login.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!login|_next/static|_next/image|favicon.ico).*)"],
};
