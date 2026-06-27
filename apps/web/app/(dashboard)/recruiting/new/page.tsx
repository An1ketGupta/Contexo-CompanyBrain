"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

type SeniorityLevel = "intern" | "entry" | "mid" | "senior" | "staff" | "lead";

const SENIORITY_OPTIONS: { value: SeniorityLevel; label: string }[] = [
  { value: "intern", label: "Intern" },
  { value: "entry", label: "Entry-level" },
  { value: "mid", label: "Mid-level" },
  { value: "senior", label: "Senior" },
  { value: "staff", label: "Staff / Principal" },
  { value: "lead", label: "Lead / Manager" },
];

interface GenerateResponse {
  id: string;
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-b border-border pb-2">
      <h2 className="text-sm font-semibold text-foreground">{children}</h2>
    </div>
  );
}

export default function NewRequisitionPage() {
  const router = useRouter();

  const [roleRequest, setRoleRequest] = useState("");
  const [location, setLocation] = useState("");
  const [department, setDepartment] = useState("");
  const [seniorityLevel, setSeniorityLevel] = useState<SeniorityLevel>("senior");
  const [stack, setStack] = useState("");
  const [disclosedCompensation, setDisclosedCompensation] = useState("");
  const [interviewDetails, setInterviewDetails] = useState("");
  const [contextNotes, setContextNotes] = useState("");
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = async () => {
    setError(null);
    setGenerating(true);
    try {
      const res = await fetch("/api/recruiting/requisitions/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          role_request: roleRequest,
          location,
          department,
          seniority_level: seniorityLevel,
          disclosed_compensation: disclosedCompensation.trim() || null,
          stack: stack.trim() || null,
          interview_details: interviewDetails.trim(),
          context_notes: contextNotes.trim() || null,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Failed (${res.status})`);
      }
      const data: GenerateResponse = await res.json();
      router.push(`/recruiting/${data.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed");
      setGenerating(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">New requisition</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Generate 3 distinct JD variants to pick from.
        </p>
      </header>

      <div className="space-y-6 rounded border border-border bg-card p-6">

        {/* Role details */}
        <div className="space-y-4">

          <div className="space-y-2">
            <Label htmlFor="role">Role</Label>
            <Textarea
              id="role"
              placeholder="Senior Product Designer focused on enterprise dashboards"
              rows={3}
              value={roleRequest}
              onChange={(e) => setRoleRequest(e.target.value)}
              maxLength={4000}
            />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="location">Location</Label>
              <Input
                id="location"
                placeholder="Remote"
                required
                value={location}
                onChange={(e) => setLocation(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="department">Department</Label>
              <Input
                id="department"
                placeholder="Product"
                required
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="seniority">Seniority level</Label>
            <select
              id="seniority"
              value={seniorityLevel}
              onChange={(e) => setSeniorityLevel(e.target.value as SeniorityLevel)}
              className="w-full rounded border border-input bg-background px-3 py-2 text-sm text-foreground"
            >
              {SENIORITY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>


        {/* Stack */}
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="stack">
              Tech stack
              <span className="ml-1 text-xs font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Input
              id="stack"
              placeholder="e.g. React, Node.js, PostgreSQL, AWS"
              value={stack}
              onChange={(e) => setStack(e.target.value)}
              maxLength={1000}
            />
          </div>
        </div>

        {/* Interview details */}
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="interview">Interview process</Label>
            <Textarea
              id="interview"
              placeholder="e.g. 3 rounds — HR screening → Technical with CTO → Culture fit panel."
              rows={3}
              value={interviewDetails}
              onChange={(e) => setInterviewDetails(e.target.value)}
              maxLength={2000}
            />
          </div>
        </div>

        {/* Compensation */}
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="comp">
              Compensation
              <span className="ml-1 text-xs font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Input
              id="comp"
              placeholder="e.g. $120k–$150k + equity"
              value={disclosedCompensation}
              onChange={(e) => setDisclosedCompensation(e.target.value)}
              maxLength={300}
            />
          </div>
        </div>
        {/* Additional information */}
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="context">
              Additional information
              <span className="ml-1 text-xs font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Textarea
              id="context"
              placeholder="1. Team mission & size  2. Problems they'll solve etc"
              rows={3}
              value={contextNotes}
              onChange={(e) => setContextNotes(e.target.value)}
              maxLength={4000}
            />
          </div>
        </div>

        {error && (
          <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-300">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={() => router.back()} disabled={generating}>
            Cancel
          </Button>
          <Button
            onClick={handleGenerate}
            disabled={
              generating ||
              roleRequest.trim().length < 4 ||
              location.trim().length === 0 ||
              department.trim().length === 0 ||
              interviewDetails.trim().length === 0
            }
          >
            {generating ? "Generating 3 variants…" : "Generate variants"}
          </Button>
        </div>
      </div>
    </div>
  );
}
