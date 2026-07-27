import { cn } from "@/lib/utils";

// Both brand assets are derived from public/logo.png — flat two-tone art, so
// neither can inherit `currentColor` the way the lucide marks they replace did.
// Each therefore ships twice, in dark ink and in light ink, and CSS picks one.
//
// The swap is CSS-only on purpose: both <img> tags render identically on the
// server and only their visibility differs, so nothing here depends on the
// resolved next-themes value and there is no hydration mismatch or first-paint
// flash. `tone` opts out for surfaces whose background is fixed regardless of
// theme — the print sheet, the pre-Tailwind global error boundary, a coloured
// plate.

type Tone = "auto" | "ink" | "inverse";

type Asset = { light: string; dark: string; ratio: number };

const WORDMARK: Asset = {
  light: "/logo-wordmark.png",
  dark: "/logo-wordmark-dark.png",
  ratio: 1200 / 236,
};

const MARK: Asset = {
  light: "/logo-mark.png",
  dark: "/logo-mark-dark.png",
  ratio: 1,
};

type BrandProps = {
  /** Rendered height in px. Width follows the asset's aspect ratio. */
  height?: number;
  tone?: Tone;
  className?: string;
  /** Pass "" when adjacent text already names the product. */
  alt?: string;
};

function Brand({
  asset,
  height,
  tone = "auto",
  className,
  alt = "Contexo",
}: BrandProps & { asset: Asset; height: number }) {
  const width = Math.round(height * asset.ratio);
  const shared = cn("block w-auto select-none", className);

  if (tone !== "auto") {
    return (
      <img
        src={tone === "inverse" ? asset.dark : asset.light}
        alt={alt}
        width={width}
        height={height}
        style={{ height }}
        className={shared}
        draggable={false}
      />
    );
  }

  return (
    <>
      <img
        src={asset.light}
        alt={alt}
        width={width}
        height={height}
        style={{ height }}
        className={cn(shared, "dark:hidden")}
        draggable={false}
      />
      <img
        src={asset.dark}
        alt={alt}
        // The light-ink copy is a duplicate of the same wordmark; screen
        // readers should not announce the brand twice.
        aria-hidden={alt !== ""}
        width={width}
        height={height}
        style={{ height }}
        className={cn(shared, "hidden dark:block")}
        draggable={false}
      />
    </>
  );
}

/** Full "Contexo" wordmark. Default height suits a 56px app bar. */
export function Logo({ height = 18, ...rest }: BrandProps) {
  return <Brand asset={WORDMARK} height={height} {...rest} />;
}

/** Square "C" mark, for slots too narrow for the wordmark. */
export function LogoMark({ height = 24, ...rest }: BrandProps) {
  return <Brand asset={MARK} height={height} {...rest} />;
}
