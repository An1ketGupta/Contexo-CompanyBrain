"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  Brain,
  Briefcase,
  CalendarDays,
  Hash,
  CheckCircle2,
  ClipboardCheck,
  FileQuestion,
  FileText,
  Gauge,
  HeartPulse,
  HelpCircle,
  History,
  Inbox,
  LayoutTemplate,
  LineChart,
  ListChecks,
  MessageSquare,
  Phone,
  PieChart,
  Presentation,
  Settings,
  Mail,
  Megaphone,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Target,
  ThumbsDown,
  Users,
  Workflow,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useCurrentUser } from "@/hooks/use-user";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Admin-only items hide for member roles. */
  adminOnly?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/channels", label: "Channels", icon: Hash },
  { href: "/history", label: "History", icon: History },
  { href: "/chat/meeting-prep", label: "Meeting prep", icon: CalendarDays },
  { href: "/meetings", label: "Meetings", icon: CalendarDays },
  { href: "/settings/templates", label: "Templates", icon: LayoutTemplate },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/activity", label: "Team activity", icon: Users },
  { href: "/approvals", label: "Approvals", icon: CheckCircle2 },
  { href: "/sequences", label: "Sequences", icon: Mail },
  { href: "/sales/precall-brief", label: "Pre-call brief", icon: Phone },
  { href: "/sales/rfp", label: "RFP responses", icon: ClipboardCheck },
  { href: "/recruiting", label: "Recruiting", icon: Briefcase },
  { href: "/executive/briefing", label: "Exec briefing", icon: Presentation },
  { href: "/calendar", label: "Upcoming meetings", icon: CalendarDays },
  { href: "/action-items", label: "Action items", icon: ListChecks },
  { href: "/briefings", label: "Briefings", icon: Sparkles },
  { href: "/insights", label: "Insights", icon: LineChart, adminOnly: true },
  {
    href: "/admin/analytics",
    label: "Analytics",
    icon: BarChart3,
    adminOnly: true,
  },
  {
    href: "/admin/health",
    label: "KB health",
    icon: HeartPulse,
    adminOnly: true,
  },
  {
    href: "/admin/coverage",
    label: "Coverage",
    icon: PieChart,
    adminOnly: true,
  },
  {
    href: "/admin/moderation",
    label: "Moderation",
    icon: Shield,
    adminOnly: true,
  },
  {
    href: "/admin/knowledge-gaps",
    label: "Knowledge gaps",
    icon: FileQuestion,
    adminOnly: true,
  },
  {
    href: "/admin/knowledge-health",
    label: "Knowledge health",
    icon: Sparkles,
    adminOnly: true,
  },
  {
    href: "/admin/announcements",
    label: "Announcements",
    icon: Megaphone,
    adminOnly: true,
  },
  {
    href: "/admin/feedback",
    label: "Flagged feedback",
    icon: ThumbsDown,
    adminOnly: true,
  },
  {
    href: "/admin/confidence",
    label: "Confidence",
    icon: Gauge,
    adminOnly: true,
  },
  {
    href: "/admin/support",
    label: "Support inbox",
    icon: Inbox,
    adminOnly: true,
  },
  {
    href: "/admin/agent-runs",
    label: "Agent runs",
    icon: Activity,
    adminOnly: true,
  },
  {
    href: "/admin/compliance",
    label: "Compliance",
    icon: ShieldCheck,
    adminOnly: true,
  },
  {
    href: "/admin/certifications",
    label: "Certifications",
    icon: ClipboardCheck,
    adminOnly: true,
  },
  {
    href: "/admin/competitor-mentions",
    label: "Competitors",
    icon: ShieldAlert,
    adminOnly: true,
  },
  {
    href: "/admin/rate-limits",
    label: "Rate limits",
    icon: Gauge,
    adminOnly: true,
  },
  {
    href: "/admin/embeddings",
    label: "Embeddings",
    icon: Brain,
    adminOnly: true,
  },
  {
    href: "/admin/quality",
    label: "Output quality",
    icon: Target,
    adminOnly: true,
  },
  {
    href: "/admin/autoflows",
    label: "Autoflows",
    icon: Workflow,
    adminOnly: true,
  },
  {
    href: "/admin/personas",
    label: "Shared personas",
    icon: Users,
    adminOnly: true,
  },
  {
    href: "/admin/knowledge-graph",
    label: "Knowledge map",
    icon: Brain,
    adminOnly: true,
  },
  { href: "/settings", label: "Settings", icon: Settings },
  { href: "/help", label: "Help", icon: HelpCircle },
];

interface SidebarNavProps {
  onNavigate?: () => void;
}

export function SidebarNav({ onNavigate }: SidebarNavProps) {
  const pathname = usePathname();
  const { user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  return (
    <nav className="flex flex-col gap-1 px-3 py-2">
      {NAV_ITEMS.map((item) => {
        if (item.adminOnly && !isAdmin) return null;
        // Exact match OR prefix match — but only if no other sibling item is
        // a *more* specific prefix of the current path. Without this, /chat
        // lights up while the user is on /chat/meeting-prep.
        const moreSpecificMatch = NAV_ITEMS.some(
          (other) =>
            other !== item &&
            other.href !== item.href &&
            other.href.startsWith(`${item.href}/`) &&
            (pathname === other.href ||
              pathname.startsWith(`${other.href}/`)),
        );
        const isActive =
          !moreSpecificMatch &&
          (pathname === item.href || pathname.startsWith(`${item.href}/`));
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              isActive
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
