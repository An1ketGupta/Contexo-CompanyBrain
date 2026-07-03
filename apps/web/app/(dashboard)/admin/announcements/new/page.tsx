"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export default function NewAnnouncementPage() {
  const router = useRouter();
  const [request, setRequest] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const res = await fetch("/api/admin/announcements/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_text: request }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.message || `Failed (${res.status})`);
      }
      const data = await res.json();
      router.push(`/admin/announcements/${data.announcement.id}`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-2xl font-extrabold tracking-tight">New announcement</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Describe what to communicate. The AI drafts email / Slack / Notion versions
          grounded in your KB. You pick channels and a send time.
        </p>
      </header>

      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="request">What needs to go out?</Label>
          <Textarea
            id="request"
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            placeholder={
              "Send an all-hands recap of last week's Q3 planning meeting: " +
              "OKRs we agreed on, key decisions, open follow-ups."
            }
            rows={8}
            maxLength={4000}
            required
          />
        </div>
        <div className="flex justify-end gap-2">
          <Button type="submit" disabled={submitting} className="gap-2 rounded-full">
            <Sparkles className="size-4" />
            {submitting ? "Drafting…" : "Draft announcement"}
          </Button>
        </div>
      </form>
    </div>
  );
}
