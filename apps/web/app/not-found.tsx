import Link from "next/link";
import { Compass } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * Catches `notFound()` calls anywhere we don't have a closer not-found.tsx,
 * and renders for any URL that doesn't match a route.
 *
 * Deliberately a Server Component — no `use client` — so this page can be
 * statically generated and served fast for crawlers and 404 sweepers.
 */
export default function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="max-w-md text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Compass className="h-5 w-5" />
        </div>
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          404
        </p>
        <h1 className="mt-1 text-xl font-semibold tracking-tight text-foreground">
          We couldn&apos;t find that page
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          The link might be stale, or the page was moved. Either way, you can
          head back to your dashboard from here.
        </p>
        <div className="mt-6 flex justify-center gap-2">
          <Button asChild variant="primary">
            <Link href="/chat">Go to dashboard</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/">Home</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
