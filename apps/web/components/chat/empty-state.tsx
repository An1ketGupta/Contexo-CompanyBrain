"use client";

import { Briefcase, ClipboardList, FileText, HelpCircle, Mail, Megaphone } from "lucide-react";
import Link from "next/link";

interface EmptyStateProps {
  onPick: (text: string) => void;
  hasDocuments: boolean;
}

interface Suggestion {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  prompt: string;
}

const SUGGESTIONS: Suggestion[] = [
  {
    icon: Mail,
    title: "Write a client email",
    prompt:
      "Write an email to a new client introducing our services and pricing. Match our usual tone.",
  },
  {
    icon: Briefcase,
    title: "Draft a job description",
    prompt:
      "Draft a job description for a senior backend engineer. Use our company voice and include the standard sections.",
  },
  {
    icon: Megaphone,
    title: "Announce a policy on Slack",
    prompt:
      "Draft a Slack announcement to the whole company explaining our new leave policy. Keep it warm and brief.",
  },
  {
    icon: HelpCircle,
    title: "Answer a policy question",
    prompt: "What is our process for handling customer complaints, step by step?",
  },
  {
    icon: ClipboardList,
    title: "Summarize onboarding",
    prompt:
      "Summarize everything a new hire needs to know during their first week, organized by day.",
  },
  {
    icon: FileText,
    title: "Synthesize a report",
    prompt:
      "Pull together our most recent quarterly priorities and what's expected from each team to hit them.",
  },
];

export function EmptyState({ onPick, hasDocuments }: EmptyStateProps) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col items-center px-6 py-12 text-center">
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Mail className="h-5 w-5" />
      </div>
      <h1 className="text-xl font-semibold tracking-tight text-foreground">
        What should we get done today?
      </h1>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        Describe any task — an email, a job description, a Slack announcement,
        a policy question. The AI will pull from your company docs.
      </p>

      {!hasDocuments && (
        <div className="mt-5 rounded-lg border border-amber-300/40 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          The brain has no knowledge yet.{" "}
          <Link
            href="/documents"
            className="font-medium underline underline-offset-2"
          >
            Upload your first document
          </Link>{" "}
          so it can give grounded answers.
        </div>
      )}

      <div className="mt-8 grid w-full grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {SUGGESTIONS.map((s) => {
          const Icon = s.icon;
          return (
            <button
              key={s.title}
              type="button"
              onClick={() => onPick(s.prompt)}
              className="flex flex-col items-start gap-1.5 rounded-lg border border-border bg-background p-3 text-left transition-colors hover:border-primary/40 hover:bg-muted/40"
            >
              <Icon className="h-4 w-4 text-primary" />
              <span className="text-sm font-medium text-foreground">{s.title}</span>
              <span className="line-clamp-2 text-xs text-muted-foreground">
                {s.prompt}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
