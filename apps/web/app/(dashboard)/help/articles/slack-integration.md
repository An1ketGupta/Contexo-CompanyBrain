---
title: Setting up the Slack integration
category: Integrations
order: 6
tags: [slack, bot, slash command, integration, dm, channels]
---

The Slack integration lets your team ask Company Brain anything without leaving Slack. It supports `/ask` slash commands and direct messages to the bot.

## Install

1. Go to **Settings → Integrations**.
2. Find the **Slack** card and click **Connect**.
3. Slack will prompt you to authorize the workspace. Approve the requested scopes (the bot reads messages it's mentioned in and posts replies — it does not read regular channel messages).
4. Pick a default channel for the bot to introduce itself in, or skip and just use DMs.

## Use it

**Slash command** — type `/ask` in any channel where the bot is invited:

```
/ask what is our refund policy?
```

The reply posts as an ephemeral message visible only to the asker, with citations.

**Direct message** — DM the bot like a coworker. The full conversation history is kept inside that DM.

**Mention** — `@Company Brain` in a thread will reply in-thread, so context stays with the conversation.

## Permissions

Slack users are matched to Company Brain users by email. If a Slack user doesn't have a Company Brain account, the bot will reply with an invite link instead of an answer — admins must add them to the workspace first.

The bot enforces the same role and document permissions as the web app: a Slack user can only retrieve documents they'd be allowed to see in the app.

## Disconnect

**Settings → Integrations → Slack → Disconnect**. The bot is removed from your Slack workspace and all stored tokens are deleted. Slack conversation history stays in Slack; Company Brain forgets the link.

## Troubleshooting

- **"This app isn't installed in this channel"** — invite the bot first with `/invite @Company Brain`.
- **Replies don't include citations** — citations need at least one retrieved chunk. If the question doesn't match any document, you'll get a plain reply with a "no sources" note.
- **The bot replies slowly** — Slack times slash commands out at 3 seconds. The bot acknowledges immediately and posts the full reply when ready, usually under 10 seconds.
