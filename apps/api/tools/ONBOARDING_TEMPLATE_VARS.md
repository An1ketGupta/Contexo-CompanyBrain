# Onboarding v2 — Template Variable Reference

This is the full list of placeholders you can use in your **Letter of Intent**, **Appointment Letter**, and **NDA** DOCX templates. Type the placeholder verbatim (with the double curly braces) anywhere in the document; the agent will fill it in when generating each PDF.

> **Validation is strict.** If you reference a variable that isn't in this list — typo or unsupported field — the run blocks with the exact missing name. Fix the template, re-upload, and the agent retries automatically. No silent `{{ unknown_var }}` will ever ship to a candidate.

---

## Candidate

| Placeholder | Type | Example | Notes |
| --- | --- | --- | --- |
| `{{ candidate_name }}` | text | `Aniket Gupta` | From the run row. |
| `{{ candidate_email }}` | text | `aniket@example.com` | |
| `{{ candidate_phone }}` | text | `+91 98xxxxxx00` | Empty string if not collected. |

## Role

| Placeholder | Type | Example | Notes |
| --- | --- | --- | --- |
| `{{ role_title }}` | text | `Senior Backend Engineer` | The internal-job title. |
| `{{ designation }}` | text | `Senior Engineer II` | Falls back to `role_title` if unset. |

## Compensation

| Placeholder | Type | Example | Notes |
| --- | --- | --- | --- |
| `{{ ctc }}` | text | `INR 24,00,000.00` | Pre-formatted with currency. |
| `{{ ctc_amount }}` | number | `2400000.0` | Raw numeric value; use for math. |
| `{{ ctc_currency }}` | text | `INR` | ISO-4217-ish; defaults to `INR`. |
| `{{ ctc_breakdown }}` | object | `{"base": 1800000, "variable": 600000}` | Free-form; reference subkeys via `{{ ctc_breakdown.base }}`. |

## Dates & location

| Placeholder | Type | Example | Notes |
| --- | --- | --- | --- |
| `{{ start_date }}` | text (YYYY-MM-DD) | `2026-08-01` | From the run row. |
| `{{ today_date }}` | text (YYYY-MM-DD) | `2026-06-28` | Rendered at generation time. |
| `{{ work_location }}` | text | `Bengaluru, KA` | |
| `{{ probation_period_months }}` | number | `3` | Use in conditional Jinja if needed. |

## Reporting line

| Placeholder | Type | Example | Notes |
| --- | --- | --- | --- |
| `{{ reporting_manager_name }}` | text | `Lakshmi Krishnan` | |
| `{{ reporting_manager_email }}` | text | `lakshmi@example.com` | |

## Company (pulled from `organizations` row)

| Placeholder | Type | Example | Notes |
| --- | --- | --- | --- |
| `{{ company_name }}` | text | `Acme Corp` | Display name. |
| `{{ company_legal_name }}` | text | `Acme Technologies Pvt Ltd` | Falls back to `company_name`. |
| `{{ company_address }}` | text | `91 MG Road, Bengaluru 560001` | Empty string if unset. |
| `{{ jurisdiction }}` | text | `India` | Default governing-law jurisdiction. |

---

## Jinja syntax

The renderer is full Jinja2 — you can use conditionals, loops, and filters in your DOCX:

```
{% if probation_period_months > 0 %}
You will be on probation for {{ probation_period_months }} months.
{% else %}
No probation period applies.
{% endif %}
```

Loops over `ctc_breakdown`:

```
{% for component, amount in ctc_breakdown.items() %}
- {{ component }}: {{ ctc_currency }} {{ amount }}
{% endfor %}
```

---

## Previewing your template

After tagging a DOCX as a template:

```
POST /api/onboarding/templates/{document_id}/preview
```

Returns a signed URL to a PDF rendered with **sample candidate data** so you can confirm the layout looks right before HR triggers a real onboarding.

---

## Editing existing templates

When you re-upload a template (new version in the KB), all **future** runs use the new version. In-progress runs keep using the version they started with — we snapshot `source_template_id` so a customer can't break a half-signed LOI by editing the master template.
