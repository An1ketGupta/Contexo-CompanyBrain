"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface Objection {
  objection: string;
  response: string;
}
interface CaseStudy {
  title: string;
  takeaway: string;
}
interface Brief {
  prospect_name: string;
  company: string;
  talking_points: string[];
  objections: Objection[];
  case_studies: CaseStudy[];
  pricing_scenario: string[];
  sources: { document_id: string; document_name: string; similarity?: number }[];
}

export default function PrecallBriefPage() {
  const [prospectName, setProspectName] = useState("");
  const [company, setCompany] = useState("");
  const [notes, setNotes] = useState("");
  const [brief, setBrief] = useState<Brief | null>(null);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    setGenerating(true);
    setBrief(null);
    try {
      const res = await fetch("/api/sales/precall-brief", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prospect_name: prospectName,
          company,
          notes: notes || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      setBrief(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Pre-call brief</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Pull a one-page brief grounded in your KB for an upcoming prospect call.
        </p>
      </header>

      <section className="rounded border bg-white p-6">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="name">Prospect name</Label>
            <Input
              id="name"
              value={prospectName}
              onChange={(e) => setProspectName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="company">Company</Label>
            <Input
              id="company"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
            />
          </div>
        </div>
        <div className="mt-4 space-y-2">
          <Label htmlFor="notes">Notes for the model (optional)</Label>
          <Textarea
            id="notes"
            rows={3}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. They evaluated us 6 months ago against Competitor X; focus on pricing posture."
          />
        </div>

        {error && (
          <div className="mt-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="mt-4 flex justify-end">
          <Button
            onClick={submit}
            disabled={generating || !prospectName.trim() || !company.trim()}
          >
            {generating ? "Generating…" : "Generate brief"}
          </Button>
        </div>
      </section>

      {brief && (
        <section className="space-y-6">
          <Section title="Talking points" items={brief.talking_points} />
          <ObjectionSection objections={brief.objections} />
          <CaseStudySection caseStudies={brief.case_studies} />
          <Section title="Pricing scenario" items={brief.pricing_scenario} />

          {brief.sources.length > 0 && (
            <div className="rounded border bg-white p-4">
              <div className="text-xs font-medium text-muted-foreground">
                Based on {brief.sources.length} document
                {brief.sources.length === 1 ? "" : "s"}
              </div>
              <ul className="mt-2 space-y-1 text-sm">
                {brief.sources.map((s) => (
                  <li key={s.document_id} className="text-zinc-700">
                    {s.document_name}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function Section({ title, items }: { title: string; items: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="rounded border bg-white p-4">
      <h2 className="text-sm font-semibold">{title}</h2>
      <ul className="mt-2 space-y-2 text-sm leading-6">
        {items.map((it, i) => (
          <li key={i} className="flex gap-2">
            <span className="text-muted-foreground">•</span>
            <span>{it}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ObjectionSection({ objections }: { objections: Objection[] }) {
  if (!objections?.length) return null;
  return (
    <div className="rounded border bg-white p-4">
      <h2 className="text-sm font-semibold">Likely objections</h2>
      <ul className="mt-2 space-y-3 text-sm leading-6">
        {objections.map((o, i) => (
          <li key={i} className="border-l-2 border-zinc-300 pl-3">
            <div className="font-medium">{o.objection}</div>
            <div className="text-zinc-600">{o.response}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CaseStudySection({ caseStudies }: { caseStudies: CaseStudy[] }) {
  if (!caseStudies?.length) return null;
  return (
    <div className="rounded border bg-white p-4">
      <h2 className="text-sm font-semibold">Relevant case studies</h2>
      <ul className="mt-2 space-y-3 text-sm leading-6">
        {caseStudies.map((c, i) => (
          <li key={i}>
            <div className="font-medium">{c.title}</div>
            <div className="text-zinc-600">{c.takeaway}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}
