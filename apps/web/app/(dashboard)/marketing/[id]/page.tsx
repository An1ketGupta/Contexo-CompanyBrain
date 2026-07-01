"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import {
  ArrowLeft,
  CheckCircle2,
  FileText,
  Layers,
  Loader2,
  Megaphone,
  Plus,
  Rocket,
  Sparkles,
  Swords,
  Target,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { StatusPill, type PillTone } from "@/components/actual/kit";

// ── Types (mirror apps/api/app/services/agents/marketing_agent/schemas.py) ──

type Channel = "blog" | "linkedin" | "x" | "email" | "landing" | "ads";
type Status = "draft" | "generating" | "ready" | "published" | "failed";

interface ValueProp {
  name: string;
  statement: string;
}

interface Positioning {
  audience: string;
  problem: string;
  category: string;
  differentiation: string;
  value_props: ValueProp[];
  taglines: string[];
}

interface MessagingPillar {
  name: string;
  statement: string;
  proof_points: string[];
  weight: number;
}

interface CompetitiveAngle {
  competitor: string;
  their_pitch: string;
  our_counter: string;
  win_themes: string[];
  gotchas: string[];
}

interface ChannelDraft {
  title: string;
  body: string;
  hook: string;
  length_hint: string;
}

interface ChannelPlanEntry {
  channel: Channel;
  lens: string;
  cta: string;
  timing: string;
  drafts: ChannelDraft[];
}

interface OutlineSection {
  heading: string;
  key_points: string[];
}

interface ContentBrief {
  working_title: string;
  target_length_words: number;
  target_keywords: string[];
  outline: OutlineSection[];
  internal_link_ideas: string[];
  distribution_notes: string;
}

interface MarketingBrief {
  id: string;
  objective: string;
  audience_hint: string | null;
  channels: Channel[];
  competitors: string[];
  positioning: Positioning;
  messaging_pillars: MessagingPillar[];
  competitive_angle: CompetitiveAngle[];
  channel_plan: ChannelPlanEntry[];
  content_brief: ContentBrief;
  sources: Array<{ document_id: string; document_name: string; similarity?: number }>;
  status: Status;
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

export default function MarketingBriefDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const briefId = params?.id;

  // Poll while generating; back off once the brief settles.
  const { data: brief, mutate, isLoading } = useSWR<MarketingBrief>(
    briefId ? `/api/marketing/briefs/${briefId}` : null,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: (data) => (data?.status === "generating" ? 3000 : 0),
    },
  );

  if (isLoading || !brief) {
    return (
      <div className="mx-auto max-w-5xl space-y-4 p-6 md:p-8">
        <Skeleton className="h-8 w-48 rounded-xl" />
        <Skeleton className="h-40 w-full rounded-2xl" />
        <Skeleton className="h-40 w-full rounded-2xl" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl p-6 md:p-8">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => router.push("/marketing")}
        className="mb-2"
      >
        <ArrowLeft className="w-4 h-4 mr-2" /> Back to briefs
      </Button>

      {brief.status === "generating" ? (
        <GeneratingState brief={brief} />
      ) : brief.status === "failed" ? (
        <FailedState brief={brief} />
      ) : (
        <BriefEditor brief={brief} onSaved={() => mutate()} />
      )}
    </div>
  );
}

function GeneratingState({ brief }: { brief: MarketingBrief }) {
  return (
    <div className="mt-4 rounded-2xl border border-border bg-muted/30 p-12 text-center">
      <Loader2 className="mx-auto mb-4 h-12 w-12 animate-spin text-brand" />
      <h2 className="text-lg font-semibold mb-2">Generating marketing brief…</h2>
      <p className="text-sm text-muted-foreground max-w-md mx-auto">
        Pulling positioning, brand voice, and competitor context from your KB → drafting
        positioning, pillars, channel drafts, and content brief in parallel.
      </p>
      <p className="text-xs text-muted-foreground mt-6">
        Started {new Date(brief.created_at).toLocaleTimeString()}
      </p>
    </div>
  );
}

function FailedState({ brief }: { brief: MarketingBrief }) {
  return (
    <div className="mt-4 rounded-2xl border border-destructive/40 bg-destructive-soft p-8">
      <h2 className="text-lg font-semibold mb-2 text-destructive">Generation failed</h2>
      <p className="text-sm text-muted-foreground mb-4">
        {brief.error_message || "The agent could not finish generating the brief."}
      </p>
    </div>
  );
}

// ── Editor ──────────────────────────────────────────────────────────────────

function BriefEditor({
  brief,
  onSaved,
}: {
  brief: MarketingBrief;
  onSaved: () => void;
}) {
  const [positioning, setPositioning] = useState<Positioning>(brief.positioning);
  const [pillars, setPillars] = useState<MessagingPillar[]>(brief.messaging_pillars);
  const [competitive, setCompetitive] = useState<CompetitiveAngle[]>(brief.competitive_angle);
  const [channelPlan, setChannelPlan] = useState<ChannelPlanEntry[]>(brief.channel_plan);
  const [contentBrief, setContentBrief] = useState<ContentBrief>(brief.content_brief);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);

  useEffect(() => {
    setPositioning(brief.positioning);
    setPillars(brief.messaging_pillars);
    setCompetitive(brief.competitive_angle);
    setChannelPlan(brief.channel_plan);
    setContentBrief(brief.content_brief);
    setDirty(false);
  }, [brief.id, brief.updated_at, brief.positioning, brief.messaging_pillars, brief.competitive_angle, brief.channel_plan, brief.content_brief]);

  const markDirty = () => setDirty(true);

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch(`/api/marketing/briefs/${brief.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          positioning,
          messaging_pillars: pillars,
          competitive_angle: competitive,
          channel_plan: channelPlan,
          content_brief: contentBrief,
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
      const res = await fetch(`/api/marketing/briefs/${brief.id}/publish`, {
        method: "POST",
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || `Failed (${res.status})`);
      }
      toast.success("Marketing brief published.");
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Publish failed.");
    } finally {
      setPublishing(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="rounded-2xl border border-border bg-card p-5">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2.5">
              <span className="flex size-9 items-center justify-center rounded-xl bg-brand-tint text-brand">
                <Rocket className="size-4" />
              </span>
              <h1 className="text-xl font-extrabold tracking-tight truncate">
                {positioning.category || "Marketing Brief"}
              </h1>
              <StatusBadge status={brief.status} />
            </div>
            <p className="text-sm text-muted-foreground mt-2 line-clamp-2">
              {brief.objective}
            </p>
            {brief.generated_at ? (
              <p className="text-xs text-muted-foreground mt-1">
                Generated {new Date(brief.generated_at).toLocaleString()}
              </p>
            ) : null}
          </div>
          <div className="flex gap-2">
            <Button onClick={save} disabled={!dirty || saving} variant="outline">
              {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
              Save
            </Button>
            <Button
              onClick={publish}
              disabled={publishing || dirty || brief.status === "published"}
            >
              {publishing ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <CheckCircle2 className="w-4 h-4 mr-2" />
              )}
              {brief.status === "published" ? "Published" : "Publish"}
            </Button>
          </div>
        </div>
        {brief.sources?.length ? (
          <div className="mt-4 pt-4 border-t">
            <p className="text-xs font-medium text-muted-foreground mb-2">
              Grounded in {brief.sources.length} knowledge-base{" "}
              {brief.sources.length === 1 ? "document" : "documents"}:
            </p>
            <div className="flex flex-wrap gap-1.5">
              {brief.sources.slice(0, 12).map((s) => (
                <span
                  key={s.document_id}
                  className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs font-semibold text-body"
                >
                  <FileText className="size-3 text-muted-foreground" />
                  {s.document_name}
                </span>
              ))}
              {brief.sources.length > 12 ? (
                <span className="text-xs text-muted-foreground">
                  +{brief.sources.length - 12} more
                </span>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      {/* Positioning */}
      <Section icon={<Target className="w-5 h-5" />} title="Positioning">
        <PositioningEditor
          value={positioning}
          onChange={(next) => {
            setPositioning(next);
            markDirty();
          }}
        />
      </Section>

      {/* Messaging Pillars */}
      <Section icon={<Layers className="w-5 h-5" />} title="Messaging pillars">
        <div className="space-y-3">
          {pillars.map((p, i) => (
            <PillarCard
              key={i}
              pillar={p}
              onChange={(next) => {
                setPillars((prev) => prev.map((x, idx) => (idx === i ? next : x)));
                markDirty();
              }}
              onRemove={() => {
                setPillars((prev) => prev.filter((_, idx) => idx !== i));
                markDirty();
              }}
            />
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setPillars((prev) => [
                ...prev,
                { name: "New pillar", statement: "", proof_points: [], weight: 0.1 },
              ]);
              markDirty();
            }}
          >
            <Plus className="w-4 h-4 mr-1" /> Add pillar
          </Button>
        </div>
      </Section>

      {/* Competitive */}
      {competitive.length > 0 ? (
        <Section icon={<Swords className="w-5 h-5" />} title="Competitive angle">
          <div className="space-y-3">
            {competitive.map((c, i) => (
              <CompetitiveCard
                key={i}
                row={c}
                onChange={(next) => {
                  setCompetitive((prev) => prev.map((x, idx) => (idx === i ? next : x)));
                  markDirty();
                }}
                onRemove={() => {
                  setCompetitive((prev) => prev.filter((_, idx) => idx !== i));
                  markDirty();
                }}
              />
            ))}
          </div>
        </Section>
      ) : null}

      {/* Channel Plan */}
      <Section icon={<Megaphone className="w-5 h-5" />} title="Channel plan">
        <div className="space-y-4">
          {channelPlan.map((c, i) => (
            <ChannelCard
              key={i}
              entry={c}
              onChange={(next) => {
                setChannelPlan((prev) => prev.map((x, idx) => (idx === i ? next : x)));
                markDirty();
              }}
            />
          ))}
        </div>
      </Section>

      {/* Content Brief */}
      <Section icon={<FileText className="w-5 h-5" />} title="Long-form content brief">
        <ContentBriefEditor
          value={contentBrief}
          onChange={(next) => {
            setContentBrief(next);
            markDirty();
          }}
        />
      </Section>
    </div>
  );
}

// ── Atoms ───────────────────────────────────────────────────────────────────

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
      <div className="flex items-center gap-2.5">
        <span className="flex size-8 items-center justify-center rounded-lg bg-brand-tint text-brand [&_svg]:size-4">
          {icon}
        </span>
        <h2 className="text-lg font-bold tracking-tight">{title}</h2>
      </div>
      {children}
    </div>
  );
}

const STATUS_TONE: Record<Status, PillTone> = {
  draft: "gray",
  generating: "blue",
  ready: "amber",
  published: "green",
  failed: "red",
};

function StatusBadge({ status }: { status: Status }) {
  return <StatusPill tone={STATUS_TONE[status]}>{status}</StatusPill>;
}

function PositioningEditor({
  value,
  onChange,
}: {
  value: Positioning;
  onChange: (v: Positioning) => void;
}) {
  return (
    <div className="rounded-2xl border border-border p-4 space-y-3 bg-card">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <Label className="text-xs text-muted-foreground">Audience</Label>
          <Input
            value={value.audience}
            onChange={(e) => onChange({ ...value, audience: e.target.value })}
            className="mt-1"
          />
        </div>
        <div>
          <Label className="text-xs text-muted-foreground">Category</Label>
          <Input
            value={value.category}
            onChange={(e) => onChange({ ...value, category: e.target.value })}
            className="mt-1"
          />
        </div>
      </div>
      <div>
        <Label className="text-xs text-muted-foreground">Problem</Label>
        <Textarea
          value={value.problem}
          onChange={(e) => onChange({ ...value, problem: e.target.value })}
          rows={2}
          className="mt-1"
        />
      </div>
      <div>
        <Label className="text-xs text-muted-foreground">Differentiation</Label>
        <Textarea
          value={value.differentiation}
          onChange={(e) => onChange({ ...value, differentiation: e.target.value })}
          rows={2}
          className="mt-1"
        />
      </div>
      <div>
        <Label className="text-xs text-muted-foreground">Value props</Label>
        <div className="space-y-2 mt-1">
          {value.value_props.map((vp, i) => (
            <div key={i} className="flex gap-2">
              <Input
                value={vp.name}
                onChange={(e) =>
                  onChange({
                    ...value,
                    value_props: value.value_props.map((x, idx) =>
                      idx === i ? { ...x, name: e.target.value } : x,
                    ),
                  })
                }
                placeholder="Name"
                className="w-48"
              />
              <Input
                value={vp.statement}
                onChange={(e) =>
                  onChange({
                    ...value,
                    value_props: value.value_props.map((x, idx) =>
                      idx === i ? { ...x, statement: e.target.value } : x,
                    ),
                  })
                }
                placeholder="Statement"
                className="flex-1"
              />
              <Button
                variant="ghost"
                size="icon"
                onClick={() =>
                  onChange({
                    ...value,
                    value_props: value.value_props.filter((_, idx) => idx !== i),
                  })
                }
              >
                <Trash2 className="w-4 h-4" />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              onChange({
                ...value,
                value_props: [...value.value_props, { name: "", statement: "" }],
              })
            }
          >
            <Plus className="w-4 h-4 mr-1" /> Add value prop
          </Button>
        </div>
      </div>
      <div>
        <Label className="text-xs text-muted-foreground">Taglines</Label>
        <div className="space-y-2 mt-1">
          {value.taglines.map((t, i) => (
            <div key={i} className="flex gap-2">
              <Input
                value={t}
                onChange={(e) =>
                  onChange({
                    ...value,
                    taglines: value.taglines.map((x, idx) =>
                      idx === i ? e.target.value : x,
                    ),
                  })
                }
                className="flex-1"
              />
              <Button
                variant="ghost"
                size="icon"
                onClick={() =>
                  onChange({
                    ...value,
                    taglines: value.taglines.filter((_, idx) => idx !== i),
                  })
                }
              >
                <Trash2 className="w-4 h-4" />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() => onChange({ ...value, taglines: [...value.taglines, ""] })}
          >
            <Plus className="w-4 h-4 mr-1" /> Add tagline
          </Button>
        </div>
      </div>
    </div>
  );
}

function PillarCard({
  pillar,
  onChange,
  onRemove,
}: {
  pillar: MessagingPillar;
  onChange: (p: MessagingPillar) => void;
  onRemove: () => void;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 space-y-2">
      <div className="flex items-center gap-2">
        <Input
          value={pillar.name}
          onChange={(e) => onChange({ ...pillar, name: e.target.value })}
          className="font-medium"
          placeholder="Pillar name"
        />
        <Input
          type="number"
          step="0.05"
          min="0"
          max="1"
          value={pillar.weight}
          onChange={(e) =>
            onChange({ ...pillar, weight: parseFloat(e.target.value) || 0 })
          }
          className="w-20"
        />
        <Button variant="ghost" size="icon" onClick={onRemove}>
          <Trash2 className="w-4 h-4" />
        </Button>
      </div>
      <Textarea
        value={pillar.statement}
        onChange={(e) => onChange({ ...pillar, statement: e.target.value })}
        placeholder="One-sentence statement the buyer would nod at"
        rows={2}
      />
      <div>
        <Label className="text-xs text-muted-foreground">Proof points</Label>
        <div className="space-y-1 mt-1">
          {pillar.proof_points.map((pp, i) => (
            <div key={i} className="flex gap-2">
              <Input
                value={pp}
                onChange={(e) =>
                  onChange({
                    ...pillar,
                    proof_points: pillar.proof_points.map((x, idx) =>
                      idx === i ? e.target.value : x,
                    ),
                  })
                }
                className="flex-1 text-xs"
              />
              <Button
                variant="ghost"
                size="icon"
                onClick={() =>
                  onChange({
                    ...pillar,
                    proof_points: pillar.proof_points.filter((_, idx) => idx !== i),
                  })
                }
              >
                <Trash2 className="w-4 h-4" />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              onChange({ ...pillar, proof_points: [...pillar.proof_points, ""] })
            }
          >
            <Plus className="w-4 h-4 mr-1" /> Add proof
          </Button>
        </div>
      </div>
    </div>
  );
}

function CompetitiveCard({
  row,
  onChange,
  onRemove,
}: {
  row: CompetitiveAngle;
  onChange: (r: CompetitiveAngle) => void;
  onRemove: () => void;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 space-y-2">
      <div className="flex items-center gap-2">
        <Input
          value={row.competitor}
          onChange={(e) => onChange({ ...row, competitor: e.target.value })}
          className="font-semibold"
        />
        <Button variant="ghost" size="icon" onClick={onRemove}>
          <Trash2 className="w-4 h-4" />
        </Button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <Label className="text-xs text-muted-foreground">Their pitch</Label>
          <Textarea
            value={row.their_pitch}
            onChange={(e) => onChange({ ...row, their_pitch: e.target.value })}
            rows={3}
            className="mt-1"
          />
        </div>
        <div>
          <Label className="text-xs text-muted-foreground">Our counter</Label>
          <Textarea
            value={row.our_counter}
            onChange={(e) => onChange({ ...row, our_counter: e.target.value })}
            rows={3}
            className="mt-1"
          />
        </div>
      </div>
      <StringList
        label="Win themes"
        items={row.win_themes}
        onChange={(next) => onChange({ ...row, win_themes: next })}
      />
      <StringList
        label="Gotchas (honest objections)"
        items={row.gotchas}
        onChange={(next) => onChange({ ...row, gotchas: next })}
      />
    </div>
  );
}

function ChannelCard({
  entry,
  onChange,
}: {
  entry: ChannelPlanEntry;
  onChange: (e: ChannelPlanEntry) => void;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card">
      <div className="border-b p-4 flex items-center gap-3 flex-wrap">
        <span className="rounded-full bg-brand-tint px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-brand">
          {entry.channel}
        </span>
        <div className="flex-1 min-w-[200px]">
          <Label className="text-xs text-muted-foreground">Lens</Label>
          <Input
            value={entry.lens}
            onChange={(e) => onChange({ ...entry, lens: e.target.value })}
            className="mt-1"
          />
        </div>
        <div className="w-32">
          <Label className="text-xs text-muted-foreground">CTA</Label>
          <Input
            value={entry.cta}
            onChange={(e) => onChange({ ...entry, cta: e.target.value })}
            className="mt-1"
          />
        </div>
        <div className="w-32">
          <Label className="text-xs text-muted-foreground">Timing</Label>
          <Input
            value={entry.timing}
            onChange={(e) => onChange({ ...entry, timing: e.target.value })}
            className="mt-1"
          />
        </div>
      </div>
      <div className="p-4 space-y-3">
        {entry.drafts.map((d, i) => (
          <div key={i} className="rounded-xl border border-border p-3 space-y-2 bg-muted/30">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground">
                Variant {i + 1}
              </span>
              <Button
                variant="ghost"
                size="icon"
                onClick={() =>
                  onChange({
                    ...entry,
                    drafts: entry.drafts.filter((_, idx) => idx !== i),
                  })
                }
              >
                <Trash2 className="w-4 h-4" />
              </Button>
            </div>
            {d.title || entry.channel === "blog" || entry.channel === "email" || entry.channel === "landing" || entry.channel === "ads" ? (
              <Input
                value={d.title}
                onChange={(e) =>
                  onChange({
                    ...entry,
                    drafts: entry.drafts.map((x, idx) =>
                      idx === i ? { ...x, title: e.target.value } : x,
                    ),
                  })
                }
                placeholder="Title / subject / headline"
                className="font-medium"
              />
            ) : null}
            {d.hook ? (
              <Input
                value={d.hook}
                onChange={(e) =>
                  onChange({
                    ...entry,
                    drafts: entry.drafts.map((x, idx) =>
                      idx === i ? { ...x, hook: e.target.value } : x,
                    ),
                  })
                }
                placeholder="Hook"
                className="text-sm italic"
              />
            ) : null}
            <Textarea
              value={d.body}
              onChange={(e) =>
                onChange({
                  ...entry,
                  drafts: entry.drafts.map((x, idx) =>
                    idx === i ? { ...x, body: e.target.value } : x,
                  ),
                })
              }
              rows={6}
              className="text-sm font-mono"
            />
          </div>
        ))}
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            onChange({
              ...entry,
              drafts: [
                ...entry.drafts,
                { title: "", body: "", hook: "", length_hint: "" },
              ],
            })
          }
        >
          <Plus className="w-4 h-4 mr-1" /> Add variant
        </Button>
      </div>
    </div>
  );
}

function ContentBriefEditor({
  value,
  onChange,
}: {
  value: ContentBrief;
  onChange: (v: ContentBrief) => void;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-[1fr_140px] gap-3">
        <div>
          <Label className="text-xs text-muted-foreground">Working title</Label>
          <Input
            value={value.working_title}
            onChange={(e) => onChange({ ...value, working_title: e.target.value })}
            className="mt-1 font-medium"
          />
        </div>
        <div>
          <Label className="text-xs text-muted-foreground">Target length (words)</Label>
          <Input
            type="number"
            value={value.target_length_words}
            onChange={(e) =>
              onChange({
                ...value,
                target_length_words: parseInt(e.target.value, 10) || 0,
              })
            }
            className="mt-1"
          />
        </div>
      </div>
      <StringList
        label="Target keywords"
        items={value.target_keywords}
        onChange={(next) => onChange({ ...value, target_keywords: next })}
      />
      <div>
        <Label className="text-xs text-muted-foreground">Outline</Label>
        <div className="space-y-2 mt-1">
          {value.outline.map((s, i) => (
            <div key={i} className="rounded-xl border border-border p-3 space-y-2 bg-muted/30">
              <div className="flex gap-2">
                <Input
                  value={s.heading}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      outline: value.outline.map((x, idx) =>
                        idx === i ? { ...x, heading: e.target.value } : x,
                      ),
                    })
                  }
                  className="font-medium"
                  placeholder="Section heading"
                />
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() =>
                    onChange({
                      ...value,
                      outline: value.outline.filter((_, idx) => idx !== i),
                    })
                  }
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
              <StringList
                label="Key points"
                items={s.key_points}
                onChange={(next) =>
                  onChange({
                    ...value,
                    outline: value.outline.map((x, idx) =>
                      idx === i ? { ...x, key_points: next } : x,
                    ),
                  })
                }
              />
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              onChange({
                ...value,
                outline: [...value.outline, { heading: "", key_points: [] }],
              })
            }
          >
            <Plus className="w-4 h-4 mr-1" /> Add section
          </Button>
        </div>
      </div>
      <StringList
        label="Internal link ideas"
        items={value.internal_link_ideas}
        onChange={(next) => onChange({ ...value, internal_link_ideas: next })}
      />
      <div>
        <Label className="text-xs text-muted-foreground">Distribution notes</Label>
        <Textarea
          value={value.distribution_notes}
          onChange={(e) => onChange({ ...value, distribution_notes: e.target.value })}
          rows={2}
          className="mt-1"
        />
      </div>
    </div>
  );
}

function StringList({
  label,
  items,
  onChange,
}: {
  label: string;
  items: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div>
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <div className="space-y-1 mt-1">
        {items.map((v, i) => (
          <div key={i} className="flex gap-2">
            <Input
              value={v}
              onChange={(e) =>
                onChange(items.map((x, idx) => (idx === i ? e.target.value : x)))
              }
              className="flex-1 text-xs"
            />
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onChange(items.filter((_, idx) => idx !== i))}
            >
              <Trash2 className="w-4 h-4" />
            </Button>
          </div>
        ))}
        <Button
          variant="outline"
          size="sm"
          onClick={() => onChange([...items, ""])}
        >
          <Plus className="w-4 h-4 mr-1" /> Add
        </Button>
      </div>
    </div>
  );
}
