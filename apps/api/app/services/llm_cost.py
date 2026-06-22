"""LLM cost tracking — per-turn token + cost accumulation.

Sits between the LLM client (which knows how many tokens a single call used)
and the query_logs writer (which persists one row per chat turn). A chat turn
can issue 2-5 LLM calls (tool rounds + the final generation), each with its
own token counts. We sum them into a ContextVar that the writer drains at the
end of the turn.

Why a ContextVar (not threadlocal / global): the orchestrator runs inside an
asyncio task and may interleave with concurrent chats serving other users.
ContextVar is the only mechanism that gives us per-task isolation without
threading state through every call site.

Pricing source of truth: the `MODEL_PRICING` dict below. Updated alongside
provider price changes. Costs are stored in micros (USD * 1e6) so the
query_logs writer never sees floats.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass

log = logging.getLogger(__name__)


# ── Model pricing ────────────────────────────────────────────────────────────
#
# (input_per_1m_tokens_usd, output_per_1m_tokens_usd). Quoted from each
# provider's public pricing page; bumping these is a one-line edit when a
# provider re-prices. Unknown models fall back to a conservative GPT-4o-ish
# estimate so we never under-report cost.
#
# Refresh checklist (do this each quarter):
#   * Gemini:  https://ai.google.dev/pricing
#   * OpenAI:  https://openai.com/api/pricing/
#   * Claude:  https://www.anthropic.com/pricing
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # ── Google Gemini ──
    # 2.0 Flash family
    "gemini-2.0-flash":            (0.10,  0.40),
    "gemini-2.0-flash-001":        (0.10,  0.40),
    "gemini-2.0-flash-lite":       (0.075, 0.30),
    # 2.5 Flash family
    "gemini-2.5-flash":            (0.30,  2.50),
    "gemini-2.5-flash-lite":       (0.10,  0.40),
    "gemini-2.5-pro":              (1.25,  10.00),
    # 3.x Flash (our default at the time of writing)
    "gemini-3.1-flash-lite":       (0.10,  0.40),
    "gemini-3.0-flash":            (0.30,  2.50),
    # Embedding (text-embedding) — input only, output cost is 0
    "gemini-embedding-001":        (0.15,  0.00),
    "text-embedding-004":          (0.025, 0.00),

    # ── OpenAI ──
    "gpt-4o":                      (2.50,  10.00),
    "gpt-4o-mini":                 (0.15,  0.60),
    "gpt-4-turbo":                 (10.00, 30.00),
    "gpt-4":                       (30.00, 60.00),
    "gpt-3.5-turbo":               (0.50,  1.50),
    "text-embedding-3-small":      (0.02,  0.00),
    "text-embedding-3-large":      (0.13,  0.00),

    # ── Anthropic Claude ──
    "claude-3-5-sonnet":           (3.00,  15.00),
    "claude-3-5-sonnet-20241022":  (3.00,  15.00),
    "claude-3-5-haiku":            (0.80,  4.00),
    "claude-3-haiku":              (0.25,  1.25),
    "claude-3-opus":               (15.00, 75.00),
    "claude-sonnet-4-5":           (3.00,  15.00),
    "claude-sonnet-4-6":           (3.00,  15.00),
    "claude-opus-4-7":             (15.00, 75.00),
    "claude-haiku-4-5":            (0.80,  4.00),
}

# Used when the model name doesn't match any prefix. Errs slightly high so we
# never silently under-account for spend on a newly-added model.
_FALLBACK_PRICING = (1.00, 5.00)


def _lookup_pricing(model: str) -> tuple[float, float]:
    """Match by exact name first, then by longest prefix. Newer providers ship
    dated suffixes (`gemini-2.5-flash-001`, `claude-3-5-sonnet-20241022`); the
    prefix match keeps us correct when a deploy bumps the dated tag."""
    if not model:
        return _FALLBACK_PRICING
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    best: tuple[str, tuple[float, float]] | None = None
    for key, prices in MODEL_PRICING.items():
        if model.startswith(key) and (best is None or len(key) > len(best[0])):
            best = (key, prices)
    if best is not None:
        return best[1]
    # Don't log every cache miss — only when we genuinely don't know the model.
    log.info("llm_cost_unknown_model", extra={"model": model})
    return _FALLBACK_PRICING


def calculate_cost_micros(model: str, input_tokens: int, output_tokens: int) -> int:
    """Returns cost in USD * 1_000_000 (integer micros).

    The hot path stores costs as BIGINT micros to avoid float arithmetic when
    aggregating millions of rows in the dashboard.
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return 0
    in_price, out_price = _lookup_pricing(model)
    # Pricing dict is per-1M-tokens. Multiply tokens by price, divide by 1M,
    # convert to micros (× 1e6) — the two factors cancel.
    cost_usd = (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return max(0, int(round(cost_usd * 1_000_000)))


def micros_to_usd(micros: int) -> float:
    """Inverse of calculate_cost_micros, for UI display."""
    return micros / 1_000_000


# ── Per-turn usage accumulator ───────────────────────────────────────────────


@dataclass
class TurnUsage:
    """Mutable summing pot for one chat turn. Captures token totals + a
    per-model breakdown so the dashboard can show "gemini-flash: 2,300 tok,
    embedding-001: 800 tok" alongside the headline cost."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_micros: int = 0
    # Last model used in the turn — what query_logs.model_used records.
    # We pick "last" deliberately: the final generation is what the user sees,
    # and that's the one we want surfaced in /history. Earlier rounds are
    # always the same model anyway (we don't mix providers per turn).
    last_model: str | None = None
    # Optional fine-grained breakdown for the founder dashboard. Map of
    # model_name → (in_tokens, out_tokens, cost_micros). Empty in dev.
    per_model: dict[str, tuple[int, int, int]] | None = None

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        if input_tokens < 0 or output_tokens < 0:
            return
        cost = calculate_cost_micros(model, input_tokens, output_tokens)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_micros += cost
        if model:
            self.last_model = model
            bucket = (self.per_model or {}).get(model, (0, 0, 0))
            updated = (
                bucket[0] + input_tokens,
                bucket[1] + output_tokens,
                bucket[2] + cost,
            )
            if self.per_model is None:
                self.per_model = {}
            self.per_model[model] = updated


# ContextVar default of None — callers MUST start_turn() before reads, or
# accept that `current_turn()` returns None (which means "no accumulator
# bound, drop the usage on the floor"). This is the right behaviour: we
# don't want background jobs that call LLMs to leak into a chat turn.
_current_turn: ContextVar[TurnUsage | None] = ContextVar("llm_turn_usage", default=None)


def start_turn() -> TurnUsage:
    """Bind a fresh TurnUsage to the current async task. Returns the same
    object that record_usage() will mutate. Call this once per chat turn,
    before any LLM call is issued."""
    usage = TurnUsage()
    _current_turn.set(usage)
    return usage


def end_turn() -> TurnUsage | None:
    """Detach + return the current turn's usage. Idempotent; further reads
    return None until the next start_turn(). Callers should pass the result
    to the query_logs writer."""
    usage = _current_turn.get()
    _current_turn.set(None)
    return usage


def current_turn() -> TurnUsage | None:
    """Read-only access to the bound TurnUsage, if any."""
    return _current_turn.get()


def record_usage(model: str, input_tokens: int | None, output_tokens: int | None) -> None:
    """Called from inside the LLM client after each provider call. No-op if
    no turn is bound (e.g. background agent flows that don't write to
    query_logs) or if neither token count was reported.

    Safe to call with None for either token count — provider responses
    occasionally omit one or the other when a stream is interrupted.
    """
    usage = _current_turn.get()
    if usage is None:
        return
    in_t = int(input_tokens or 0)
    out_t = int(output_tokens or 0)
    if in_t == 0 and out_t == 0:
        return
    usage.add(model, in_t, out_t)


__all__ = [
    "MODEL_PRICING",
    "TurnUsage",
    "calculate_cost_micros",
    "current_turn",
    "end_turn",
    "micros_to_usd",
    "record_usage",
    "start_turn",
]
