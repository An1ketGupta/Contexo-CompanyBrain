import { Sidebar } from "@/components/layout/sidebar";
import { MobileHeader } from "@/components/layout/mobile-header";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-screen flex-col md:flex-row">
      <Sidebar />
      <MobileHeader />
      <div className="flex-1 overflow-y-auto bg-muted/30">{children}</div>
    </div>
  );
}
