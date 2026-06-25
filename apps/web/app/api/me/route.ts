import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

export async function GET(): Promise<NextResponse> {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // Pull profile + org via RLS. We embed org_id in app_metadata at signup,
  // so even if the users row hasn't synced yet, we can still surface the org.
  const orgIdFromJwt = (user.app_metadata?.org_id ?? null) as string | null;

  const [{ data: profile }, { data: org }] = await Promise.all([
    supabase
      .from("users")
      .select(
        "id, org_id, role, display_name, activity_private, persona, custom_persona_name, custom_persona_instructions",
      )
      .eq("id", user.id)
      .maybeSingle(),
    orgIdFromJwt
      ? supabase
          .from("organizations")
          .select("id, name, slug, plan")
          .eq("id", orgIdFromJwt)
          .maybeSingle()
      : Promise.resolve({ data: null }),
  ]);

  return NextResponse.json({
    user: {
      id: user.id,
      email: user.email,
      display_name: profile?.display_name ?? user.email?.split("@")[0] ?? null,
      role: profile?.role ?? "member",
      org_id: profile?.org_id ?? orgIdFromJwt,
      activity_private: profile?.activity_private ?? false,
      persona: profile?.persona ?? null,
      custom_persona_name: profile?.custom_persona_name ?? null,
      custom_persona_instructions: profile?.custom_persona_instructions ?? null,
    },
    organization: org ?? null,
  });
}
