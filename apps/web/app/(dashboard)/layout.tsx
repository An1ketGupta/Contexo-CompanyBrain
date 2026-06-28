import { Sidebar } from "@/components/layout/sidebar";
import { MobileHeader } from "@/components/layout/mobile-header";
import { GlobalDocumentToaster } from "@/components/layout/global-document-toaster";
import { CommandPalette } from "@/components/command-palette/command-palette";
import { CommandPaletteProvider } from "@/components/command-palette/command-palette-context";
import { ShortcutsPanel } from "@/components/ui/shortcuts-panel";
import { ShortcutsPanelProvider } from "@/components/ui/shortcuts-panel-context";
import { EnrichmentModal } from "@/components/onboarding/enrichment-modal";
import { AcknowledgementBanner } from "@/components/compliance/acknowledgement-banner";
import { UploadProvider } from "@/components/documents/upload-context";
import { UploadDialogHost } from "@/components/documents/upload-dialog";
import { UploadWidget } from "@/components/documents/upload-widget";
import { CrispProvider } from "@/components/support/crisp-provider";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ShortcutsPanelProvider>
      <CommandPaletteProvider>
        <UploadProvider>
          {/* Pinned to the viewport with `fixed inset-0` so the body can never
              grow taller than the screen — that's what was producing a second
              body-level scrollbar next to the inner content scroll. The inner
              `flex-1 overflow-y-auto` div is the only scroll container on the
              page. `h-dvh` is kept as a fallback for browsers/devtools that
              measure `inset-0` against `vh` instead of `dvh`. */}
          <div className="fixed inset-0 flex h-dvh flex-col overflow-hidden md:flex-row">
            <GlobalDocumentToaster />
            <Sidebar />
            <MobileHeader />
            <div className="flex-1 overflow-y-auto bg-muted/30 [overflow-anchor:none]">
              <AcknowledgementBanner />
              {children}
            </div>
          </div>
          <CommandPalette />
          <ShortcutsPanel />
          <EnrichmentModal />
          <UploadDialogHost />
          <UploadWidget />
          <CrispProvider />
        </UploadProvider>
      </CommandPaletteProvider>
    </ShortcutsPanelProvider>
  );
}
