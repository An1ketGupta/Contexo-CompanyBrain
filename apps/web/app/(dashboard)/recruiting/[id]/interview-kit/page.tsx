"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import {
  ArrowLeft,
  CheckCircle2,
  Clock,
  Edit2,
  Loader2,
  Plus,
  Sparkles,
  Target,
  Trash2,
  Users,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  PageHeader,
  StatusPill as KitStatusPill,
  type PillTone,
} from "@/components/actual/kit";

// ── Types ───────────────────────────────────────────────────────────────────

type CompetencyKind = "technical" | "behavioral" | "values" | "cultural";

interface Competency {
  name: string;
  kind: CompetencyKind;
  description: string;
  weight: number;
}

interface PanelQuestion {
  question: string;
  competency: string;
  kind: CompetencyKind;
  follow_ups: string[];
  what_good_looks_like: string;
}

interface InterviewPanel {
  stage: string;
  panelist_role: string;
  duration_minutes: number;
  focus: string;
  questions: PanelQuestion[];
}

interface RubricLevel {
  score: number;
  label: string;
  anchor: string;
  behavioral_indicators: string[];
}

interface CompetencyRubric {
  competency: string;
  levels: RubricLevel[];
}

interface ReferenceQuestion {
  question: string;
  competency: string;
  why: string;
}

interface ReferenceBank {
  relationship: "manager" | "peer" | "report" | "cross_functional";
  intro: string;
  questions: ReferenceQuestion[];
}

interface InterviewKit {
  id: string;
  org_id: string;
  requisition_id: string;
  created_by: string;
  run_id: string | null;
  jd_variant_index: number | null;
  role_title: string | null;
  seniority_level: string | null;
  competencies: Competency[];
  panels: InterviewPanel[];
  scorecard: CompetencyRubric[];
  reference_questions: ReferenceBank[];
  sources: Array<{ document_id: string; document_name: string; similarity?: number }>;
  status: "draft" | "generating" | "ready" | "published" | "failed";
  error_message: string | null;
  generated_at: string | null;
  published_at: string | null;
  created_at: string;
  updated_at: string;
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

// ── Page ────────────────────────────────────────────────────────────────────

export default function InterviewKitPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const requisitionId = params?.id;

  const { data: kits, mutate: mutateKits, isLoading } = useSWR<InterviewKit[]>(
    requisitionId ? `/api/recruiting/requisitions/${requisitionId}/interview-kits` : null,
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 4000 },
  );

  // Show the most recent kit by default; surface older ones in a switcher.
  const activeKit = useMemo(() => {
    if (!kits?.length) return null;
    return kits[0];
  }, [kits]);

  const [generating, setGenerating] = useState(false);

  const generate = async () => {
    if (!requisitionId) return;
    setGenerating(true);
    try {
      const res = await fetch(
        `/api/recruiting/requisitions/${requisitionId}/interview-kits/generate`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
      );
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `Failed (${res.status})`);
      }
      toast.success("Generating interview kit — this takes ~30–60s.");
      await mutateKits();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to start generation.");
    } finally {
      setGenerating(false);
    }
  };

  if (isLoading) {
    return (
      <div className="container max-w-5xl mx-auto py-8 px-4">
        <Skeleton className="h-8 w-48 mb-4" />
        <Skeleton className="h-32 w-full mb-4" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  return (
    <div className="container mx-auto max-w-5xl space-y-8 px-4 py-8">
      <div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push(`/recruiting/${requisitionId}`)}
          className="mb-3 -ml-2"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to requisition
        </Button>
        <PageHeader
          eyebrow="Talent"
          title="Interview kit"
          description="AI-generated panels, rubric, and reference questions grounded in your company values."
          actions={
            activeKit && activeKit.status === "ready" ? (
              <Button onClick={generate} disabled={generating} variant="outline">
                {generating ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Sparkles className="size-4" />
                )}
                Regenerate
              </Button>
            ) : null
          }
        />
      </div>

      {!activeKit ? (
        <EmptyState onGenerate={generate} generating={generating} />
      ) : activeKit.status === "generating" ? (
        <GeneratingState kit={activeKit} />
      ) : activeKit.status === "failed" ? (
        <FailedState kit={activeKit} onRegenerate={generate} regenerating={generating} />
      ) : (
        <KitEditor kit={activeKit} onSaved={() => mutateKits()} />
      )}
    </div>
  );
}

// ── Empty / loading / failed states ─────────────────────────────────────────

function EmptyState({
  onGenerate,
  generating,
}: {
  onGenerate: () => void;
  generating: boolean;
}) {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-background p-12 text-center">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-brand-tint text-brand">
        <Sparkles className="h-5 w-5" />
      </div>
      <h2 className="mb-2 text-lg font-extrabold tracking-tight">Generate an interview kit</h2>
      <p className="text-sm text-muted-foreground mb-6 max-w-md mx-auto">
        The AI will extract competencies from the JD, ground them in your company values from the
        KB, and emit a structured panel loop, scorecard rubric, and reference question set.
      </p>
      <Button onClick={onGenerate} disabled={generating} size="lg">
        {generating ? (
          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
        ) : (
          <Sparkles className="w-4 h-4 mr-2" />
        )}
        Generate Interview Kit
      </Button>
    </div>
  );
}

function GeneratingState({ kit }: { kit: InterviewKit }) {
  return (
    <div className="rounded-2xl border border-border bg-card p-12 text-center">
      <Loader2 className="w-12 h-12 mx-auto text-brand animate-spin mb-4" />
      <h2 className="text-lg font-extrabold tracking-tight mb-2">Generating interview kit…</h2>
      <p className="text-sm text-muted-foreground max-w-md mx-auto">
        Extracting competencies → searching your knowledge base → drafting panels, rubric, and
        reference questions. Usually 30–60 seconds.
      </p>
      <p className="text-xs text-muted-foreground mt-6">
        Started {new Date(kit.created_at).toLocaleTimeString()}
      </p>
    </div>
  );
}

function FailedState({
  kit,
  onRegenerate,
  regenerating,
}: {
  kit: InterviewKit;
  onRegenerate: () => void;
  regenerating: boolean;
}) {
  return (
    <div className="rounded-2xl border border-destructive/30 bg-destructive-soft p-8">
      <h2 className="text-lg font-extrabold tracking-tight mb-2 text-destructive">Generation failed</h2>
      <p className="text-sm text-muted-foreground mb-4">
        {kit.error_message || "The agent could not finish generating the kit."}
      </p>
      <Button onClick={onRegenerate} disabled={regenerating} variant="outline">
        {regenerating ? (
          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
        ) : (
          <Sparkles className="w-4 h-4 mr-2" />
        )}
        Try again
      </Button>
    </div>
  );
}

// ── Editor ──────────────────────────────────────────────────────────────────

function KitEditor({ kit, onSaved }: { kit: InterviewKit; onSaved: () => void }) {
  const [competencies, setCompetencies] = useState<Competency[]>(kit.competencies);
  const [panels, setPanels] = useState<InterviewPanel[]>(kit.panels);
  const [scorecard, setScorecard] = useState<CompetencyRubric[]>(kit.scorecard);
  const [refs, setRefs] = useState<ReferenceBank[]>(kit.reference_questions);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);

  useEffect(() => {
    setCompetencies(kit.competencies);
    setPanels(kit.panels);
    setScorecard(kit.scorecard);
    setRefs(kit.reference_questions);
    setDirty(false);
  }, [kit.id, kit.updated_at]);

  const markDirty = () => setDirty(true);

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch(`/api/recruiting/interview-kits/${kit.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          competencies,
          panels,
          scorecard,
          reference_questions: refs,
        }),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || `Failed (${res.status})`);
      }
      toast.success("Saved.");
      setDirty(false);
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  const publish = async () => {
    if (dirty) {
      toast.error("Save your edits first.");
      return;
    }
    setPublishing(true);
    try {
      const res = await fetch(`/api/recruiting/interview-kits/${kit.id}/publish`, {
        method: "POST",
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || `Failed (${res.status})`);
      }
      toast.success("Interview kit published.");
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Publish failed.");
    } finally {
      setPublishing(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Header card */}
      <div className="rounded-2xl border border-border bg-card p-5">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-extrabold tracking-tight">
                {kit.role_title || "Interview Kit"}
              </h2>
              <StatusBadge status={kit.status} />
            </div>
            <p className="text-sm text-muted-foreground mt-1">
              Seniority: <span className="font-medium">{kit.seniority_level || "—"}</span>
              {kit.generated_at ? (
                <span className="ml-3">
                  Generated {new Date(kit.generated_at).toLocaleString()}
                </span>
              ) : null}
            </p>
          </div>
          <div className="flex gap-2">
            <Button onClick={save} disabled={!dirty || saving} variant="outline">
              {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
              Save changes
            </Button>
            <Button
              onClick={publish}
              disabled={publishing || dirty || kit.status === "published"}
            >
              {publishing ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <CheckCircle2 className="w-4 h-4 mr-2" />
              )}
              {kit.status === "published" ? "Published" : "Publish"}
            </Button>
          </div>
        </div>
        {kit.sources?.length ? (
          <div className="mt-4 pt-4 border-t">
            <p className="mb-2 font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
              Grounded in {kit.sources.length} knowledge-base{" "}
              {kit.sources.length === 1 ? "document" : "documents"}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {kit.sources.slice(0, 8).map((s) => (
                <span
                  key={s.document_id}
                  className="rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground"
                >
                  {s.document_name}
                </span>
              ))}
              {kit.sources.length > 8 ? (
                <span className="text-xs text-muted-foreground">
                  +{kit.sources.length - 8} more
                </span>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      {/* Competencies */}
      <Section icon={<Target className="w-5 h-5" />} title="Competencies">
        <div className="space-y-3">
          {competencies.map((c, i) => (
            <CompetencyRow
              key={i}
              competency={c}
              onChange={(next) => {
                setCompetencies((prev) => prev.map((x, idx) => (idx === i ? next : x)));
                markDirty();
              }}
              onRemove={() => {
                setCompetencies((prev) => prev.filter((_, idx) => idx !== i));
                markDirty();
              }}
            />
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setCompetencies((prev) => [
                ...prev,
                { name: "New competency", kind: "behavioral", description: "", weight: 0.1 },
              ]);
              markDirty();
            }}
          >
            <Plus className="w-4 h-4 mr-1" /> Add competency
          </Button>
        </div>
      </Section>

      {/* Panels */}
      <Section icon={<Users className="w-5 h-5" />} title="Interview Panels">
        <div className="space-y-4">
          {panels.map((p, i) => (
            <PanelCard
              key={i}
              panel={p}
              onChange={(next) => {
                setPanels((prev) => prev.map((x, idx) => (idx === i ? next : x)));
                markDirty();
              }}
              onRemove={() => {
                setPanels((prev) => prev.filter((_, idx) => idx !== i));
                markDirty();
              }}
            />
          ))}
        </div>
      </Section>

      {/* Scorecard */}
      <Section icon={<Edit2 className="w-5 h-5" />} title="Scorecard Rubric">
        <div className="space-y-4">
          {scorecard.map((row, i) => (
            <RubricCard
              key={i}
              row={row}
              onChange={(next) => {
                setScorecard((prev) => prev.map((x, idx) => (idx === i ? next : x)));
                markDirty();
              }}
            />
          ))}
        </div>
      </Section>

      {/* References */}
      <Section icon={<Users className="w-5 h-5" />} title="Reference-Check Questions">
        <div className="space-y-4">
          {refs.map((bank, i) => (
            <ReferenceCard
              key={i}
              bank={bank}
              onChange={(next) => {
                setRefs((prev) => prev.map((x, idx) => (idx === i ? next : x)));
                markDirty();
              }}
            />
          ))}
        </div>
      </Section>
    </div>
  );
}

// ── Small atoms ─────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: InterviewKit["status"] }) {
  const tone: Record<InterviewKit["status"], PillTone> = {
    draft: "gray",
    generating: "blue",
    ready: "amber",
    published: "green",
    failed: "red",
  };
  return <KitStatusPill tone={tone[status]}>{status}</KitStatusPill>;
}

function Section({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-brand">
        {icon}
        <h2 className="text-lg font-extrabold tracking-tight text-foreground">{title}</h2>
      </div>
      {children}
    </div>
  );
}

function CompetencyRow({
  competency,
  onChange,
  onRemove,
}: {
  competency: Competency;
  onChange: (c: Competency) => void;
  onRemove: () => void;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 space-y-2">
      <div className="flex items-center gap-2">
        <Input
          value={competency.name}
          onChange={(e) => onChange({ ...competency, name: e.target.value })}
          className="font-medium"
        />
        <select
          value={competency.kind}
          onChange={(e) =>
            onChange({ ...competency, kind: e.target.value as CompetencyKind })
          }
          className="h-9 rounded-lg border border-input bg-background px-2 text-sm"
        >
          <option value="technical">technical</option>
          <option value="behavioral">behavioral</option>
          <option value="values">values</option>
          <option value="cultural">cultural</option>
        </select>
        <Input
          type="number"
          step="0.05"
          min="0"
          max="1"
          value={competency.weight}
          onChange={(e) =>
            onChange({ ...competency, weight: parseFloat(e.target.value) || 0 })
          }
          className="w-20"
        />
        <Button variant="ghost" size="icon" onClick={onRemove}>
          <Trash2 className="w-4 h-4" />
        </Button>
      </div>
      <Textarea
        value={competency.description}
        onChange={(e) => onChange({ ...competency, description: e.target.value })}
        placeholder="What this competency means for this role"
        rows={2}
      />
    </div>
  );
}

function PanelCard({
  panel,
  onChange,
  onRemove,
}: {
  panel: InterviewPanel;
  onChange: (p: InterviewPanel) => void;
  onRemove: () => void;
}) {
  const updateQuestion = (qi: number, next: PanelQuestion) => {
    onChange({
      ...panel,
      questions: panel.questions.map((q, i) => (i === qi ? next : q)),
    });
  };
  const removeQuestion = (qi: number) => {
    onChange({ ...panel, questions: panel.questions.filter((_, i) => i !== qi) });
  };
  return (
    <div className="rounded-2xl border border-border bg-card">
      <div className="border-b p-4 flex items-center gap-3 flex-wrap">
        <div className="flex-1 min-w-[200px]">
          <Label className="text-xs text-muted-foreground">Stage</Label>
          <Input
            value={panel.stage}
            onChange={(e) => onChange({ ...panel, stage: e.target.value })}
            className="mt-1 font-semibold"
          />
        </div>
        <div className="flex-1 min-w-[200px]">
          <Label className="text-xs text-muted-foreground">Panelist role</Label>
          <Input
            value={panel.panelist_role}
            onChange={(e) => onChange({ ...panel, panelist_role: e.target.value })}
            className="mt-1"
          />
        </div>
        <div className="w-28">
          <Label className="text-xs text-muted-foreground">Duration (min)</Label>
          <Input
            type="number"
            value={panel.duration_minutes}
            onChange={(e) =>
              onChange({
                ...panel,
                duration_minutes: parseInt(e.target.value, 10) || 45,
              })
            }
            className="mt-1"
          />
        </div>
        <Button variant="ghost" size="icon" onClick={onRemove} className="self-end">
          <Trash2 className="w-4 h-4" />
        </Button>
      </div>
      <div className="p-4 space-y-3">
        <Textarea
          value={panel.focus}
          onChange={(e) => onChange({ ...panel, focus: e.target.value })}
          rows={2}
          placeholder="One-line focus statement"
        />
        <div className="space-y-3">
          {panel.questions.map((q, qi) => (
            <div key={qi} className="rounded-xl border border-border p-3 space-y-2 bg-muted/40">
              <div className="flex items-start gap-2">
                <Textarea
                  value={q.question}
                  onChange={(e) => updateQuestion(qi, { ...q, question: e.target.value })}
                  rows={2}
                  className="flex-1"
                />
                <Button variant="ghost" size="icon" onClick={() => removeQuestion(qi)}>
                  <X className="w-4 h-4" />
                </Button>
              </div>
              <div className="flex flex-wrap gap-2 items-center">
                <Input
                  value={q.competency}
                  onChange={(e) =>
                    updateQuestion(qi, { ...q, competency: e.target.value })
                  }
                  placeholder="Competency"
                  className="flex-1 min-w-[160px] h-8 text-xs"
                />
                <select
                  value={q.kind}
                  onChange={(e) =>
                    updateQuestion(qi, {
                      ...q,
                      kind: e.target.value as CompetencyKind,
                    })
                  }
                  className="h-8 rounded-lg border border-input bg-background px-2 text-xs"
                >
                  <option value="technical">technical</option>
                  <option value="behavioral">behavioral</option>
                  <option value="values">values</option>
                  <option value="cultural">cultural</option>
                </select>
              </div>
              {q.follow_ups?.length ? (
                <div>
                  <Label className="text-xs text-muted-foreground">Follow-ups</Label>
                  <ul className="list-disc list-inside text-xs mt-1 space-y-0.5">
                    {q.follow_ups.map((f, fi) => (
                      <li key={fi}>{f}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {q.what_good_looks_like ? (
                <p className="text-xs text-muted-foreground italic">
                  Good signal: {q.what_good_looks_like}
                </p>
              ) : null}
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              onChange({
                ...panel,
                questions: [
                  ...panel.questions,
                  {
                    question: "",
                    competency: "",
                    kind: "behavioral",
                    follow_ups: [],
                    what_good_looks_like: "",
                  },
                ],
              })
            }
          >
            <Plus className="w-4 h-4 mr-1" /> Add question
          </Button>
        </div>
      </div>
    </div>
  );
}

function RubricCard({
  row,
  onChange,
}: {
  row: CompetencyRubric;
  onChange: (r: CompetencyRubric) => void;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4">
      <Input
        value={row.competency}
        onChange={(e) => onChange({ ...row, competency: e.target.value })}
        className="font-semibold mb-3"
      />
      <div className="grid grid-cols-1 md:grid-cols-5 gap-2">
        {row.levels.map((lvl, li) => (
          <div
            key={li}
            className="rounded-xl border border-border p-3 text-xs space-y-1 bg-muted/40"
          >
            <div className="flex items-center justify-between">
              <span className="font-semibold">{lvl.score}</span>
              <Input
                value={lvl.label}
                onChange={(e) =>
                  onChange({
                    ...row,
                    levels: row.levels.map((x, i) =>
                      i === li ? { ...x, label: e.target.value } : x,
                    ),
                  })
                }
                className="h-7 text-xs w-32"
              />
            </div>
            <Textarea
              value={lvl.anchor}
              onChange={(e) =>
                onChange({
                  ...row,
                  levels: row.levels.map((x, i) =>
                    i === li ? { ...x, anchor: e.target.value } : x,
                  ),
                })
              }
              rows={3}
              className="text-xs"
            />
            {lvl.behavioral_indicators?.length ? (
              <ul className="list-disc list-inside text-[11px] text-muted-foreground space-y-0.5">
                {lvl.behavioral_indicators.map((b, bi) => (
                  <li key={bi}>{b}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function ReferenceCard({
  bank,
  onChange,
}: {
  bank: ReferenceBank;
  onChange: (b: ReferenceBank) => void;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="rounded-full bg-brand-tint px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-brand">
          {bank.relationship}
        </span>
      </div>
      <Textarea
        value={bank.intro}
        onChange={(e) => onChange({ ...bank, intro: e.target.value })}
        rows={2}
        placeholder="Intro for the caller"
        className="mb-3"
      />
      <div className="space-y-2">
        {bank.questions.map((q, qi) => (
          <div key={qi} className="rounded-xl border border-border p-2 text-xs space-y-1 bg-muted/40">
            <Textarea
              value={q.question}
              onChange={(e) =>
                onChange({
                  ...bank,
                  questions: bank.questions.map((x, i) =>
                    i === qi ? { ...x, question: e.target.value } : x,
                  ),
                })
              }
              rows={2}
              className="text-xs"
            />
            <div className="flex gap-2">
              <Input
                value={q.competency}
                onChange={(e) =>
                  onChange({
                    ...bank,
                    questions: bank.questions.map((x, i) =>
                      i === qi ? { ...x, competency: e.target.value } : x,
                    ),
                  })
                }
                placeholder="Competency"
                className="flex-1 h-7 text-xs"
              />
              <Input
                value={q.why}
                onChange={(e) =>
                  onChange({
                    ...bank,
                    questions: bank.questions.map((x, i) =>
                      i === qi ? { ...x, why: e.target.value } : x,
                    ),
                  })
                }
                placeholder="Why this question"
                className="flex-1 h-7 text-xs"
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
