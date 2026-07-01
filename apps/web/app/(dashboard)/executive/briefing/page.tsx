"use client";

import { useState } from "react";

import { ExternalLink, Loader2, Presentation, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/actual/kit";

interface Sections {
  executive_summary: string;
  market_context: string;
  competitive_advantages: string;
  risks: string;
  recommendations: string;
}
interface BriefRead {
  id: string;
  google_doc_url: string | null;
  sections: Sections;
  status: string;
  error_message: string | null;
}

export default function ExecBriefingPage() {
  const [request, setRequest] = useState("");
  const [recipients, setRecipients] = useState("");
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<BriefRead | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    setResult(null);
    setGenerating(true);
    try {
      const rcps = recipients
        .split(/[,;\s]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      const res = await fetch("/api/executive/briefing/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_text: request,
          recipients: rcps,
          schedule_followup: false,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      setResult(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-8 p-6 md:p-8">
      <PageHeader
        eyebrow="Talent & Exec"
        title="Executive briefing"
        description="Synthesize a structured briefing across your KB, drop it into a Google Doc, and email the link to recipients."
      />

      <section className="rounded-2xl border border-border bg-card p-6">
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="request">Request</Label>
            <Textarea
              id="request"
              rows={4}
              placeholder="e.g. Prepare a briefing for our board meeting on competitive positioning vs Acme and Beta."
              value={request}
              onChange={(e) => setRequest(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="recipients">Recipients (comma-separated emails)</Label>
            <Input
              id="recipients"
              value={recipients}
              onChange={(e) => setRecipients(e.target.value)}
              placeholder="alice@acme.com, bob@acme.com"
            />
          </div>

          {error && (
            <div className="rounded-xl border border-destructive/30 bg-destructive-soft p-3 text-sm font-medium text-destructive">
              {error}
            </div>
          )}

          <div className="flex justify-end">
            <Button onClick={submit} disabled={generating || request.trim().length < 10}>
              {generating ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Sparkles className="size-4" />
              )}
              {generating ? "Generating…" : "Generate briefing"}
            </Button>
          </div>
        </div>
      </section>

      {result && (
        <section className="space-y-5 rounded-2xl border border-border bg-card p-6">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-tint text-brand">
              <Presentation className="h-4 w-4" />
            </div>
            <h2 className="text-base font-extrabold tracking-tight">Briefing ready</h2>
          </div>
          {result.google_doc_url && (
            <a
              href={result.google_doc_url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center justify-between rounded-xl border border-brand/30 bg-brand-tint p-3 text-sm font-bold text-brand transition-colors hover:bg-brand/10"
            >
              Open Google Doc
              <ExternalLink className="h-4 w-4" />
            </a>
          )}
          <SectionBlock title="Executive summary" text={result.sections?.executive_summary} />
          <SectionBlock title="Market context" text={result.sections?.market_context} />
          <SectionBlock title="Competitive advantages" text={result.sections?.competitive_advantages} />
          <SectionBlock title="Risks" text={result.sections?.risks} />
          <SectionBlock title="Recommendations" text={result.sections?.recommendations} />
        </section>
      )}
    </div>
  );
}

function SectionBlock({ title, text }: { title: string; text?: string }) {
  if (!text) return null;
  return (
    <div>
      <h3 className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      <p className="mt-1.5 whitespace-pre-wrap text-sm leading-relaxed text-body">{text}</p>
    </div>
  );
}
