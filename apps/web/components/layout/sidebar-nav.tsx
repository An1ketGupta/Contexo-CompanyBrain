"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  Briefcase,
  CalendarDays,
  Hash,
  CheckCircle2,
  FileSignature,
  FileText,
  Gauge,
  HeartPulse,
  HelpCircle,
  History,
  Inbox,
  LayoutTemplate,
  ListChecks,
  MessageSquare,
  Settings,
  Megaphone,
  Send,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Target,
  ThumbsDown,
  Users,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useCurrentUser } from "@/hooks/use-user";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  adminOnly?: boolean;
}

interface NavSection {
  /** undefined = no header (top-level items above the first section). */
  label?: string;
  adminOnly?: boolean;
  items: NavItem[];
}

const NAV_SECTIONS: NavSection[] = [
  {
    items: [
      { href: "/chat", label: "Chat", icon: MessageSquare },
      { href: "/channels", label: "Channels", icon: Hash },
      { href: "/history", label: "History", icon: History },
    ],
  },
  {
    label: "Workspace",
    items: [
      { href: "/documents", label: "Documents", icon: FileText },
      { href: "/settings/templates", label: "Templates", icon: LayoutTemplate },
      { href: "/activity", label: "Team activity", icon: Users },
      { href: "/approvals", label: "Approvals", icon: CheckCircle2 },
    ],
  },
  {
    label: "Meetings",
    items: [
      { href: "/meetings", label: "Upcoming", icon: CalendarDays },
      { href: "/meetings/transcripts", label: "Transcript", icon: FileText },
      { href: "/meetings/prep", label: "Preparation", icon: Sparkles },
      { href: "/action-items", label: "Action items", icon: ListChecks },
      { href: "/briefings", label: "Briefings", icon: Sparkles },
    ],
  },
  {
    label: "Talent & Exec",
    items: [
      { href: "/recruiting", label: "Recruiting", icon: Briefcase },
      { href: "/onboarding", label: "Onboarding", icon: ShieldCheck },
      {
        href: "/document-templates",
        label: "Document templates",
        icon: FileSignature,
      },
    ],
  },
  {
    label: "Insights",
    adminOnly: true,
    items: [
      { href: "/admin/analytics", label: "Analytics", icon: BarChart3, adminOnly: true },
      { href: "/admin/recruiting", label: "Recruiting", icon: Briefcase, adminOnly: true },
      { href: "/admin/health", label: "KB health", icon: HeartPulse, adminOnly: true },
      { href: "/admin/quality", label: "Output quality", icon: Target, adminOnly: true },
    ],
  },
  {
    label: "Admin",
    adminOnly: true,
    items: [
      { href: "/admin/support", label: "Support", icon: Inbox, adminOnly: true },
      { href: "/admin/sales", label: "Sales", icon: Send, adminOnly: true },
      { href: "/admin/moderation", label: "Moderation", icon: Shield, adminOnly: true },
      { href: "/admin/announcements", label: "Announcements", icon: Megaphone, adminOnly: true },
      { href: "/admin/feedback", label: "Flagged feedback", icon: ThumbsDown, adminOnly: true },
      { href: "/admin/confidence", label: "Confidence", icon: Gauge, adminOnly: true },
      { href: "/admin/agent-runs", label: "Agent runs", icon: Activity, adminOnly: true },
      { href: "/admin/compliance", label: "Compliance", icon: ShieldCheck, adminOnly: true },
      { href: "/admin/competitor-mentions", label: "Competitors", icon: ShieldAlert, adminOnly: true },
      { href: "/admin/rate-limits", label: "Rate limits", icon: Gauge, adminOnly: true },
      { href: "/admin/personas", label: "Shared personas", icon: Users, adminOnly: true },
    ],
  },
  {
    items: [
      { href: "/settings", label: "Settings", icon: Settings },
      { href: "/help", label: "Help", icon: HelpCircle },
    ],
  },
];

const ALL_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);

interface SidebarNavProps {
  onNavigate?: () => void;
}

export function SidebarNav({ onNavigate }: SidebarNavProps) {
  const pathname = usePathname();
  const { user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  return (
    <nav className="flex flex-col gap-3 px-3 py-1">
      {NAV_SECTIONS.map((section, sectionIdx) => {
        if (section.adminOnly && !isAdmin) return null;
        const visibleItems = section.items.filter(
          (it) => !it.adminOnly || isAdmin,
        );
        if (visibleItems.length === 0) return null;

        return (
          <div key={sectionIdx} className="flex flex-col gap-0.5">
            {section.label && (
              <div className="px-3 pb-1 pt-1 font-mono text-[10px] font-bold uppercase tracking-[0.06em] text-muted-foreground/70">
                {section.label}
              </div>
            )}
            {visibleItems.map((item) => {
              // Prevent /chat from lighting up while on /chat/meeting-prep, etc.
              const moreSpecificMatch = ALL_ITEMS.some(
                (other) =>
                  other.href !== item.href &&
                  other.href.startsWith(`${item.href}/`) &&
                  (pathname === other.href ||
                    pathname.startsWith(`${other.href}/`)),
              );
              const isActive =
                !moreSpecificMatch &&
                (pathname === item.href ||
                  pathname.startsWith(`${item.href}/`));
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onNavigate}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-accent font-semibold text-accent-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  <Icon className="h-4 w-6 shrink-0" />
                  {item.label}
                </Link>
              );
            })}
          </div>
        );
      })}
    </nav>
  );
}
