"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface FormState {
  fullName: string;
  companyName: string;
  email: string;
  password: string;
}

export default function SignupPage() {
  const router = useRouter();
  const [form, setForm] = useState<FormState>({
    fullName: "",
    companyName: "",
    email: "",
    password: "",
  });
  const [loading, setLoading] = useState(false);

  function setField(field: keyof FormState, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);

    const supabase = createClient();

    const { data, error: signUpError } = await supabase.auth.signUp({
      email: form.email,
      password: form.password,
    });

    if (signUpError || !data.user) {
      toast.error(signUpError?.message ?? "Signup failed. Please try again.");
      setLoading(false);
      return;
    }

    const res = await fetch("/api/auth/complete-signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: data.user.id,
        full_name: form.fullName,
        company_name: form.companyName,
      }),
    });

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      toast.error(body.error ?? "Failed to create workspace. Please try again.");
      setLoading(false);
      return;
    }

    // Refresh session so the new JWT includes org_id from app_metadata
    await supabase.auth.refreshSession();
    toast.success(`Workspace "${form.companyName}" created.`);

    router.push("/chat");
    router.refresh();
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted p-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-bold text-foreground">Nirnaya IQ</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Create your company workspace
          </p>
        </div>

        <div className="rounded-lg border border-border bg-background p-8 shadow-sm">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="fullName">Full name</Label>
              <Input
                id="fullName"
                type="text"
                value={form.fullName}
                onChange={(e) => setField("fullName", e.target.value)}
                required
                autoComplete="name"
                placeholder="Aniket Gupta"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="companyName">Company name</Label>
              <Input
                id="companyName"
                type="text"
                value={form.companyName}
                onChange={(e) => setField("companyName", e.target.value)}
                required
                placeholder="Acme Corp"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={form.email}
                onChange={(e) => setField("email", e.target.value)}
                required
                autoComplete="email"
                placeholder="you@company.com"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={form.password}
                onChange={(e) => setField("password", e.target.value)}
                required
                minLength={8}
                autoComplete="new-password"
                placeholder="Min 8 characters"
              />
            </div>

            <Button type="submit" disabled={loading} className="w-full">
              {loading && <Loader2 className="animate-spin" />}
              {loading ? "Creating workspace…" : "Create workspace"}
            </Button>
          </form>

          <p className="mt-6 text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link
              href="/login"
              className="font-medium text-primary hover:underline"
            >
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </main>
  );
}
