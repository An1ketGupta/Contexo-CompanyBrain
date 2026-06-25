"use client";

import { useEffect, useState } from "react";
import { Loader2, Wand2, CheckCircle2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";

interface DocRow {
  document_id: string;
  name: string | null;
  attempts: number;
  passes: number;
  unique_passers: number;
  pass_rate: number;
}

interface UserRow {
  user_id: string;
  display_name: string | null;
  document_id: string;
  document_name: string | null;
  score: number | null;
  passed: boolean | null;
  completed_at: string | null;
}

interface Report {
  summary: { total_attempts: number; passes: number };
  by_document: DocRow[];
  by_user: UserRow[];
}

export default function AdminCertificationsPage() {
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [docId, setDocId] = useState("");
  const [generating, setGenerating] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const res = await fetch("/api/certifications/admin/report");
      if (res.ok) setReport(await res.json());
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);

  async function generate() {
    if (!docId.trim()) {
      toast.error("Enter a document id first.");
      return;
    }
    setGenerating(true);
    try {
      const res = await fetch(
        `/api/certifications/admin/documents/${docId.trim()}/generate-quiz`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question_count: 5, passing_score: 0.8 }),
        },
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `Generate failed (${res.status})`);
      toast.success(
        `Quiz generated with ${data.question_count} questions. Now flip "require certification" on the document.`,
      );
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Generate failed.";
      toast.error(msg);
    } finally {
      setGenerating(false);
    }
  }

  async function setRequired(documentId: string, required: boolean) {
    const res = await fetch(
      `/api/certifications/admin/documents/${documentId}/require`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ required }),
      },
    );
    if (res.ok) {
      toast.success(
        required ? "Certification required." : "Certification optional.",
      );
    } else {
      toast.error("Failed to update.");
    }
  }

  return (
    <div className="container max-w-5xl py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Knowledge certifications</h1>
        <p className="text-sm text-muted-foreground mt-1">
          LLM-generated multiple-choice quizzes that gate policy acknowledgement.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Wand2 className="size-4" /> Generate a quiz
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input
              placeholder="document id (UUID)"
              value={docId}
              onChange={(e) => setDocId(e.target.value)}
            />
            <Button onClick={generate} disabled={generating || !docId.trim()}>
              {generating ? <Loader2 className="size-4 mr-2 animate-spin" /> : null}
              Generate
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            We&apos;ll synthesise 5 multiple-choice questions from the document&apos;s
            chunks. Existing active quiz (if any) is archived. After generating,
            flip the document&apos;s &quot;Require certification&quot; to enforce passing
            it before acknowledgement is accepted.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pass rates by document</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          ) : !report || report.by_document.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No attempts yet.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Document</TableHead>
                  <TableHead className="text-right">Attempts</TableHead>
                  <TableHead className="text-right">Passes</TableHead>
                  <TableHead className="text-right">Unique passers</TableHead>
                  <TableHead className="text-right">Pass rate</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {report.by_document.map((d) => (
                  <TableRow key={d.document_id}>
                    <TableCell className="font-medium">{d.name ?? d.document_id}</TableCell>
                    <TableCell className="text-right">{d.attempts}</TableCell>
                    <TableCell className="text-right">{d.passes}</TableCell>
                    <TableCell className="text-right">{d.unique_passers}</TableCell>
                    <TableCell className="text-right">
                      {Math.round(d.pass_rate * 100)}%
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setRequired(d.document_id, true)}
                      >
                        Require
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Latest attempt per user</CardTitle>
        </CardHeader>
        <CardContent>
          {!report || report.by_user.length === 0 ? (
            <p className="text-sm text-muted-foreground">No attempts yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Document</TableHead>
                  <TableHead className="text-right">Score</TableHead>
                  <TableHead className="text-right">Result</TableHead>
                  <TableHead className="text-right">When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {report.by_user.map((u) => (
                  <TableRow key={`${u.user_id}:${u.document_id}`}>
                    <TableCell>{u.display_name ?? u.user_id}</TableCell>
                    <TableCell>{u.document_name ?? u.document_id}</TableCell>
                    <TableCell className="text-right">
                      {u.score !== null ? `${Math.round(u.score * 100)}%` : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      {u.passed ? (
                        <CheckCircle2 className="size-4 text-green-600 inline" />
                      ) : (
                        <XCircle className="size-4 text-red-600 inline" />
                      )}
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground text-xs">
                      {u.completed_at
                        ? new Date(u.completed_at).toLocaleString()
                        : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
