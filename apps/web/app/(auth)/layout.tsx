import Link from "next/link";

// Wraps every page in the (auth) route group with a small legal-links
// footer. Each auth page already renders its own centred form within
// the page body — this layout adds a footer beneath the form region
// without disrupting the existing visual hierarchy.
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-dvh flex-col">
      <div className="flex flex-1 flex-col">{children}</div>
      <footer className="border-t border-border bg-background">
        <div className="mx-auto flex max-w-md items-center justify-center gap-4 px-4 py-3 text-[11px] text-muted-foreground">
          <Link
            href="/privacy"
            className="transition-colors hover:text-foreground"
          >
            Privacy
          </Link>
          <span aria-hidden>·</span>
          <Link
            href="/terms"
            className="transition-colors hover:text-foreground"
          >
            Terms
          </Link>
          <span aria-hidden>·</span>
          <a
            href="mailto:support@nirnayaiq.com"
            className="transition-colors hover:text-foreground"
          >
            Support
          </a>
        </div>
      </footer>
    </div>
  );
}
