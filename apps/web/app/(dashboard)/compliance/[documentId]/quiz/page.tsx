"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { CheckCircle2, XCircle, Loader2, ArrowLeft } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface Question {
  id: string;
  prompt: string;
  choices: string[];
}

interface Quiz {
  id: string;
  document_id: string;
  passing_score: number;
  question_count: number;
  questions: Question[];
}

interface AttemptResult {
  attempt_id: string;
  score: number;
  correct_count: number;
  total_count: number;
  passed: boolean;
  passing_score: number;
}

export default function QuizPage() {
  const params = useParams<{ documentId: string }>();
  const router = useRouter();
  const documentId = params.documentId;

  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [loading, setLoading] = useState(true);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<AttemptResult | null>(null);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      const res = await fetch(`/api/certifications/documents/${documentId}/quiz`);
      if (res.ok) {
        setQuiz(await res.json());
      } else if (res.status === 404) {
        toast.error("No active quiz for this document.");
      } else {
        toast.error(`Failed to load quiz (${res.status}).`);
      }
      setLoading(false);
    })();
  }, [documentId]);

  async function submit() {
    if (!quiz) return;
    const unanswered = quiz.questions.filter((q) => answers[q.id] === undefined);
    if (unanswered.length) {
      toast.error(`Answer all ${quiz.questions.length} questions before submitting.`);
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch(
        `/api/certifications/quizzes/${quiz.id}/attempts`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            answers: quiz.questions.map((q) => ({
              question_id: q.id,
              selected_index: answers[q.id],
            })),
          }),
        },
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `Submit failed (${res.status})`);
      setResult(data);
      if (data.passed) {
        toast.success("Certification passed!");
      } else {
        toast.error("Score below passing threshold.");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Submit failed.";
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="container max-w-2xl py-8 text-center text-muted-foreground">
        <Loader2 className="size-6 animate-spin mx-auto" />
      </div>
    );
  }
  if (!quiz) {
    return (
      <div className="container max-w-2xl py-8 text-center text-muted-foreground">
        No quiz available.
      </div>
    );
  }

  if (result) {
    return (
      <div className="container max-w-2xl py-8 space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {result.passed ? (
                <CheckCircle2 className="size-5 text-green-600" />
              ) : (
                <XCircle className="size-5 text-red-600" />
              )}
              {result.passed ? "Certification passed" : "Did not pass"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div>
              Score: <strong>{Math.round(result.score * 100)}%</strong> ({result.correct_count}/
              {result.total_count})
            </div>
            <div className="text-muted-foreground">
              Passing threshold: {Math.round(result.passing_score * 100)}%
            </div>
            {!result.passed && (
              <p className="pt-2">
                You can re-take the quiz. Review the source document, then try again.
              </p>
            )}
            <div className="pt-3 flex gap-2">
              <Button variant="outline" asChild>
                <Link href="/compliance/pending">Back to pending</Link>
              </Button>
              {!result.passed && (
                <Button
                  onClick={() => {
                    setAnswers({});
                    setResult(null);
                  }}
                >
                  Retake
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="container max-w-2xl py-8 space-y-4">
      <Link
        href="/compliance/pending"
        className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
      >
        <ArrowLeft className="size-3.5" />
        Back
      </Link>
      <Card>
        <CardHeader>
          <CardTitle>Knowledge check</CardTitle>
          <p className="text-sm text-muted-foreground">
            {quiz.question_count} questions ·{" "}
            {Math.round(quiz.passing_score * 100)}% to pass
          </p>
        </CardHeader>
      </Card>

      {quiz.questions.map((q, idx) => (
        <Card key={q.id}>
          <CardContent className="pt-6">
            <p className="font-medium mb-3">
              <span className="text-muted-foreground mr-2">{idx + 1}.</span>
              {q.prompt}
            </p>
            <div className="space-y-2">
              {q.choices.map((choice, i) => (
                <label
                  key={i}
                  className={`flex items-start gap-2 p-3 border rounded-md cursor-pointer transition-colors ${
                    answers[q.id] === i
                      ? "border-primary bg-primary/5"
                      : "hover:bg-accent/40"
                  }`}
                >
                  <input
                    type="radio"
                    className="mt-1"
                    checked={answers[q.id] === i}
                    onChange={() =>
                      setAnswers((prev) => ({ ...prev, [q.id]: i }))
                    }
                  />
                  <span className="text-sm">{choice}</span>
                </label>
              ))}
            </div>
          </CardContent>
        </Card>
      ))}

      <div className="flex justify-end pt-2">
        <Button onClick={submit} disabled={submitting} size="lg">
          {submitting ? <Loader2 className="size-4 mr-2 animate-spin" /> : null}
          Submit
        </Button>
      </div>
    </div>
  );
}
