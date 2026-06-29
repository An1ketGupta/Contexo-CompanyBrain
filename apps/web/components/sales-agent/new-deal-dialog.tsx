"use client";

import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: () => void;
}

export function NewDealDialog({ open, onOpenChange, onCreated }: Props) {
  const [companyName, setCompanyName] = useState("");
  const [website, setWebsite] = useState("");
  const [industry, setIndustry] = useState("");
  const [contactName, setContactName] = useState("");
  const [contactTitle, setContactTitle] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [notes, setNotes] = useState("");
  const [dealValue, setDealValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setCompanyName("");
    setWebsite("");
    setIndustry("");
    setContactName("");
    setContactTitle("");
    setContactEmail("");
    setNotes("");
    setDealValue("");
    setError(null);
  }

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const payload = {
        company_name: companyName.trim(),
        company_website: website.trim() || null,
        company_industry: industry.trim() || null,
        contact_name: contactName.trim() || null,
        contact_title: contactTitle.trim() || null,
        contact_email: contactEmail.trim() || null,
        notes: notes.trim() || null,
        deal_value_amount: dealValue ? Number(dealValue) : null,
      };
      const res = await fetch("/api/sales/deals/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.message || `Request failed: ${res.status}`);
      }
      reset();
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>New deal</DialogTitle>
          <DialogDescription>
            The agent will research the company, score it against your ICP, and draft a personalized
            cold outreach. You review and approve before anything is sent.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="company">Company name *</Label>
            <Input
              id="company"
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              placeholder="Acme Corp"
              autoFocus
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-2">
              <Label htmlFor="website">Website</Label>
              <Input
                id="website"
                value={website}
                onChange={(e) => setWebsite(e.target.value)}
                placeholder="acme.com"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="industry">Industry</Label>
              <Input
                id="industry"
                value={industry}
                onChange={(e) => setIndustry(e.target.value)}
                placeholder="SaaS, fintech, etc."
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-2">
              <Label htmlFor="contact-name">Contact name</Label>
              <Input
                id="contact-name"
                value={contactName}
                onChange={(e) => setContactName(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="contact-title">Title</Label>
              <Input
                id="contact-title"
                value={contactTitle}
                onChange={(e) => setContactTitle(e.target.value)}
              />
            </div>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="contact-email">Contact email (required to send outreach)</Label>
            <Input
              id="contact-email"
              type="email"
              value={contactEmail}
              onChange={(e) => setContactEmail(e.target.value)}
              placeholder="someone@acme.com"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="deal-value">Expected deal value (USD)</Label>
            <Input
              id="deal-value"
              type="number"
              min={0}
              value={dealValue}
              onChange={(e) => setDealValue(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="notes">Notes</Label>
            <Textarea
              id="notes"
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Source of lead, prior context, anything the agent should know."
            />
          </div>

          {error ? (
            <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-600">
              {error}
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting || !companyName.trim()}>
            {submitting ? "Creating…" : "Create deal"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
