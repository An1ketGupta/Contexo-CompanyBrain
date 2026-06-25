"use client";

import { useRouter, useParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { Loader2, Send, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";

interface Step {
  id: string;
  step_order: number;
  send_offset_days: number;
  subject: string;
  body: string;
  status: string;
  scheduled_for: string | null;
  sent_at: string | null;
  gmail_message_id: string | null;
  error_message: string | null;
}

interface Sequence {
  id: string;
  name: string;
  prospect_email: string;
  prospect_name: string | null;
  prospect_context: string;
  status: string;
  sender_email: string | null;
  created_at: string;
}

interface Response {
  sequence: Sequence;
  steps: Step[];
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function SequenceDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params?.id as string;

  const { data, error, isLoading, mutate } = useSWR<Response>(
    id ? `/api/sequences/${id}` : null,
    fetcher,
    { revalidateOnFocus: false },
  );

  const isDraft = data?.sequence.status === "draft";

  const [working, setWorking] = useState(false);

  const saveStep = async (stepId: string, patch: Partial<Step>) => {
    const res = await fetch(`/api/sequences/${id}/steps/${stepId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.message || `Save failed (${res.status})`);
    }
    return res.json();
  };

  const schedule = async () => {
    setWorking(true);
    try {
      const res = await fetch(`/api/sequences/${id}/schedule`, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.message || `Failed (${res.status})`);
      }
      toast.success("Sequence scheduled.");
      mutate();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setWorking(false);
    }
  };

  const cancel = async () => {
    if (!confirm("Cancel this sequence? Remaining steps won't be sent.")) return;
    setWorking(true);
    try {
      const res = await fetch(`/api/sequences/${id}/cancel`, { method: "POST" });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      mutate();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setWorking(false);
    }
  };

  if (isLoading) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 p-6">
        <Skeleton className="h-12 w-2/3" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="mx-auto max-w-3xl p-6 text-sm text-destructive">
        {(error as Error)?.message ?? "Not found."}
      </div>
    );
  }

  const { sequence, steps } = data;

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-semibold tracking-tight">
            {sequence.name}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            To {sequence.prospect_name ? `${sequence.prospect_name} · ` : ""}
            <span className="font-mono">{sequence.prospect_email}</span>
          </p>
        </div>
        <Badge>{sequence.status}</Badge>
      </header>

      <div className="space-y-4">
        {steps.map((s) => (
          <StepCard
            key={s.id}
            step={s}
            editable={isDraft}
            onSave={(patch) =>
              saveStep(s.id, patch)
                .then(() => mutate())
                .catch((err) => toast.error((err as Error).message))
            }
          />
        ))}
      </div>

      <div className="flex justify-end gap-2">
        {(sequence.status === "draft" ||
          sequence.status === "scheduled" ||
          sequence.status === "active") && (
          <Button variant="outline" onClick={cancel} disabled={working} className="gap-2">
            <X className="size-4" />
            Cancel sequence
          </Button>
        )}
        {isDraft && (
          <Button onClick={schedule} disabled={working} className="gap-2">
            {working ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            {working ? "Scheduling…" : "Schedule sends"}
          </Button>
        )}
        <Button variant="ghost" onClick={() => router.push("/sequences")}>
          Back
        </Button>
      </div>
    </div>
  );
}

function StepCard({
  step,
  editable,
  onSave,
}: {
  step: Step;
  editable: boolean;
  onSave: (patch: { subject?: string; body?: string; send_offset_days?: number }) => void;
}) {
  const [subject, setSubject] = useState(step.subject);
  const [body, setBody] = useState(step.body);
  const [offset, setOffset] = useState(step.send_offset_days);
  const dirty =
    subject !== step.subject || body !== step.body || offset !== step.send_offset_days;

  return (
    <div className="rounded-md border bg-card p-4">
      <div className="mb-3 flex items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2">
          <span className="font-medium">Step {step.step_order + 1}</span>
          <span className="text-muted-foreground">
            Sends {step.send_offset_days === 0 ? "immediately" : `+${step.send_offset_days} days`}
          </span>
        </div>
        <Badge variant="outline">{step.status}</Badge>
      </div>
      {editable ? (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-[1fr_140px]">
            <div className="space-y-1">
              <Label className="text-xs">Subject</Label>
              <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Send offset (days)</Label>
              <Input
                type="number"
                min={0}
                max={365}
                value={offset}
                onChange={(e) => setOffset(Number(e.target.value))}
              />
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Body</Label>
            <Textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={8}
              className="font-mono text-sm"
            />
          </div>
          {dirty && (
            <div className="flex justify-end">
              <Button
                size="sm"
                onClick={() =>
                  onSave({ subject, body, send_offset_days: offset })
                }
              >
                Save changes
              </Button>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-2 text-sm">
          <p className="font-medium">{step.subject}</p>
          <pre className="whitespace-pre-wrap text-xs text-muted-foreground">{step.body}</pre>
          {step.sent_at && (
            <p className="text-xs text-muted-foreground">
              Sent {new Date(step.sent_at).toLocaleString()}
            </p>
          )}
          {step.error_message && (
            <p className="text-xs text-destructive">{step.error_message}</p>
          )}
        </div>
      )}
    </div>
  );
}
