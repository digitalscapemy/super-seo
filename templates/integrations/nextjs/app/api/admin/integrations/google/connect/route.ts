// app/api/admin/integrations/google/connect/route.ts — starts the OAuth flow (super admin only).
import { NextResponse } from "next/server";
import { buildAuthUrl } from "@/lib/integrations/google";
import { saveFlowState } from "@/lib/integrations/oauth-state";
import { requireSuperAdmin, canManageTenant } from "@/lib/auth"; // [REPLACE] your RBAC guard + tenant permission check

export async function GET(req: Request) {
  const admin = await requireSuperAdmin(); // throws/redirects when not allowed
  const tenantId = new URL(req.url).searchParams.get("tenant"); // null = platform site
  if (!(await canManageTenant(admin, tenantId))) return new NextResponse("Forbidden", { status: 403 });
  const { url, state, verifier } = buildAuthUrl();
  await saveFlowState({ state, verifier, tenantId, userId: String(admin.id) }); // HMAC-signed __Host- cookie
  return NextResponse.redirect(url);
}
