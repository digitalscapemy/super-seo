// app/api/admin/integrations/google/callback/route.ts — verifies signed state, stores the encrypted refresh token.
import { timingSafeEqual } from "node:crypto";
import { NextResponse } from "next/server";
import { exchangeCode } from "@/lib/integrations/google";
import { takeFlowState } from "@/lib/integrations/oauth-state";
import { requireSuperAdmin, canManageTenant } from "@/lib/auth"; // [REPLACE]
import { encrypt } from "@/lib/crypto";                           // [REPLACE] AES-256-GCM with a KMS/env key; never store tokens in plain text
import { db } from "@/lib/db";                                    // [REPLACE] Prisma/Drizzle client

export async function GET(req: Request) {
  const admin = await requireSuperAdmin();
  const params = new URL(req.url).searchParams;
  const saved = await takeFlowState(); // null if missing, tampered, expired or already used
  const back = new URL("/admin/integrations", req.url);

  const state = params.get("state") ?? "";
  const stateOk = !!saved && saved.userId === String(admin.id) && state.length === saved.state.length
    && timingSafeEqual(Buffer.from(state), Buffer.from(saved.state));
  // Re-check authorisation at callback time: permissions may have changed, and the tenant comes from stored state.
  if (!stateOk || !(await canManageTenant(admin, saved!.tenantId)) || params.get("error") || !params.get("code")) {
    back.searchParams.set("error", "Google connection failed or was cancelled.");
    return NextResponse.redirect(back);
  }
  try {
    const { refreshToken, scopes, email } = await exchangeCode(params.get("code")!, saved!.verifier);
    await db.seoIntegration.upsert({
      where: { tenantId_provider: { tenantId: saved!.tenantId, provider: "google" } },
      create: { tenantId: saved!.tenantId, provider: "google", authType: "oauth", refreshToken: encrypt(refreshToken),
                scopes, accountEmail: email, status: "pending", connectedBy: admin.id },
      update: { authType: "oauth", refreshToken: encrypt(refreshToken), scopes, accountEmail: email, status: "pending", lastError: null },
    });
    back.searchParams.set("pick_property", "1");
  } catch (e) {
    back.searchParams.set("error", e instanceof Error ? e.message : "Google connection failed.");
  }
  return NextResponse.redirect(back);
}
