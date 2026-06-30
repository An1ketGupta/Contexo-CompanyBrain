"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
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

  return (
    <div className="container max-w-5xl mx-auto py-8 px-4">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Rocket className="w-6 h-6" /> Marketing briefs
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            AI-generated positioning, messaging, channel drafts, and content briefs
            grounded in your KB.
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="w-4 h-4 mr-2" /> New brief
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : !data?.length ? (
        <EmptyState onCreate={() => setOpen(true)} />
      ) : (
        <div className="space-y-3">
          {data.map((b) => (
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
    <div className="border border-dashed rounded-lg p-12 text-center">
      <Sparkles className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
      <h2 className="text-lg font-semibold mb-2">No marketing briefs yet</h2>
      <p className="text-sm text-muted-foreground mb-6 max-w-md mx-auto">
        Spin up a brief by telling the agent your objective. It will pull positioning,
        voice, and competitor context from your KB and emit 5 editable artifacts.
      </p>
      <Button onClick={onCreate} size="lg">
        <Plus className="w-4 h-4 mr-2" /> Create your first brief
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
      className="block rounded-lg border bg-card hover:bg-accent/40 transition-colors p-4"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <StatusBadge status={brief.status} />
            <span className="text-xs text-muted-foreground">
              {new Date(brief.created_at).toLocaleString()}
            </span>
          </div>
          <p className="text-sm font-medium line-clamp-2">{brief.objective}</p>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {brief.channels.map((c) => (
              <span
                key={c}
                className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground uppercase tracking-wide"
              >
                {c}
              </span>
            ))}
            {brief.competitors.length ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-800">
                vs {brief.competitors.join(", ")}
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </Link>
  );
}

function StatusBadge({ status }: { status: Status }) {
  const styles: Record<Status, string> = {
    draft: "bg-muted text-muted-foreground",
    generating: "bg-blue-100 text-blue-700",
    ready: "bg-amber-100 text-amber-800",
    published: "bg-green-100 text-green-800",
    failed: "bg-red-100 text-red-800",
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${styles[status]}`}>
      {status}
    </span>
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
            <div className="flex flex-wrap gap-2 mt-2">
              {ALL_CHANNELS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => toggleChannel(c)}
                  className={`text-xs px-3 py-1 rounded-full border transition-colors ${
                    channels.includes(c)
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-background text-muted-foreground border-border hover:bg-muted"
                  }`}
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
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <Sparkles className="w-4 h-4 mr-2" />
            )}
            Generate brief
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
