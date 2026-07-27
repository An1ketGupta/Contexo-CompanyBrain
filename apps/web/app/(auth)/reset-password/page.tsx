"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { createClient } from "@/lib/supabase/client";
import { Logo } from "@/components/brand/logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type SessionState = "loading" | "ready" | "invalid";

export default function ResetPasswordPage() {
  const router = useRouter();
  const [sessionState, setSessionState] = useState<SessionState>("loading");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Supabase exchanges the reset-link token for a recovery session before
  // it routes the browser here. If no recovery session exists (link expired,
  // direct nav, already-consumed), we surface a clear retry path.
  useEffect(() => {
    const supabase = createClient();
    let unsubscribed = false;

    (async () => {
      const { data } = await supabase.auth.getSession();
      if (unsubscribed) return;
      if (data.session) {
        setSessionState("ready");
      } else {
        setSessionState("invalid");
      }
    })();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event) => {
      if (event === "PASSWORD_RECOVERY") {
        setSessionState("ready");
      }
    });

    return () => {
      unsubscribed = true;
      subscription.unsubscribe();
    };
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErrorMsg(null);

    if (password.length < 8) {
      setErrorMsg("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirmPassword) {
      setErrorMsg("Passwords do not match.");
      return;
    }

    setLoading(true);
    const supabase = createClient();
    const { error } = await supabase.auth.updateUser({ password });
    setLoading(false);

    if (error) {
      setErrorMsg(error.message);
      return;
    }

    // Sign out of the recovery session so the user has to log in fresh with
    // the new credential — recovery sessions shouldn't grant long-lived
    // app access.
    await supabase.auth.signOut();

    toast.success("Password updated. Please sign in.");
    router.replace("/login?reset=success");
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted p-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="flex justify-center">
            <Logo height={26} />
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Set a new password
          </p>
        </div>

        <div className="rounded-lg border border-border bg-background p-8 shadow-sm">
          {sessionState === "loading" ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Validating reset link…
            </div>
          ) : sessionState === "invalid" ? (
            <div className="space-y-4 text-center">
              <p className="text-sm font-medium">Reset link is invalid or expired.</p>
              <p className="text-xs text-muted-foreground">
                Reset links expire after one hour and can only be used once.
                Request a new one to continue.
              </p>
              <Button asChild className="w-full">
                <Link href="/forgot-password">Request a new link</Link>
              </Button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="password">New password</Label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={8}
                  autoComplete="new-password"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="confirm-password">Confirm new password</Label>
                <Input
                  id="confirm-password"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                  minLength={8}
                  autoComplete="new-password"
                />
                <p className="text-xs text-muted-foreground">
                  Use at least 8 characters. Avoid passwords you&apos;ve used
                  elsewhere.
                </p>
              </div>

              {errorMsg ? (
                <p className="text-sm text-destructive">{errorMsg}</p>
              ) : null}

              <Button type="submit" disabled={loading} className="w-full">
                {loading ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <ShieldCheck className="h-4 w-4" />
                )}
                {loading ? "Saving…" : "Update password"}
              </Button>
            </form>
          )}
        </div>
      </div>
    </main>
  );
}
