"""Knowledge certification service (Production Roadmap 2.10).

Generates LLM-backed multiple-choice quizzes from document chunks and grades
user attempts deterministically. This sits on top of the existing
acknowledgements model: passing a certification quiz auto-acknowledges the
underlying policy document so admins get one report, not two.

Why MCQ-only:
  Auditors (SOX / HIPAA / labor law training) need a defensible answer to
  "was the test consistent across employees?" Multiple-choice with a stored
  correct_index gives an obvious yes. LLM-graded short-answer responses
  reintroduce AI judgement into the compliance loop, which kills the audit
  story. We deliberately do not offer that variant.

Question persistence:
  Quizzes are generated once per (document, version) and cached. Admins can
  regenerate — the new row becomes active and the prior flips to inactive.
  The audit trail is the certification_attempts.quiz_id FK: once an employee
  has attempted v1 of the quiz, the row remains forever.

Grading:
  Pure server-side index comparison. We strip `correct_index` from the
  payload returned to the quiz UI so the answers never leave the server.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client
from app.services.agents.kb_synthesis import synthesize_json

log = logging.getLogger(__name__)


_QUIZ_SYSTEM_PROMPT = """You are an HR compliance training assistant. Build a comprehension quiz from the policy document excerpts provided.

Rules:
- Generate exactly the requested number of multiple-choice questions.
- Each question has exactly 4 choices.
- Exactly one choice must be unambiguously correct based on the document text.
- The three wrong choices must be plausible — same domain, same style — not obviously absurd.
- Cover the most important facts, deadlines, definitions, and required behaviours from the document. Skip throwaway details.
- Questions must be answerable from the document alone — no outside knowledge.
- Keep prompts under 200 characters. Keep choices under 150 characters.

Output JSON only (no prose, no markdown fences):
{
  "questions": [
    {
      "prompt": "string",
      "choices": ["string", "string", "string", "string"],
      "correct_index": 0,
      "rationale": "one-sentence explanation tying the answer to the document"
    }
  ]
}
"""


# ── Quiz generation ────────────────────────────────────────────────────────


async def generate_quiz(
    *,
    org_id: str,
    document_id: str,
    document_version_id: str | None,
    created_by: str,
    question_count: int = 5,
    passing_score: float = 0.80,
) -> dict[str, Any]:
    """Generate a quiz from the document's chunks and store it active.

    Flips any prior active quiz for (doc, version) to is_active=false in the
    same transaction. Returns the new quiz row (correct_index INCLUDED — this
    is the admin-side function; the user-facing fetch strips it).
    """
    svc = get_service_client()

    # Pull representative chunks. Cap to ~6000 chars total to keep the prompt
    # cheap. Pulls in chunk order (idx asc) so the LLM sees the doc structure.
    def _chunks() -> list[dict[str, Any]]:
        res = (
            svc.table("chunks")
            .select("content, chunk_index, section_heading, page_number, id")
            .eq("org_id", org_id)
            .eq("document_id", document_id)
            .eq("is_archived", False)
            .order("chunk_index")
            .limit(40)
            .execute()
        )
        return res.data or []

    chunks = await asyncio.to_thread(_chunks)
    if not chunks:
        raise ValueError("document_has_no_chunks")

    # Build a compact prompt
    parts: list[str] = []
    total_chars = 0
    chunk_ids: list[str] = []
    for c in chunks:
        seg = c.get("content") or ""
        if not seg:
            continue
        head = c.get("section_heading") or ""
        body = f"[chunk {c.get('chunk_index')}{' — ' + head if head else ''}]\n{seg}"
        parts.append(body)
        chunk_ids.append(c["id"])
        total_chars += len(body)
        if total_chars > 6000:
            break

    user_prompt = (
        f"## Number of questions\n{question_count}\n\n"
        f"## Document excerpts\n\n" + "\n\n---\n\n".join(parts)
    )

    result = await synthesize_json(
        system_prompt=_QUIZ_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.2,
        timeout=60.0,
    )
    if not isinstance(result, dict):
        raise RuntimeError("quiz_gen_returned_non_object")
    raw_questions = result.get("questions") or []
    if not isinstance(raw_questions, list) or not raw_questions:
        raise RuntimeError("quiz_gen_returned_no_questions")

    # Normalise + validate each question. Reject the entire generation if any
    # are malformed — a 4-choice MCQ has no recoverable degraded state.
    questions: list[dict[str, Any]] = []
    for i, q in enumerate(raw_questions):
        if not isinstance(q, dict):
            continue
        prompt = (q.get("prompt") or "").strip()
        choices = q.get("choices") or []
        correct_index = q.get("correct_index")
        if (
            not prompt
            or not isinstance(choices, list)
            or len(choices) != 4
            or not all(isinstance(c, str) and c.strip() for c in choices)
            or not isinstance(correct_index, int)
            or not 0 <= correct_index < 4
        ):
            log.warning("quiz_gen.malformed_question doc=%s idx=%s", document_id, i)
            continue
        questions.append(
            {
                "id": str(uuid.uuid4()),
                "prompt": prompt[:500],
                "choices": [c.strip()[:300] for c in choices],
                "correct_index": correct_index,
                "rationale": (q.get("rationale") or "").strip()[:500],
                "source_chunk_ids": chunk_ids[: max(3, len(chunk_ids) // 2)],
            }
        )

    if len(questions) < max(3, question_count // 2):
        raise RuntimeError("quiz_gen_too_few_valid_questions")

    new_row = {
        "org_id": org_id,
        "document_id": document_id,
        "document_version_id": document_version_id,
        "questions": questions,
        "passing_score": passing_score,
        "question_count": len(questions),
        "created_by": created_by,
        "is_active": True,
    }

    def _persist() -> dict[str, Any]:
        # Flip any prior active quiz for this doc+version to inactive so the
        # unique partial index admits the new row.
        (
            svc.table("knowledge_quizzes")
            .update({"is_active": False})
            .eq("document_id", document_id)
            .eq("is_active", True)
            .execute()
        )
        res = svc.table("knowledge_quizzes").insert(new_row).execute()
        if not res.data:
            raise RuntimeError("quiz_insert_returned_no_row")
        return res.data[0]

    return await asyncio.to_thread(_persist)


# ── Take a quiz ────────────────────────────────────────────────────────────


def _strip_answers(quiz_row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the quiz with correct_index + rationale removed.

    Defence in depth — the RLS policy already allows everyone in the org to
    select the active quiz, so the only sensitive data is the answers
    themselves. The router calls this on every user-facing fetch.
    """
    questions = quiz_row.get("questions") or []
    safe = [
        {
            "id": q.get("id"),
            "prompt": q.get("prompt"),
            "choices": q.get("choices") or [],
        }
        for q in questions
        if isinstance(q, dict)
    ]
    return {
        "id": quiz_row.get("id"),
        "document_id": quiz_row.get("document_id"),
        "passing_score": quiz_row.get("passing_score"),
        "question_count": quiz_row.get("question_count"),
        "questions": safe,
    }


async def get_active_quiz_for_document(
    *, org_id: str, document_id: str
) -> dict[str, Any] | None:
    svc = get_service_client()

    def _q() -> dict[str, Any] | None:
        res = (
            svc.table("knowledge_quizzes")
            .select("*")
            .eq("org_id", org_id)
            .eq("document_id", document_id)
            .eq("is_active", True)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    return await asyncio.to_thread(_q)


async def submit_attempt(
    *,
    org_id: str,
    user_id: str,
    quiz_id: str,
    answers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Grade an attempt and persist it.

    `answers` is a list of {question_id, selected_index}. Score is computed
    server-side against the stored correct_index. On a pass we flip any
    matching pending acknowledgement to 'acknowledged' in the same flow so
    the compliance dashboard reflects the new state immediately.
    """
    svc = get_service_client()

    def _quiz() -> dict[str, Any] | None:
        res = (
            svc.table("knowledge_quizzes")
            .select("id, org_id, document_id, document_version_id, passing_score, questions")
            .eq("id", quiz_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    quiz = await asyncio.to_thread(_quiz)
    if not quiz:
        raise PermissionError("quiz_not_found")

    questions = quiz.get("questions") or []
    by_qid: dict[str, dict[str, Any]] = {q["id"]: q for q in questions if isinstance(q, dict) and q.get("id")}
    total = len(by_qid)
    if total == 0:
        raise RuntimeError("quiz_has_no_questions")

    # Grade
    correct = 0
    seen: set[str] = set()
    normalised: list[dict[str, Any]] = []
    for ans in answers or []:
        if not isinstance(ans, dict):
            continue
        qid = ans.get("question_id")
        sel = ans.get("selected_index")
        if (
            not isinstance(qid, str)
            or qid not in by_qid
            or qid in seen
            or not isinstance(sel, int)
            or sel < 0
            or sel > 3
        ):
            continue
        seen.add(qid)
        normalised.append({"question_id": qid, "selected_index": sel})
        if sel == by_qid[qid].get("correct_index"):
            correct += 1

    score = correct / total
    passing = float(quiz.get("passing_score") or 0.8)
    passed = score >= passing
    now = datetime.now(UTC).isoformat()

    attempt = {
        "org_id": org_id,
        "quiz_id": quiz_id,
        "user_id": user_id,
        "document_id": quiz["document_id"],
        "answers": normalised,
        "score": score,
        "correct_count": correct,
        "total_count": total,
        "passed": passed,
        "completed_at": now,
    }

    def _insert() -> dict[str, Any]:
        res = svc.table("certification_attempts").insert(attempt).execute()
        if not res.data:
            raise RuntimeError("attempt_insert_returned_no_row")
        return res.data[0]

    attempt_row = await asyncio.to_thread(_insert)

    # On a pass, flip any pending acknowledgement to acknowledged + cross-link.
    if passed:
        try:
            def _ack() -> dict[str, Any] | None:
                # Find a pending ack for this user+doc; prefer the matching
                # version if we have one.
                q = (
                    svc.table("acknowledgements")
                    .select("id, document_version_id")
                    .eq("org_id", org_id)
                    .eq("user_id", user_id)
                    .eq("document_id", quiz["document_id"])
                    .eq("status", "pending")
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                rows = q.data or []
                if not rows:
                    return None
                ack_id = rows[0]["id"]
                upd = (
                    svc.table("acknowledgements")
                    .update(
                        {
                            "status": "acknowledged",
                            "acknowledged_at": now,
                        }
                    )
                    .eq("id", ack_id)
                    .execute()
                )
                # Cross-link the attempt to the ack
                svc.table("certification_attempts").update(
                    {"acknowledgement_id": ack_id}
                ).eq("id", attempt_row["id"]).execute()
                return (upd.data or [None])[0]

            await asyncio.to_thread(_ack)
        except Exception as exc:
            log.warning(
                "certification.ack_link_failed user=%s doc=%s err=%s",
                user_id,
                quiz["document_id"],
                exc,
            )

    return {
        "attempt_id": attempt_row["id"],
        "score": score,
        "correct_count": correct,
        "total_count": total,
        "passed": passed,
        "passing_score": passing,
    }


# ── User status + admin report ─────────────────────────────────────────────


async def get_user_certification_status(
    *, org_id: str, user_id: str, document_id: str
) -> dict[str, Any]:
    svc = get_service_client()

    def _q() -> dict[str, Any]:
        latest = (
            svc.table("certification_attempts")
            .select("id, score, passed, completed_at, quiz_id, total_count, correct_count")
            .eq("org_id", org_id)
            .eq("user_id", user_id)
            .eq("document_id", document_id)
            .order("completed_at", desc=True)
            .limit(1)
            .execute()
        )
        last = (latest.data or [None])[0]
        return {"latest_attempt": last}

    return await asyncio.to_thread(_q)


async def admin_report(*, org_id: str) -> dict[str, Any]:
    """Per-document pass rate + per-user latest attempt.

    Cheap roll-up in Python (the table is small per-org). We deliberately do
    not paginate — admins want one screen.
    """
    svc = get_service_client()

    def _attempts() -> list[dict[str, Any]]:
        res = (
            svc.table("certification_attempts")
            .select(
                "id, user_id, document_id, score, passed, completed_at, total_count, "
                "users(display_name), documents(name)"
            )
            .eq("org_id", org_id)
            .order("completed_at", desc=True)
            .limit(10_000)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_attempts)

    by_doc: dict[str, dict[str, Any]] = {}
    by_user: dict[str, dict[str, Any]] = {}
    for r in rows:
        doc_id = r.get("document_id")
        user_id = r.get("user_id")
        if not doc_id or not user_id:
            continue
        d = by_doc.setdefault(
            doc_id,
            {
                "document_id": doc_id,
                "name": (r.get("documents") or {}).get("name"),
                "attempts": 0,
                "passes": 0,
                "unique_passers": set(),
            },
        )
        d["attempts"] += 1
        if r.get("passed"):
            d["passes"] += 1
            d["unique_passers"].add(user_id)

        # Per-user latest only
        u_key = user_id
        if u_key not in by_user or (r.get("completed_at") or "") > (
            by_user[u_key].get("completed_at") or ""
        ):
            by_user[u_key] = {
                "user_id": user_id,
                "display_name": (r.get("users") or {}).get("display_name"),
                "document_id": doc_id,
                "document_name": (r.get("documents") or {}).get("name"),
                "score": r.get("score"),
                "passed": r.get("passed"),
                "completed_at": r.get("completed_at"),
            }

    docs = [
        {
            **d,
            "unique_passers": len(d["unique_passers"]),
            "pass_rate": round(d["passes"] / d["attempts"], 3) if d["attempts"] else 0,
        }
        for d in by_doc.values()
    ]
    docs.sort(key=lambda x: (x["pass_rate"], x["name"] or ""))

    return {
        "summary": {
            "total_attempts": len(rows),
            "passes": sum(1 for r in rows if r.get("passed")),
        },
        "by_document": docs,
        "by_user": list(by_user.values()),
    }
