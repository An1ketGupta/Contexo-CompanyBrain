import { Sidebar } from "@/components/layout/sidebar";
import { MobileHeader } from "@/components/layout/mobile-header";
import { GlobalDocumentToaster } from "@/components/layout/global-document-toaster";
import { CommandPalette } from "@/components/command-palette/command-palette";
import { CommandPaletteProvider } from "@/components/command-palette/command-palette-context";
import { ShortcutsPanel } from "@/components/ui/shortcuts-panel";
import { ShortcutsPanelProvider } from "@/components/ui/shortcuts-panel-context";
import { EnrichmentModal } from "@/components/onboarding/enrichment-modal";
import { AcknowledgementBanner } from "@/components/compliance/acknowledgement-banner";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ShortcutsPanelProvider>
      <CommandPaletteProvider>
        <div className="flex h-screen flex-col md:flex-row">
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
      </CommandPaletteProvider>
    </ShortcutsPanelProvider>
  );
}
