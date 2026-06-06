import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@supabase/supabase-js";

function slugify(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-");
}

function createServiceClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!,
  );
}

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => null);

  if (!body?.user_id || !body?.full_name || !body?.company_name) {
    return NextResponse.json({ error: "Missing required fields" }, { status: 400 });
  }

  const { user_id, full_name, company_name } = body as {
    user_id: string;
    full_name: string;
    company_name: string;
  };

  const supabase = createServiceClient();

  // Generate a unique slug — append short suffix on collision
  let slug = slugify(company_name);
  const { data: org, error: orgError } = await supabase
    .from("organizations")
    .insert({ name: company_name, slug })
    .select("id")
    .single();

  let orgId: string;

  if (orgError) {
    if (orgError.code !== "23505") {
      return NextResponse.json({ error: "Failed to create organization" }, { status: 500 });
    }
    // Unique slug collision — append random 4-char suffix
    slug = `${slug}-${Math.random().toString(36).slice(2, 6)}`;
    const { data: org2, error: org2Error } = await supabase
      .from("organizations")
      .insert({ name: company_name, slug })
      .select("id")
      .single();

    if (org2Error || !org2) {
      return NextResponse.json({ error: "Failed to create organization" }, { status: 500 });
    }
    orgId = org2.id;
  } else {
    orgId = org!.id;
  }

  // Create the user profile row
  const { error: userError } = await supabase
    .from("users")
    .insert({ id: user_id, org_id: orgId, display_name: full_name, role: "admin" });

  if (userError) {
    return NextResponse.json({ error: "Failed to create user profile" }, { status: 500 });
  }

  // Embed org_id into the JWT via app_metadata so FastAPI can read it
  const { error: metaError } = await supabase.auth.admin.updateUserById(user_id, {
    app_metadata: { org_id: orgId },
  });

  if (metaError) {
    return NextResponse.json({ error: "Failed to update user metadata" }, { status: 500 });
  }

  return NextResponse.json({ org_id: orgId });
}
