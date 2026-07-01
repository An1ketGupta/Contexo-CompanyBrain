"use client";

/**
 * Dialog HR opens to kick off the Onboarding v2 pipeline for a candidate.
 *
 * Captures the offer details only:
 *   - candidate identity (name, email, phone)
 *   - role + designation + CTC + start date + manager
 *
 * Background-check references are NO LONGER collected here — the candidate
 * submits them via a public form linked from their LOI email. HR retains an
 * override button on the run detail page if the candidate ghosts.
 *
 * On submit, POSTs to /api/onboarding/runs which fires the Inngest agent.
 * Routes the HR to the new run's detail page so they can watch the agent
 * generate the LOI in real time.
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface StartOnboardingDialogProps {
  /** Pre-fill role from the requisition the HR is looking at. */
  defaultRoleTitle?: string;
  /** Pre-fill candidate from a single ATS-synced candidate. */
  defaultCandidate?: {
    id?: string;
    name?: string;
    email?: string;
    phone?: string;
  };
  /** Optional id of the source requisition for soft linkage. */
  requisitionId?: string;
  /** Render override — if omitted, a default outline button is used. */
  trigger?: React.ReactNode;
  /**
   * Controlled-open API. When provided, the parent owns visibility — useful
   * when the same dialog instance is reused across candidates by swapping
   * defaultCandidate. When omitted, the dialog manages its own open state
   * driven by the trigger.
   */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export function StartOnboardingDialog({
  defaultRoleTitle,
  defaultCandidate,
  requisitionId,
  trigger,
  open: controlledOpen,
  onOpenChange,
}: StartOnboardingDialogProps) {
  const router = useRouter();
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const open = controlledOpen ?? uncontrolledOpen;
  const setOpen = (next: boolean) => {
    if (onOpenChange) onOpenChange(next);
    if (controlledOpen === undefined) setUncontrolledOpen(next);
  };
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [candidateName, setCandidateName] = useState(
    defaultCandidate?.name || "",
  );
  const [candidateEmail, setCandidateEmail] = useState(
    defaultCandidate?.email || "",
  );
  const [candidatePhone, setCandidatePhone] = useState(
    defaultCandidate?.phone || "",
  );
  const [roleTitle, setRoleTitle] = useState(defaultRoleTitle || "");
  const [designation, setDesignation] = useState("");
  const [ctcAmount, setCtcAmount] = useState("");
  const [ctcCurrency, setCtcCurrency] = useState("INR");
  const [startDate, setStartDate] = useState("");
  const [workLocation, setWorkLocation] = useState("");
  const [probationMonths, setProbationMonths] = useState("");
  const [managerName, setManagerName] = useState("");
  const [managerEmail, setManagerEmail] = useState("");

  function reset() {
    setCandidateName(defaultCandidate?.name || "");
    setCandidateEmail(defaultCandidate?.email || "");
    setCandidatePhone(defaultCandidate?.phone || "");
    setRoleTitle(defaultRoleTitle || "");
    setDesignation("");
    setCtcAmount("");
    setCtcCurrency("INR");
    setStartDate("");
    setWorkLocation("");
    setProbationMonths("");
    setManagerName("");
    setManagerEmail("");
    setError(null);
  }

  // Re-seed identity + role fields when the dialog opens with a different
  // pipeline candidate. Without this, swapping defaultCandidate while the
  // component stays mounted would leave stale values in the form.
  // We intentionally skip the offer-detail fields (CTC, start date, etc.) so
  // an HR mid-typing isn't reset if they re-click the same candidate's
  // button — only identity changes trigger the re-seed.
  useEffect(() => {
    if (!open) return;
    setCandidateName(defaultCandidate?.name || "");
    setCandidateEmail(defaultCandidate?.email || "");
    setCandidatePhone(defaultCandidate?.phone || "");
    setRoleTitle(defaultRoleTitle || "");
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    open,
    defaultCandidate?.id,
    defaultCandidate?.name,
    defaultCandidate?.email,
    defaultCandidate?.phone,
    defaultRoleTitle,
  ]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!candidateName.trim() || !candidateEmail.trim() || !roleTitle.trim()) {
      setError("Candidate name, email, and role are required.");
      return;
    }
    if (!startDate) {
      setError("Pick a start date.");
      return;
    }

    const payload = {
      candidate_id: defaultCandidate?.id,
      requisition_id: requisitionId,
      candidate_name: candidateName.trim(),
      candidate_email: candidateEmail.trim(),
      candidate_phone: candidatePhone.trim() || undefined,
      role_title: roleTitle.trim(),
      designation: designation.trim() || undefined,
      ctc_amount: ctcAmount ? Number(ctcAmount) : undefined,
      ctc_currency: ctcCurrency,
      start_date: startDate,
      work_location: workLocation.trim() || undefined,
      probation_period_months: probationMonths
        ? Number(probationMonths)
        : undefined,
      reporting_manager_name: managerName.trim() || undefined,
      reporting_manager_email: managerEmail.trim() || undefined,
      references: [],
    };

    setSubmitting(true);
    try {
      const res = await fetch("/api/onboarding/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = (await res.json().catch(() => ({}))) as {
        id?: string;
        detail?: string;
        message?: string;
      };
      if (!res.ok || !body.id) {
        setError(
          body.detail || body.message || "Couldn't start onboarding. Try again.",
        );
        return;
      }
      setOpen(false);
      reset();
      router.push(`/onboarding/${body.id}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      {controlledOpen === undefined ? (
        <DialogTrigger asChild>
          {trigger ?? (
            <Button variant="outline" size="sm">
              Mark hired & start onboarding
            </Button>
          )}
        </DialogTrigger>
      ) : null}
      <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Start onboarding</DialogTitle>
        </DialogHeader>

        <form onSubmit={onSubmit} className="space-y-6">
          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Candidate
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="cand-name">Full name</Label>
                <Input
                  id="cand-name"
                  value={candidateName}
                  onChange={(e) => setCandidateName(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="cand-email">Email</Label>
                <Input
                  id="cand-email"
                  type="email"
                  value={candidateEmail}
                  onChange={(e) => setCandidateEmail(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="cand-phone">Phone (optional)</Label>
                <Input
                  id="cand-phone"
                  value={candidatePhone}
                  onChange={(e) => setCandidatePhone(e.target.value)}
                />
              </div>
            </div>
          </section>

          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Role and offer
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="role-title">Role title</Label>
                <Input
                  id="role-title"
                  value={roleTitle}
                  onChange={(e) => setRoleTitle(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="designation">Designation (optional)</Label>
                <Input
                  id="designation"
                  value={designation}
                  onChange={(e) => setDesignation(e.target.value)}
                  placeholder="Senior Engineer, II"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ctc">CTC</Label>
                <div className="flex gap-2">
                  <Input
                    id="ctc-currency"
                    className="max-w-20"
                    value={ctcCurrency}
                    onChange={(e) =>
                      setCtcCurrency(e.target.value.toUpperCase())
                    }
                  />
                  <Input
                    id="ctc"
                    type="number"
                    min={0}
                    value={ctcAmount}
                    onChange={(e) => setCtcAmount(e.target.value)}
                    placeholder="e.g. 1800000"
                  />
                </div>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="start-date">Start date</Label>
                <Input
                  id="start-date"
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="work-location">Work location</Label>
                <Input
                  id="work-location"
                  value={workLocation}
                  onChange={(e) => setWorkLocation(e.target.value)}
                  placeholder="Bengaluru / Remote"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="probation">
                  Probation period (months)
                </Label>
                <Input
                  id="probation"
                  type="number"
                  min={0}
                  max={24}
                  value={probationMonths}
                  onChange={(e) => setProbationMonths(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mgr-name">Reporting manager name</Label>
                <Input
                  id="mgr-name"
                  value={managerName}
                  onChange={(e) => setManagerName(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="mgr-email">Reporting manager email</Label>
                <Input
                  id="mgr-email"
                  type="email"
                  value={managerEmail}
                  onChange={(e) => setManagerEmail(e.target.value)}
                />
              </div>
            </div>
          </section>

          {error ? (
            <p className="rounded-xl border border-destructive/30 bg-destructive-soft p-3 text-xs font-medium text-destructive">
              {error}
            </p>
          ) : null}

          <DialogFooter>
            <Button
              variant="outline"
              type="button"
              onClick={() => setOpen(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : null}
              Start onboarding
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
