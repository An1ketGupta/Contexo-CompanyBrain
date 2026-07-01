"use client";

import { useState } from "react";
import {
  BookOpen,
  DollarSign,
  FileText,
  Loader2,
  ShieldQuestion,
  Sparkles,
  Target,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/actual/kit";

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
    <div className="mx-auto max-w-4xl space-y-8 p-6 md:p-8">
      <PageHeader
        eyebrow="Sales"
        title="Pre-call brief"
        description="Pull a one-page brief grounded in your knowledge base for an upcoming prospect call — talking points, likely objections, and pricing posture in seconds."
      />

      <section className="rounded-2xl border border-border bg-card p-6">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="name">Prospect name</Label>
            <Input
              id="name"
              value={prospectName}
              onChange={(e) => setProspectName(e.target.value)}
              placeholder="e.g. Dana Ruiz"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="company">Company</Label>
            <Input
              id="company"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="e.g. Northwind Labs"
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
          <div className="mt-4 rounded-xl border border-destructive/40 bg-destructive-soft p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <div className="mt-5 flex justify-end">
          <Button
            onClick={submit}
            disabled={generating || !prospectName.trim() || !company.trim()}
          >
            {generating ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Sparkles className="size-4" />
            )}
            {generating ? "Generating…" : "Generate brief"}
          </Button>
        </div>
      </section>

      {generating && !brief ? (
        <div className="rounded-2xl border border-dashed border-border bg-background p-12 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-brand-tint text-brand">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
          <p className="mt-3 text-sm font-bold">Building your brief</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Searching your knowledge base and drafting talking points.
          </p>
        </div>
      ) : null}

      {brief && (
        <section className="space-y-4">
          <BulletCard
            icon={<Target className="size-4" />}
            title="Talking points"
            items={brief.talking_points}
          />
          <ObjectionCard objections={brief.objections} />
          <CaseStudyCard caseStudies={brief.case_studies} />
          <BulletCard
            icon={<DollarSign className="size-4" />}
            title="Pricing scenario"
            items={brief.pricing_scenario}
          />

          {brief.sources.length > 0 && (
            <div className="rounded-2xl border border-border bg-muted/40 p-5">
              <p className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                Based on {brief.sources.length} document
                {brief.sources.length === 1 ? "" : "s"}
              </p>
              <ul className="mt-3 flex flex-wrap gap-2">
                {brief.sources.map((s) => (
                  <li
                    key={s.document_id}
                    className="inline-flex items-center gap-1.5 rounded-full bg-card px-3 py-1 text-xs font-semibold text-body"
                  >
                    <FileText className="size-3 text-muted-foreground" />
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

function SectionHead({
  icon,
  title,
}: {
  icon: React.ReactNode;
  title: string;
}) {
  return (
    <div className="mb-3 flex items-center gap-2.5">
      <span className="flex size-7 items-center justify-center rounded-lg bg-brand-tint text-brand">
        {icon}
      </span>
      <h2 className="text-sm font-bold">{title}</h2>
    </div>
  );
}

function BulletCard({
  icon,
  title,
  items,
}: {
  icon: React.ReactNode;
  title: string;
  items: string[];
}) {
  if (!items?.length) return null;
  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <SectionHead icon={icon} title={title} />
      <ul className="space-y-2.5 text-sm leading-6 text-body">
        {items.map((it, i) => (
          <li key={i} className="flex gap-2.5">
            <span className="mt-2 size-1.5 shrink-0 rounded-full bg-brand" />
            <span>{it}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ObjectionCard({ objections }: { objections: Objection[] }) {
  if (!objections?.length) return null;
  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <SectionHead icon={<ShieldQuestion className="size-4" />} title="Likely objections" />
      <ul className="space-y-3 text-sm leading-6">
        {objections.map((o, i) => (
          <li
            key={i}
            className="rounded-xl border-l-2 border-brand bg-muted/40 py-2.5 pl-4 pr-3"
          >
            <div className="font-bold text-foreground">{o.objection}</div>
            <div className="mt-0.5 text-body">{o.response}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CaseStudyCard({ caseStudies }: { caseStudies: CaseStudy[] }) {
  if (!caseStudies?.length) return null;
  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <SectionHead icon={<BookOpen className="size-4" />} title="Relevant case studies" />
      <ul className="space-y-3 text-sm leading-6">
        {caseStudies.map((c, i) => (
          <li key={i}>
            <div className="font-bold text-foreground">{c.title}</div>
            <div className="mt-0.5 text-body">{c.takeaway}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}
