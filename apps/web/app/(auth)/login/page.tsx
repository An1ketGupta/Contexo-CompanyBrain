"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { networkError, parseApiError, reportApiError } from "@/lib/errors";

const OAUTH_ERROR_MESSAGES: Record<string, string> = {
  oauth_failed: "Sign-in failed. Please try again.",
  missing_code: "That sign-in link was invalid or expired. Try again.",
  exchange_failed: "We couldn't finish signing you in. Try again.",
};

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const inviteToken = searchParams.get("invite");
  const inviteEmail = searchParams.get("email");
  const [email, setEmail] = useState(inviteEmail ?? "");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const redirectedFrom = searchParams.get("redirectedFrom");

  // One-shot toasts for ?reset=success and ?error=…. We strip the query
  // params on first render so a refresh doesn't re-fire them.
  useEffect(() => {
    const reset = searchParams.get("reset");
    const errCode = searchParams.get("error");
    if (!reset && !errCode) return;

    if (reset === "success") {
      toast.success("Password updated. Please sign in.");
    }
    if (errCode) {
      toast.error(
        OAUTH_ERROR_MESSAGES[errCode] ?? "Sign-in failed. Please try again.",
      );
    }

    const url = new URL(window.location.href);
    url.searchParams.delete("reset");
    url.searchParams.delete("error");
    window.history.replaceState({}, "", url.toString());
  }, [searchParams]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);

    const supabase = createClient();
    const { error } = await supabase.auth.signInWithPassword({
      email,
      password,
    });

    if (error) {
      // Friendly messages for the two common cases. Fall back to provider text.
      const msg = error.message.toLowerCase();
      if (msg.includes("invalid login")) {
        toast.error("Wrong email or password.");
      } else if (msg.includes("email not confirmed")) {
        toast.error("Please confirm your email before signing in.");
      } else {
        toast.error(error.message);
      }
      setLoading(false);
      return;
    }

    // If a ?invite=<token> param is present, bind the now-authenticated
    // user to that workspace. Failing here shouldn't block sign-in (they
    // ARE signed in to their existing account); surface the error and
    // let them retry the invite link.
    if (inviteToken) {
      try {
        const acceptRes = await fetch(
          `/api/auth/invitations/${encodeURIComponent(inviteToken)}/accept-authenticated`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              display_name: email.split("@")[0] || "Member",
            }),
          },
        );
        if (acceptRes.ok) {
          await supabase.auth.refreshSession();
          toast.success("Joined your workspace.");
          router.push("/chat");
          router.refresh();
          return;
        }
        reportApiError(await parseApiError(acceptRes));
      } catch (err) {
        reportApiError(networkError(err));
      }
    }

    const target =
      redirectedFrom && redirectedFrom.startsWith("/") ? redirectedFrom : "/chat";
    router.push(target);
    router.refresh();
  }

  async function handleGoogle() {
    const supabase = createClient();
    // Carry the redirect target through the OAuth round-trip via the `next`
    // query param on /auth/callback — see auth/callback/route.ts.
    const next =
      redirectedFrom && redirectedFrom.startsWith("/") ? redirectedFrom : "/chat";
    const callbackUrl = new URL("/auth/callback", window.location.origin);
    callbackUrl.searchParams.set("next", next);

    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: callbackUrl.toString() },
    });
    if (error) toast.error(error.message);
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted p-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-bold text-foreground">Company Brain</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Sign in to your workspace
          </p>
        </div>

        <div className="rounded-lg border border-border bg-background p-8 shadow-sm">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
                placeholder="you@company.com"
              />
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="password">Password</Label>
                <Link
                  href="/forgot-password"
                  className="text-xs text-muted-foreground hover:text-foreground hover:underline"
                >
                  Forgot password?
                </Link>
              </div>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
            </div>

            <Button type="submit" disabled={loading} className="w-full">
              {loading && <Loader2 className="animate-spin" />}
              {loading ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <div className="my-5 flex items-center gap-3 text-xs text-muted-foreground">
            <div className="h-px flex-1 bg-border" />
            or
            <div className="h-px flex-1 bg-border" />
          </div>

          <Button
            type="button"
            variant="outline"
            onClick={handleGoogle}
            className="w-full"
          >
            <GoogleIcon className="h-4 w-4" />
            Continue with Google
          </Button>

          <p className="mt-6 text-center text-sm text-muted-foreground">
            Don&apos;t have an account?{" "}
            <Link
              href="/signup"
              className="font-medium text-primary hover:underline"
            >
              Sign up
            </Link>
          </p>
        </div>
      </div>
    </main>
  );
}

function GoogleIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M21.6 12.227c0-.709-.064-1.39-.182-2.045H12v3.868h5.382a4.6 4.6 0 0 1-1.995 3.018v2.51h3.232c1.89-1.741 2.98-4.305 2.98-7.351z"
      />
      <path
        fill="#34A853"
        d="M12 22c2.7 0 4.964-.895 6.618-2.423l-3.232-2.509c-.895.6-2.04.955-3.386.955-2.605 0-4.81-1.759-5.596-4.123H3.064v2.59A9.996 9.996 0 0 0 12 22z"
      />
      <path
        fill="#FBBC05"
        d="M6.404 13.9A6.013 6.013 0 0 1 6.09 12c0-.659.114-1.3.314-1.9V7.51H3.064A9.996 9.996 0 0 0 2 12c0 1.614.386 3.14 1.064 4.49l3.34-2.59z"
      />
      <path
        fill="#EA4335"
        d="M12 5.977c1.468 0 2.786.505 3.823 1.496l2.868-2.868C16.96 2.992 14.695 2 12 2A9.996 9.996 0 0 0 3.064 7.51l3.34 2.59C7.19 7.736 9.395 5.977 12 5.977z"
      />
    </svg>
  );
}
