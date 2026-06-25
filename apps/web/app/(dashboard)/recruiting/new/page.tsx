"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface GenerateResponse {
  id: string;
}

export default function NewRequisitionPage() {
  const router = useRouter();
  const [roleRequest, setRoleRequest] = useState("");
  const [location, setLocation] = useState("");
  const [department, setDepartment] = useState("");
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
          location: location || null,
          department: department || null,
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
          We&apos;ll search your KB for comp bands, requirements, and team
          structure, then generate 5 distinct JD variants for you to pick from.
        </p>
      </header>

      <div className="space-y-4 rounded border bg-white p-6">
        <div className="space-y-2">
          <Label htmlFor="role">Role to hire</Label>
          <Textarea
            id="role"
            placeholder="e.g. 'Senior Product Designer focused on enterprise dashboards. Reports to Head of Design.'"
            rows={4}
            value={roleRequest}
            onChange={(e) => setRoleRequest(e.target.value)}
            maxLength={4000}
          />
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="location">Location (optional)</Label>
            <Input
              id="location"
              placeholder="Remote / NYC / hybrid"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="department">Department (optional)</Label>
            <Input
              id="department"
              placeholder="Engineering / Product / GTM"
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
            />
          </div>
        </div>

        {error && (
          <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={() => router.back()} disabled={generating}>
            Cancel
          </Button>
          <Button
            onClick={handleGenerate}
            disabled={generating || roleRequest.trim().length < 4}
          >
            {generating ? "Generating 5 variants…" : "Generate variants"}
          </Button>
        </div>
      </div>
    </div>
  );
}
