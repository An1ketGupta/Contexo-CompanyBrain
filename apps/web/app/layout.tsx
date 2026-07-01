import type { Metadata, Viewport } from "next";
import { Plus_Jakarta_Sans, JetBrains_Mono } from "next/font/google";
import { ThemeProvider } from "next-themes";
import { Toaster } from "sonner";
import { CookieConsent } from "@/components/cookie-consent";
import "./globals.css";

// Actual design system — Plus Jakarta Sans is the workhorse (heavy, tightly
// tracked weights for display; plain weights for body). JetBrains Mono labels
// tokens, data, and eyebrows via the Tailwind `font-mono` utility.
const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-jakarta",
  display: "swap",
  weight: ["400", "500", "600", "700", "800"],
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
  weight: ["400", "500", "700"],
});

export const metadata: Metadata = {
  title: {
    default: "Company Brain",
    template: "%s | Company Brain",
  },
  description:
    "Your company's AI-powered brain. Centralize all company knowledge and execute any work task with full context.",
};

// `viewport-fit=cover` lets layout extend under the iOS notch / home indicator
// so `env(safe-area-inset-*)` returns non-zero values. Without it, sticky
// bottom inputs sit awkwardly above the home indicator on iPhone.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#16181c" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${jakarta.variable} ${jetbrainsMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full bg-background text-foreground">
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          {children}
          <CookieConsent />
          <Toaster
            position="top-right"
            richColors
            closeButton
            theme="system"
            toastOptions={{
              classNames: {
                toast:
                  "!bg-background !text-foreground !border !border-border !shadow-md",
              },
            }}
          />
        </ThemeProvider>
      </body>
    </html>
  );
}
