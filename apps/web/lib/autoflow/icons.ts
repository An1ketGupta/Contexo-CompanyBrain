import {
  Bell,
  CheckCircle2,
  Clock,
  FileCheck2,
  FileQuestion,
  FileText,
  FileX2,
  ListChecks,
  Mail,
  MessageSquare,
  SearchX,
  ShieldCheck,
  Sparkles,
  ThumbsDown,
  Upload,
  UserPlus,
  Webhook,
  Workflow,
  type LucideIcon,
} from "lucide-react";

const ICONS: Record<string, LucideIcon> = {
  Bell,
  CheckCircle2,
  Clock,
  FileCheck2,
  FileQuestion,
  FileText,
  FileX2,
  ListChecks,
  Mail,
  MessageSquare,
  SearchX,
  ShieldCheck,
  Sparkles,
  ThumbsDown,
  Upload,
  UserPlus,
  Webhook,
  Workflow,
};

export function getIcon(name: string): LucideIcon {
  return ICONS[name] ?? Workflow;
}
