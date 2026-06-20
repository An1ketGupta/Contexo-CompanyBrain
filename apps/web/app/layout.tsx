import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { ThemeProvider } from "next-themes";
import { Toaster } from "sonner";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
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
    { media: "(prefers-color-scheme: dark)", color: "#0b1120" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} h-full antialiased`}
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
