"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

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
    <div className="mx-auto max-w-4xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Executive briefing</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Synthesize a structured briefing across your KB, drop it into a
          Google Doc, and email the link to recipients.
        </p>
      </header>

      <section className="rounded border bg-white p-6">
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
            <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {error}
            </div>
          )}

          <div className="flex justify-end">
            <Button onClick={submit} disabled={generating || request.trim().length < 10}>
              {generating ? "Generating…" : "Generate briefing"}
            </Button>
          </div>
        </div>
      </section>

      {result && (
        <section className="space-y-3 rounded border bg-white p-6">
          <h2 className="text-sm font-semibold">Briefing ready</h2>
          {result.google_doc_url && (
            <a
              href={result.google_doc_url}
              target="_blank"
              rel="noreferrer"
              className="block rounded border border-blue-200 bg-blue-50 p-3 text-blue-700 hover:bg-blue-100"
            >
              Open Google Doc ↗
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
      <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </h3>
      <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{text}</p>
    </div>
  );
}
