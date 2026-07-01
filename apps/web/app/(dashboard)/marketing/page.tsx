"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Loader2, Plus, Rocket, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  PageHeader,
  Stat,
  StatGrid,
  StatusPill,
  type PillTone,
} from "@/components/actual/kit";
import { cn } from "@/lib/utils";

type Channel = "blog" | "linkedin" | "x" | "email" | "landing" | "ads";
type Status = "draft" | "generating" | "ready" | "published" | "failed";

interface MarketingBrief {
  id: string;
  objective: string;
  audience_hint: string | null;
  channels: Channel[];
  competitors: string[];
  status: Status;
  error_message: string | null;
  generated_at: string | null;
  published_at: string | null;
  created_at: string;
  updated_at: string;
}

const ALL_CHANNELS: Channel[] = ["blog", "linkedin", "x", "email", "landing", "ads"];

const STATUS_TONE: Record<Status, PillTone> = {
  draft: "gray",
  generating: "blue",
  ready: "amber",
  published: "green",
  failed: "red",
};

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function MarketingBriefsPage() {
  const router = useRouter();
  const { data, mutate, isLoading } = useSWR<MarketingBrief[]>(
    "/api/marketing/briefs",
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 5000 },
  );

  const [open, setOpen] = useState(false);

  const briefs = useMemo(() => data ?? [], [data]);
  const stats = useMemo(() => {
    const by = (s: Status) => briefs.filter((b) => b.status === s).length;
    return {
      total: briefs.length,
      published: by("published"),
      ready: by("ready"),
      generating: by("generating"),
    };
  }, [briefs]);

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-6 md:p-8">
      <PageHeader
        eyebrow="Marketing"
        title="Marketing briefs"
        description="AI-generated positioning, messaging, channel drafts, and content briefs — grounded in your knowledge base and competitor context."
        actions={
          <Button onClick={() => setOpen(true)}>
            <Plus className="size-4" /> New brief
          </Button>
        }
      />

      <StatGrid>
        <Stat label="Total briefs" value={stats.total} />
        <Stat label="Published" value={stats.published} tone="up" />
        <Stat label="Ready to review" value={stats.ready} />
        <Stat label="Generating" value={stats.generating} />
      </StatGrid>

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full rounded-2xl" />
          <Skeleton className="h-24 w-full rounded-2xl" />
        </div>
      ) : !briefs.length ? (
        <EmptyState onCreate={() => setOpen(true)} />
      ) : (
        <div className="space-y-3">
          {briefs.map((b) => (
            <BriefRow
              key={b.id}
              brief={b}
              onClick={() => router.push(`/marketing/${b.id}`)}
            />
          ))}
        </div>
      )}

      <NewBriefDialog
        open={open}
        onOpenChange={setOpen}
        onCreated={async (id) => {
          setOpen(false);
          await mutate();
          router.push(`/marketing/${id}`);
        }}
      />
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-background p-12 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-brand-tint text-brand">
        <Rocket className="h-5 w-5" />
      </div>
      <p className="mt-3 text-sm font-bold">No marketing briefs yet</p>
      <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
        Spin up a brief by telling the agent your objective. It pulls positioning,
        voice, and competitor context from your KB and emits 5 editable artifacts.
      </p>
      <Button onClick={onCreate} size="lg" className="mt-5">
        <Plus className="size-4" /> Create your first brief
      </Button>
    </div>
  );
}

function BriefRow({
  brief,
  onClick,
}: {
  brief: MarketingBrief;
  onClick: () => void;
}) {
  return (
    <Link
      href={`/marketing/${brief.id}`}
      onClick={(e) => {
        e.preventDefault();
        onClick();
      }}
      className="block rounded-2xl border border-border bg-card p-5 transition-colors hover:bg-muted/40"
    >
      <div className="flex items-center gap-2">
        <StatusPill tone={STATUS_TONE[brief.status]}>{brief.status}</StatusPill>
        <span className="text-xs text-muted-foreground">
          {new Date(brief.created_at).toLocaleString()}
        </span>
      </div>
      <p className="mt-2.5 line-clamp-2 text-sm font-bold text-foreground">
        {brief.objective}
      </p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {brief.channels.map((c) => (
          <span
            key={c}
            className="rounded-full bg-muted px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground"
          >
            {c}
          </span>
        ))}
        {brief.competitors.length ? (
          <span className="rounded-full bg-amber-tint px-2.5 py-0.5 text-[10px] font-bold text-amber">
            vs {brief.competitors.join(", ")}
          </span>
        ) : null}
      </div>
    </Link>
  );
}

function NewBriefDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (b: boolean) => void;
  onCreated: (id: string) => void;
}) {
  const [objective, setObjective] = useState("");
  const [audienceHint, setAudienceHint] = useState("");
  const [channels, setChannels] = useState<Channel[]>(["blog", "linkedin", "email"]);
  const [competitorsRaw, setCompetitorsRaw] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const toggleChannel = (c: Channel) => {
    setChannels((prev) =>
      prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c],
    );
  };

  const submit = async () => {
    const trimmed = objective.trim();
    if (trimmed.length < 4) {
      toast.error("Give the agent a clearer objective.");
      return;
    }
    if (channels.length === 0) {
      toast.error("Select at least one channel.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch("/api/marketing/briefs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          objective: trimmed,
          audience_hint: audienceHint.trim() || null,
          channels,
          competitors: competitorsRaw
            .split(",")
            .map((c) => c.trim())
            .filter(Boolean),
        }),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || `Failed (${res.status})`);
      }
      const brief = (await res.json()) as { id: string };
      toast.success("Generating — this takes ~30–60s.");
      onCreated(brief.id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to create brief.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>New marketing brief</DialogTitle>
          <DialogDescription>
            Tell the agent what you&apos;re trying to achieve. It will ground in
            your KB and emit positioning, pillars, channel drafts, and a content brief.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label htmlFor="objective">Objective</Label>
            <Textarea
              id="objective"
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              placeholder="e.g. Launch our new performance-review module to HR leaders at 200-2,000 person companies."
              rows={3}
              className="mt-1"
            />
          </div>
          <div>
            <Label htmlFor="audience">Audience hint (optional)</Label>
            <Input
              id="audience"
              value={audienceHint}
              onChange={(e) => setAudienceHint(e.target.value)}
              placeholder="e.g. VP People at Series B–D SaaS"
              className="mt-1"
            />
          </div>
          <div>
            <Label>Channels</Label>
            <div className="mt-2 flex flex-wrap gap-2">
              {ALL_CHANNELS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => toggleChannel(c)}
                  className={cn(
                    "rounded-full border px-3 py-1 text-xs font-semibold transition-colors",
                    channels.includes(c)
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-background text-muted-foreground hover:bg-muted",
                  )}
                >
                  {c}
                </button>
              ))}
            </div>
          </div>
          <div>
            <Label htmlFor="competitors">Competitors (optional, comma-separated)</Label>
            <Input
              id="competitors"
              value={competitorsRaw}
              onChange={(e) => setCompetitorsRaw(e.target.value)}
              placeholder="e.g. Lattice, 15Five"
              className="mt-1"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Sparkles className="size-4" />
            )}
            Generate brief
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
