# Company Brain — Pricing & Margin Analysis

> **Product type:** Work execution platform (AI task outputs grounded in company knowledge)
> **Usage unit:** AI task output (one completed email, job description, Slack message, summary, etc.)
> **Last updated:** 2026-06-06

---

## Plans at a Glance

| Plan | Monthly | Quarterly | Annual | Users | Docs | Tasks/month |
|------|---------|-----------|--------|-------|------|-------------|
| **Starter** | $59 | $159 | $564 | 10 | 200 | 500 |
| **Growth** | $149 | $402 | $1,428 | 40 | 1,000 | 2,500 |
| **Business** | $349 | $942 | $3,348 | Unlimited | Unlimited | Unlimited |

---

## Cost Structure

### What goes into every AI task output

Each task (e.g. "write an email to a client about our refund policy") triggers:

```
1. LLM first call — decide what to search
   Input:  ~500 tokens (system prompt + user message)
   Output: ~100 tokens (tool call JSON: 2–4 search queries)

2. Search execution (parallel) — 3 searches average
   Our cost: $0 (runs on our Postgres pgvector — infra already paid)

3. LLM final call — generate output with retrieved context
   Input:  ~3,500 tokens (system prompt + user msg + retrieved chunks)
   Output: ~500 tokens (the actual email / JD / Slack message)

Total per task:  ~4,000 tokens input  +  ~600 tokens output
```

### LLM cost per task by provider

| Provider | Model | Input cost/1M | Output cost/1M | Cost per task |
|----------|-------|--------------|----------------|---------------|
| Google | Gemini 2.0 Flash | **FREE** | **FREE** | **$0.000** |
| Anthropic | Claude 3.5 Haiku | $0.80 | $4.00 | **$0.006** |
| Anthropic | Claude 3.5 Sonnet | $3.00 | $15.00 | **$0.021** |
| Blended | 70% Haiku + 30% Sonnet | — | — | **$0.011** |

> **Routing strategy (paid tier):** Simple tasks (short answers, quick summaries) → Haiku. Complex tasks (full emails, job descriptions, multi-doc synthesis) → Sonnet. This gives quality where it matters at 60% lower average cost vs Sonnet-only.

### Infrastructure fixed costs

| Phase | Monthly Fixed Cost | What's Included |
|-------|-------------------|-----------------|
| **Free tier** (0–8 customers) | **$0** | Vercel Hobby, Railway free credit, Supabase free |
| **Paid tier** (8+ customers) | **~$100/month** | Vercel Pro $20 + Railway $30 + Supabase Pro $25 + R2 $5 + Upstash $10 + misc $10 |

Fixed cost per customer drops as you scale:
- 10 customers: $10/customer/month
- 20 customers: $5/customer/month
- 50 customers: $2/customer/month

---

## Unit Economics Per Plan

### On FREE tier (Gemini, 0–8 customers)

| Plan | Price | LLM cost | Infra | Total cost | Profit | **Margin** |
|------|-------|----------|-------|------------|--------|------------|
| Starter | $59 | $0 | $0 | $0 | $59 | **~97%** |
| Growth | $149 | $0 | $0 | $0 | $149 | **~97%** |
| Business | $349 | $0 | $0 | $0 | $349 | **~97%** |

> The ~3% comes out of Stripe processing fees (2.9% + $0.30/transaction). Everything else is free.
> Gemini's free tier handles up to ~12,000 tasks/month — enough for 10+ customers combined.

---

### On PAID tier (Claude blended, 10+ customers)

Assumptions:
- Blended LLM cost: **$0.011/task**
- Fixed infra per customer at 10 customers: **$10/month**
- Fixed infra per customer at 20 customers: **$5/month**
- Stripe fees: ~3%

**Starter plan ($59/month, 500 tasks):**
```
Revenue:                    $59.00
Stripe fee (3%):           - $1.77
LLM cost (500 × $0.011):   - $5.50
Infra allocation:          - $10.00  (at 10 customers)
                           ─────────
Net per customer:           $41.73
Gross margin:               70.7%
```

**Growth plan ($149/month, 2,500 tasks):**
```
Revenue:                     $149.00
Stripe fee (3%):            -  $4.47
LLM cost (2,500 × $0.011):  - $27.50
Infra allocation:           - $10.00
                            ─────────
Net per customer:            $107.03
Gross margin:                71.8%
```

**Business plan ($349/month, ~5,000 tasks estimated):**
```
Revenue:                     $349.00
Stripe fee (3%):            -  $10.47
LLM cost (5,000 × $0.011):  - $55.00
Infra allocation:           - $10.00
                            ─────────
Net per customer:            $273.53
Gross margin:                78.4%
```

---

## Revenue & Profit Scenarios

### Scenario 1: Month 1 — 2 customers (likely both Growth)

```
2 × Growth ($149):           $298/month revenue

LLM: Gemini free tier        $0  (still within free limits)
Infra:                       $0  (still on free tier)
Stripe fees:                 $9

Net profit:                  $289/month
Margin:                      97%
```

### Scenario 2: Month 3 — 8 customers

Mix: 3 Starter + 3 Growth + 2 Business

```
Revenue:
  3 × $59   =  $177
  3 × $149  =  $447
  2 × $349  =  $698
Total:          $1,322/month

LLM cost (still mostly Gemini, on edge):   ~$20
Infra (crossing into paid tier):           $100
Stripe fees:                               $40

Total costs:    $160/month
Net profit:     $1,162/month
Margin:         87.9%
```

### Scenario 3: Month 6 — 20 customers

Mix: 8 Starter + 8 Growth + 4 Business

```
Revenue:
  8 × $59   =  $472
  8 × $149  =  $1,192
  4 × $349  =  $1,396
Total:          $3,060/month

LLM cost (Claude blended):
  8 × 500 tasks × $0.011   =  $44
  8 × 2,500 tasks × $0.011 =  $220
  4 × 5,000 tasks × $0.011 =  $220
Total LLM:                    $484/month

Infra fixed:    $100/month
Stripe fees:    $92/month

Total costs:    $676/month
Net profit:     $2,384/month
Margin:         77.9%
```

### Scenario 4: Month 12 — 50 customers

Mix: 20 Starter + 20 Growth + 10 Business

```
Revenue:
  20 × $59  =  $1,180
  20 × $149 =  $2,980
  10 × $349 =  $3,490
Total:          $7,650/month  (~$91,800/year ARR)

LLM cost:
  20 × 500 × $0.011   =  $110
  20 × 2,500 × $0.011 =  $550
  10 × 5,000 × $0.011 =  $550
Total LLM:               $1,210/month

Infra fixed:   $100/month
Stripe fees:   $230/month

Total costs:   $1,540/month
Net profit:    $6,110/month  (~$73,320/year)
Margin:        79.9%
```

---

## Margin Summary

| Stage | Customers | Monthly Revenue | Monthly Profit | **Margin** |
|-------|-----------|-----------------|----------------|------------|
| Pre-revenue | 0 | $0 | $0 | — |
| First customer | 1 Growth | $149 | $145 | 97% |
| Break-even | 2 Growth | $298 | $289 | 97% |
| Early traction | 8 mix | $1,322 | $1,162 | 88% |
| Growth stage | 20 mix | $3,060 | $2,384 | 78% |
| Scale | 50 mix | $7,650 | $6,110 | 80% |

**Bottom line: 75–97% gross margin depending on stage.**
This is exceptional for a SaaS. Most SaaS businesses target 70–80%. You're at the top of that range.

---

## Why the Margin is This High

1. **LLM costs are tiny.** Each task costs ~1 cent on a blended basis. You're charging $0.12–0.14 per task (Starter) or $0.06 per task (Growth). **6–12x markup on the raw AI cost.**

2. **No human labor in delivery.** Once built, serving one more customer costs essentially nothing extra.

3. **Infrastructure doesn't scale linearly.** Your fixed $100/month serves 8 customers or 80 customers. As you add customers, the per-customer infra allocation shrinks to near zero.

4. **Free tier buys you 8–10 customers at $0 LLM cost.** Your margin in the early months is nearly pure profit.

---

## What Kills the Margin (Watch For)

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Business plan customer using 20,000+ tasks/month | LLM cost exceeds plan price | Add overage fee ($0.05/task over limit) or fair use policy |
| All customers on Sonnet (no Haiku routing) | Cost 2× higher than modeled | Implement task complexity routing on Day 25 |
| 10 customers all on Starter | Revenue too low, LLM still scales | Upsell 2 customers to Growth = solves it |
| LLM price increase (Anthropic raises prices) | Higher variable costs | Gemini free tier as fallback; pass increase via annual price adjustment |

---

## Overage Pricing (add when customers hit limits)

Don't charge overage in v1 — it creates friction. When a customer consistently exceeds limits, use it as a sales conversation to upgrade. Only add overage billing at 20+ customers when the manual upgrade conversation doesn't scale.

Future overage rate: **$0.05 per task output over plan limit** (still ~5× markup on cost).

---

## Annual vs Monthly Mix Impact

If 30% of customers pay annually (20% discount), revenue recognition changes but cash flow improves dramatically:

At 20 customers, if 6 pay annually:
```
Monthly payers (14):   ~$2,150/month
Annual payers (6):     ~$1,800 collected upfront per customer
Annual cash collected: ~$10,800 in month 1 from annual customers
```

Annual billing is a cash flow superpower for a bootstrapped startup. Push it on the landing page.

---

*Last updated: 2026-06-06*
