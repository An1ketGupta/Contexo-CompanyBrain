# Slack App Configuration

One-time setup for the Slack integration. After this is done, individual orgs install via the **Add to Slack** button in `/settings/integrations` — they don't touch any of this.

This doc covers both **outbound** (post messages, `/brain` slash command) and **inbound** (pin/canvas/file ingest), which share a single Slack app config.

---

## 1. Create the Slack app

Go to <https://api.slack.com/apps> → **Create New App** → **From an app manifest** → select your dev/prod workspace.

Paste the manifest below. Replace `https://api.nirnayaiq.com` with your backend host (or `https://<tunnel>.ngrok-free.app` for local dev).

```yaml
display_information:
  name: NirnayaIQ
  description: Your company's brain — ask anything, get answers grounded in your docs.
  background_color: "#1f2937"
features:
  bot_user:
    display_name: NirnayaIQ
    always_online: true
  slash_commands:
    - command: /brain
      url: https://api.nirnayaiq.com/slack/events
      description: Ask your company's brain
      usage_hint: what's our refund policy?
      should_escape: false
    - command: /ani
      url: https://api.nirnayaiq.com/slack/events
      description: NirnayaIQ — alias for /brain
      should_escape: false
oauth_config:
  redirect_urls:
    - https://api.nirnayaiq.com/integrations/slack/callback
  scopes:
    bot:
      - chat:write          # post answers + outputs
      - commands            # /brain slash command
      - channels:read       # list public channels (picker)
      - channels:history    # read pinned message text
      - groups:read         # list private channels bot is in
      - groups:history      # read pinned message text in private channels
      - pins:read           # pin enumeration
      - files:read          # download channel files
      - users:read          # humanize author IDs
      - canvases:read       # canvas ingest (paid plans only; Free plans silently drop this)
settings:
  event_subscriptions:
    request_url: https://api.nirnayaiq.com/slack/events
    bot_events:
      - pin_added
      - pin_removed
      - message.channels
      - message.groups
      - file_shared
      - member_joined_channel
  interactivity:
    is_enabled: true
    request_url: https://api.nirnayaiq.com/slack/interactions
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

> **Canvas events**: Slack's `canvas_*` event family is still rolling out for third-party apps. We poll-detect canvases at backfill time, so missing canvas events doesn't break the flow — they just become eventually-consistent rather than real-time.

### When Slack asks to "Reinstall App"

Anytime you change scopes or event subscriptions, Slack invalidates existing installs. After the manifest is saved:

1. **Install to Workspace** in the dev workspace to verify the manifest is valid.
2. Every customer org will need to **Reconnect Slack** from their integrations page. The `/integrations/status` endpoint exposes `slack.scopes_complete=false` so the UI shows a yellow "Reconnect" banner automatically.

---

## 2. Backend env vars

In `apps/api/.env`:

```
SLACK_CLIENT_ID=<from Basic Information → App Credentials>
SLACK_CLIENT_SECRET=<same screen>
SLACK_SIGNING_SECRET=<same screen, used for HMAC verification>
SLACK_OAUTH_REDIRECT_URI=https://api.nirnayaiq.com/integrations/slack/callback
```

The redirect URI must match the manifest exactly (Slack is strict).

---

## 3. Verify the events URL

After saving the manifest, Slack will hit your `request_url` with a `url_verification` challenge. Our handler at `apps/api/app/routers/slack_router.py:slack_events` echoes the challenge automatically — if you see a green checkmark next to the URL in Slack's app config, you're good.

If it fails, common causes:
- `SLACK_SIGNING_SECRET` doesn't match the workspace.
- `https://` is required by Slack (no http even for local). Use ngrok or Cloudflare Tunnel.
- The challenge endpoint is behind auth — we explicitly *don't* require JWT on `/slack/events`.

---

## 4. Local development with ngrok

Slack requires HTTPS for both the OAuth callback and the events URL. For local dev:

```bash
# Terminal 1: API
uv run uvicorn app.main:app --reload --port 8000

# Terminal 2: Inngest dev server
npx inngest-cli@latest dev

# Terminal 3: ngrok tunnel
ngrok http 8000
```

Then update the manifest URLs to your `https://<random>.ngrok-free.app/...` host and reinstall the app to your dev workspace.

---

## 5. End-to-end test

Once the manifest is saved and an org has installed:

1. In Slack, invite the bot to a channel: `/invite @NirnayaIQ` in `#test-channel`.
2. In the NirnayaIQ web app, open `/settings/integrations` → Slack card → **Add channel** → pick `#test-channel`.
3. Within ~30 seconds, the Inngest dashboard at `http://localhost:8288` should show:
   - `slack-backfill-channel` succeed
   - Per-pin `slack-ingest-pin` jobs
   - Per-file `slack-ingest-file` jobs
4. In `/documents`, filter by `source = slack` to see the ingested rows.
5. Pin a new message in `#test-channel`. The `slack-process-event` function should fire within seconds → `slack-ingest-pin` → new document.
6. Unpin → existing document status flips to `archived` (no longer searchable).

---

## 6. Production checklist

Before flipping the manifest URLs to prod:

- [ ] `SLACK_SIGNING_SECRET` in Railway env matches the prod app credentials
- [ ] OAuth redirect URI matches Railway's public hostname
- [ ] Inngest production environment has all `slack-*` functions registered (auto on backend boot, but verify in the Inngest dashboard)
- [ ] Existing org installs are flagged for reconnect (the migration sets `scopes='{}'` for all rows, which the status endpoint reads as `scopes_complete=false`)
- [ ] Rate-limit headroom: with N orgs, each `pin_added` event burns 2 Slack API calls (`conversations.history` + `users.info`). The Inngest per-team concurrency=1 cap means each team is sequential, so you're well under Slack's tier-3 50/min budget per workspace.

---

## 7. Slack rate-limit reference

| API | Tier | Limit | Used by |
|---|---|---|---|
| `pins.list` | 3 | 50/min | `slack_inbound.fetch_pins` |
| `conversations.history` | 3 | 50/min | `fetch_message`, `fetch_thread` |
| `conversations.replies` | 3 | 50/min | `fetch_thread` |
| `conversations.info` | 4 | 100/min | `fetch_channel_info`, `fetch_channel_canvas_id` |
| `conversations.list` | 2 | 20/min | `list_bot_channels` |
| `files.info` | 4 | 100/min | `fetch_file_info` |
| `files.list` | 2 | 20/min | `fetch_files` (backfill) |
| `users.info` | 4 | 100/min | `resolve_user` (cached 12h) |

The `concurrency=1` per team_id Inngest cap on every inbound function means even an extreme burst (admin enables 20 channels at once) stays well under any tier limit. If we ever exceed, the wrappers raise `SlackRateLimit` carrying the precise `Retry-After`, and Inngest retries with backoff.

---

## 8. Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Reconnect Slack" banner shows after a fresh install | App manifest is missing one of the required scopes — re-check the `bot:` list. |
| `/brain` slash command shows "🧠 Thinking…" forever | Background task crashed; check API logs. The slash command path is unchanged from before the inbound work, so this is unrelated. |
| Subscriptions endpoint returns `bot_not_in_channel` | Admin needs to `/invite @NirnayaIQ` to the channel first. We deliberately don't auto-join (privacy). |
| Pin events don't fire | Slack's event subscription URL verification probably failed — check the green checkmark in the Slack app config. |
| Canvas ingest silently skipped | Workspace is on Free/Pro plan (no canvas API); `slack.has_canvas_scope` in status will be false. |
| Many "slack_files_list_failed" warnings | Bot lacks `files:read` scope — workspace owner needs to reinstall. |
