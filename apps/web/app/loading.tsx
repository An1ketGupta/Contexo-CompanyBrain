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
          <span className="relative flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.25"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-5 w-5"
              aria-hidden
            >
              <path d="M12 2a5 5 0 0 0-5 5v1.5A4 4 0 0 0 4 12a4 4 0 0 0 1.5 3.12A3.5 3.5 0 0 0 9 21h6a3.5 3.5 0 0 0 3.5-5.88A4 4 0 0 0 20 12a4 4 0 0 0-3-3.5V7a5 5 0 0 0-5-5Z" />
              <path d="M9 13h6" />
              <path d="M12 13v5" />
            </svg>
          </span>
        </div>
        <div className="flex flex-col items-center gap-2.5">
          <p className="text-sm font-medium tracking-tight text-foreground">
            Company Brain
          </p>
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
