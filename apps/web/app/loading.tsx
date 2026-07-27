import { Logo, LogoMark } from "@/components/brand/logo";

/**
 * Root splash — the first thing the user sees before any route is resolved.
 * Branded mark + staggered dots. Kept dependency-free so it boots instantly.
 */
export default function RootLoading() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-5">
        <div className="relative flex h-14 w-14 items-center justify-center">
          <span
            className="absolute inset-0 rounded-2xl bg-primary/15 animate-ping"
            style={{ animationDuration: "1.8s" }}
          />
          <LogoMark height={34} alt="" className="relative" />
        </div>
        <div className="flex flex-col items-center gap-2.5">
          <Logo height={16} />
          <div className="flex items-center gap-1.5" aria-hidden>
            {[0, 1, 2].map((i) => (
              <span
                key={i}
                className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60"
                style={{
                  animation: "dot-bounce 1.3s ease-in-out infinite",
                  animationDelay: `${i * 0.16}s`,
                }}
              />
            ))}
          </div>
        </div>
        <span className="sr-only">Loading workspace</span>
      </div>
    </div>
  );
}
