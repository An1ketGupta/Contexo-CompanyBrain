import type { Metadata } from "next";
import { Lock } from "lucide-react";
import { Logo } from "@/components/brand/logo";

export const metadata: Metadata = {
  title: "Review & sign",
  // Signing URLs carry their own credential in the path — never index them.
  robots: { index: false, follow: false },
};

// Signing gets its own chrome instead of the shared (public) layout. Two
// reasons: the embedded signer scrolls inside its iframe, so a page that also
// scrolls around it produces two competing scrollbars; and a recipient who
// arrived from an email link has no account, which makes the public header's
// "Sign in" call to action the wrong thing to put next to their signature.
//
// The wordmark is deliberately not a link — nothing here should navigate a
// half-signed document away.
export default function SignLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-muted/40">
      <header className="shrink-0 border-b border-border bg-background">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
          <Logo height={16} />
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <Lock className="h-3.5 w-3.5" />
            Secure signing
          </span>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col">{children}</main>
    </div>
  );
}
