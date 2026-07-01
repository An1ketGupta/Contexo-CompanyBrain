---
title: Billing and plan limits
category: Account
order: 8
tags: [billing, plans, quota, upgrade, pricing, payment, invoice]
---

Nirnaya IQ has three plans. You can see your current plan and usage on **Settings → Billing**.

## Plans

| Plan | Price (monthly) | Users | Documents | Queries / month |
|------|-----------------|-------|-----------|-----------------|
| **Starter** | $49 | 10 | 100 | 500 |
| **Team** | $129 | 30 | 1,000 | 3,000 |
| **Business** | $299 | Unlimited | Unlimited | Unlimited |

**Annual billing** saves around 20%. **Quarterly** saves about 10%. Switch at any time from **Settings → Billing**.

## What counts as a "query"?

Every message you send in chat counts as one query, whether it's a quick lookup or a long generation. Search inside the **Documents** page doesn't count; neither do automated retries or webhook calls.

API requests count against your quota the same way an in-app message would.

## What happens at the limit?

- **Documents** — uploading is blocked until you delete some or upgrade. Existing documents keep working.
- **Queries** — chat is blocked for the rest of the billing period. Admins can upgrade mid-cycle to restore access immediately.
- **Users** — new invites are blocked. Existing members keep their access.

You'll see banners in the app starting at 80% of any limit, and an email at 100%.

## Upgrade, downgrade, switch

**Settings → Billing → Change plan**. Upgrades take effect immediately and are prorated. Downgrades take effect at the end of the current period so you don't lose what you already paid for.

If a downgrade would push you over a new lower limit (e.g., 200 documents → Starter's 100), we'll keep the existing documents accessible but block new uploads until you're under the limit.

## Invoices & receipts

Every invoice is emailed to the billing contact and stored under **Settings → Billing → Invoices**. To change the email or add a VAT number, edit the **Billing details** card on that page.

## Cancel

**Settings → Billing → Cancel subscription**. You keep access until the end of the period you've paid for. Your data is retained for 30 days in case you change your mind; after that, documents and chats are permanently deleted.

## Discounts

Startups under 12 months old and registered nonprofits get 50% off the Team plan for the first year. Email `billing@companybrain.app` with a short note about your company to apply.
