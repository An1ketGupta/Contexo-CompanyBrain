"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export default function NewSequencePage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [prospectEmail, setProspectEmail] = useState("");
  const [prospectName, setProspectName] = useState("");
  const [context, setContext] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const res = await fetch("/api/sequences/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          prospect_email: prospectEmail,
          prospect_name: prospectName || null,
          prospect_context: context,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.message || `Failed (${res.status})`);
      }
      const data = await res.json();
      router.push(`/sequences/${data.sequence.id}`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">New sequence</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          The AI drafts 3 emails (Day 0, +3, +7) grounded in your knowledge base.
          You review, edit, then schedule.
        </p>
      </header>

      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="name">Sequence name</Label>
          <Input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Acme Corp — Q1 outreach"
            maxLength={200}
            required
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="prospect-name">Prospect name (optional)</Label>
            <Input
              id="prospect-name"
              value={prospectName}
              onChange={(e) => setProspectName(e.target.value)}
              placeholder="Alex Chen"
              maxLength={200}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="prospect-email">Prospect email</Label>
            <Input
              id="prospect-email"
              type="email"
              value={prospectEmail}
              onChange={(e) => setProspectEmail(e.target.value)}
              placeholder="alex@example.com"
              required
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="context">Context for the AI</Label>
          <Textarea
            id="context"
            value={context}
            onChange={(e) => setContext(e.target.value)}
            placeholder={
              "Enterprise prospect in manufacturing. Mentioned SOC2 concerns. Last call: " +
              "interested in our audit-log feature but worried about price."
            }
            rows={6}
            maxLength={4000}
            required
          />
          <p className="text-xs text-muted-foreground">
            The AI uses this to write tailored copy and references KB docs for proof.
          </p>
        </div>
        <div className="flex justify-end gap-2">
          <Button type="submit" disabled={submitting} className="gap-2">
            <Sparkles className="size-4" />
            {submitting ? "Drafting…" : "Draft sequence"}
          </Button>
        </div>
      </form>
    </div>
  );
}
