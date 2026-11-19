// app/api/admin/integrations/google/connect/route.ts — starts the OAuth flow (super admin only).
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { buildAuthUrl } from "@/lib/integrations/google";
import { requireSuperAdmin } from "@/lib/auth"; // [REPLACE] your RBAC guard; must return the admin user + tenant scope

export async function GET(req: Request) {
  const admin = await requireSuperAdmin(); // throws/redirects when not allowed
  const tenantId = new URL(req.url).searchParams.get("tenant"); // null = platform site
  const { url, state, verifier } = buildAuthUrl();
  (await cookies()).set("google_seo_oauth", JSON.stringify({ state, verifier, tenantId, userId: admin.id, exp: Date.now() + 600_000 }), {
    httpOnly: true, secure: true, sameSite: "lax", path: "/api/admin/integrations/google", maxAge: 600,
  });
  return NextResponse.redirect(url);
}
