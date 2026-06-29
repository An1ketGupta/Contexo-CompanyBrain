"use client";

import { useRef, useState } from "react";
import { Loader2, Upload, CheckCircle2, RefreshCw, FileText } from "lucide-react";

import { Button } from "@/components/ui/button";

interface DealDoc {
  id: string;
  kind: string;
  signed_url: string | null;
  pdf_signed_url: string | null;
  source: string;
  revision: number;
}

interface Props {
  dealId: string;
  documents: DealDoc[];
  onMutated: () => void;
}

export function ProposalReview({ dealId, documents, onMutated }: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [saving, setSaving] = useState<null | "approve" | "regen" | "upload">(null);
  const [error, setError] = useState<string | null>(null);

  const proposalDocs = documents
    .filter((d) => d.kind === "proposal")
    .sort((a, b) => b.revision - a.revision);

  async function approve() {
    setSaving("approve");
    setError(null);
    try {
      const res = await fetch(`/api/sales/deals/runs/${dealId}/proposal/approve`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`Failed: ${res.status}`);
      onMutated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(null);
    }
  }

  async function regenerate() {
    setSaving("regen");
    setError(null);
    try {
      const res = await fetch(`/api/sales/deals/runs/${dealId}/proposal/regenerate`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`Failed: ${res.status}`);
      onMutated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(null);
    }
  }

  async function uploadEdited(file: File) {
    setSaving("upload");
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`/api/sales/deals/runs/${dealId}/proposal/upload-edited`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail || b.message || `Failed: ${res.status}`);
      }
      onMutated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <header>
        <h3 className="text-lg font-semibold">Proposal draft</h3>
        <p className="text-sm text-muted-foreground">
          Preview the draft, regenerate, upload an edited DOCX/PDF, or approve to mark sent.
        </p>
      </header>

      <ul className="space-y-2">
        {proposalDocs.map((d) => (
          <li
            key={d.id}
            className="flex items-center justify-between rounded-md border bg-muted/30 px-3 py-2 text-sm"
          >
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-muted-foreground" />
              <span>
                Proposal v{d.revision}{" "}
                <span className="text-xs text-muted-foreground">
                  ({d.source === "agent_generated" ? "agent draft" : "rep upload"})
                </span>
              </span>
            </div>
            <div className="flex items-center gap-2">
              {d.pdf_signed_url ? (
                <a
                  href={d.pdf_signed_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs underline"
                >
                  PDF
                </a>
              ) : null}
              {d.signed_url ? (
                <a
                  href={d.signed_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs underline"
                >
                  DOCX
                </a>
              ) : null}
            </div>
          </li>
        ))}
        {proposalDocs.length === 0 ? (
          <li className="text-sm text-muted-foreground">No proposal drafts yet.</li>
        ) : null}
      </ul>

      {error ? (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-600">
          {error}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-end gap-2">
        <input
          ref={fileInput}
          type="file"
          accept=".pdf,.docx"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) uploadEdited(f);
          }}
        />
        <Button
          variant="outline"
          onClick={() => fileInput.current?.click()}
          disabled={saving !== null}
        >
          {saving === "upload" ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Upload className="mr-2 h-4 w-4" />
          )}
          Upload edited version
        </Button>
        <Button variant="outline" onClick={regenerate} disabled={saving !== null}>
          {saving === "regen" ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="mr-2 h-4 w-4" />
          )}
          Regenerate
        </Button>
        <Button onClick={approve} disabled={saving !== null}>
          {saving === "approve" ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <CheckCircle2 className="mr-2 h-4 w-4" />
          )}
          Approve & mark sent
        </Button>
      </div>
    </div>
  );
}
