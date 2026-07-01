"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { AlertCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { networkError, parseApiError, reportApiError } from "@/lib/errors";

interface InviteLookup {
  email: string;
  role: "admin" | "member";
  org: { id: string; name: string };
}

export default function AcceptInvitePage() {
  return (
    <Suspense
      fallback={
        <Shell>
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        </Shell>
      }
    >
      <AcceptInviteInner />
    </Suspense>
  );
}

function AcceptInviteInner() {
  const params = useSearchParams();
  const router = useRouter();
  const token = params.get("token") ?? "";

  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "valid"; invite: InviteLookup }
    | { kind: "invalid"; message: string }
  >({ kind: "loading" });

  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setState({ kind: "invalid", message: "No invite token in the URL." });
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/auth/invitations/${encodeURIComponent(token)}`,
        );
        const data = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok) {
          setState({
            kind: "invalid",
            message:
              data.detail ??
              data.message ??
              "This invite link is invalid or has expired.",
          });
          return;
        }
        setState({ kind: "valid", invite: data as InviteLookup });
      } catch {
        if (!cancelled) {
          setState({
            kind: "invalid",
            message: "Couldn't load this invite. Check the link and try again.",
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (state.kind === "loading") {
    return (
      <Shell>
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      </Shell>
    );
  }

  if (state.kind === "invalid") {
    return (
      <Shell>
        <div className="rounded-lg border border-destructive/30 bg-destructive-soft/60 p-4 text-sm">
          <div className="flex items-start gap-2 text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <p>{state.message}</p>
          </div>
        </div>
        <p className="text-center text-sm text-muted-foreground">
          Ask the workspace admin to send a fresh invite.
        </p>
        <p className="text-center text-sm">
          <Link
            href="/login"
            className="font-medium text-primary hover:underline"
          >
            Back to sign in
          </Link>
        </p>
      </Shell>
    );
  }

  const { invite } = state;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);

    // Ask the server to either (a) create the auth user + bind to org, or
    // (b) tell us this email already has an account so we should redirect
    // to /login. The server is the only place that can answer (b) safely.
    let acceptRes: Response;
    try {
      acceptRes = await fetch(
        `/api/auth/invitations/${encodeURIComponent(token)}/accept-credentials`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: displayName, password }),
        },
      );
    } catch (err) {
      reportApiError(networkError(err));
      setSubmitting(false);
      return;
    }

    if (!acceptRes.ok) {
      reportApiError(await parseApiError(acceptRes));
      setSubmitting(false);
      return;
    }

    const result = (await acceptRes.json()) as {
      requires_login?: boolean;
      email?: string;
    };

    if (result.requires_login) {
      toast.info("This email already has an account. Sign in to join.");
      const params = new URLSearchParams({ invite: token });
      if (result.email) params.set("email", result.email);
      router.replace(`/login?${params.toString()}`);
      return;
    }

    // Server already created the auth user. Sign in with the password the
    // user just chose to materialize a session, then refresh so the JWT
    // picks up app_metadata.org_id.
    const supabase = createClient();
    const { error: signInError } = await supabase.auth.signInWithPassword({
      email: invite.email,
      password,
    });
    if (signInError) {
      toast.error(
        "Joined workspace, but couldn't sign you in automatically. Please sign in.",
      );
      router.replace("/login");
      return;
    }
    await supabase.auth.refreshSession();
    toast.success(`Welcome to ${invite.org.name}.`);
    router.replace("/chat");
    router.refresh();
  }

  return (
    <Shell>
      <div className="text-center">
        <p className="text-sm text-muted-foreground">
          You&apos;re joining{" "}
          <span className="font-medium text-foreground">{invite.org.name}</span>{" "}
          as a {invite.role}.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" value={invite.email} readOnly disabled />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="displayName">Your name</Label>
          <Input
            id="displayName"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            required
            minLength={1}
            maxLength={80}
            autoComplete="name"
            placeholder="Riya Sharma"
            disabled={submitting}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            autoComplete="new-password"
            placeholder="Min 8 characters"
            disabled={submitting}
          />
        </div>

        <Button type="submit" disabled={submitting} className="w-full">
          {submitting && <Loader2 className="animate-spin" />}
          {submitting ? "Joining…" : "Accept invitation"}
        </Button>
      </form>

      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link
          href={`/login?invite=${encodeURIComponent(token)}`}
          className="font-medium text-primary hover:underline"
        >
          Sign in instead
        </Link>
      </p>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-muted p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-foreground">Nirnaya IQ</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Accept your invitation
          </p>
        </div>
        <div className="space-y-4 rounded-lg border border-border bg-background p-8 shadow-sm">
          {children}
        </div>
      </div>
    </main>
  );
}
