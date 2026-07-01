import Link from "next/link";
import { Brain } from "lucide-react";

// Shared chrome for unauthenticated marketing-adjacent pages — Privacy,
// Terms, and (future) public docs. Deliberately separate from the
// dashboard layout so cookies / auth aren't required to render.
export default function PublicLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-4">
          <Link
            href="/"
            className="flex items-center gap-2 text-sm font-semibold tracking-tight"
          >
            <Brain className="h-5 w-5 text-primary" />
            Nirnaya IQ
          </Link>
          <nav className="flex items-center gap-4 text-xs text-muted-foreground">
            <Link
              href="/privacy"
              className="transition-colors hover:text-foreground"
            >
              Privacy
            </Link>
            <Link
              href="/terms"
              className="transition-colors hover:text-foreground"
            >
              Terms
            </Link>
            <Link
              href="/login"
              className="rounded-md border border-border px-3 py-1.5 text-foreground transition-colors hover:bg-muted"
            >
              Sign in
            </Link>
          </nav>
        </div>
      </header>

      <main className="flex-1">{children}</main>

      <footer className="border-t border-border bg-muted/30">
        <div className="mx-auto flex max-w-5xl flex-col gap-3 px-4 py-6 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
          <p>© {new Date().getFullYear()} NirnayaIQ. All rights reserved.</p>
          <nav className="flex items-center gap-4">
            <Link
              href="/privacy"
              className="transition-colors hover:text-foreground"
            >
              Privacy
            </Link>
            <Link
              href="/terms"
              className="transition-colors hover:text-foreground"
            >
              Terms
            </Link>
            <a
              href="mailto:privacy@nirnayaiq.com"
              className="transition-colors hover:text-foreground"
            >
              Contact
            </a>
          </nav>
        </div>
      </footer>
    </div>
  );
}
