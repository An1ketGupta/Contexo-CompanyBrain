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

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ShortcutsPanelProvider>
      <CommandPaletteProvider>
        <UploadProvider>
          {/* `h-dvh` (dynamic viewport height) handles the iOS Safari
              address-bar collapse that `h-screen` (= 100vh) can't. The
              `min-h-` variant is a fallback for the rare browser without
              dvh support — flexbox children still get the correct height. */}
          <div className="flex h-dvh min-h-dvh flex-col md:flex-row">
            <GlobalDocumentToaster />
            <Sidebar />
            <MobileHeader />
            <div className="flex-1 overflow-y-auto bg-muted/30">
              <AcknowledgementBanner />
              {children}
            </div>
          </div>
          <CommandPalette />
          <ShortcutsPanel />
          <EnrichmentModal />
          <UploadDialogHost />
          <UploadWidget />
        </UploadProvider>
      </CommandPaletteProvider>
    </ShortcutsPanelProvider>
  );
}
