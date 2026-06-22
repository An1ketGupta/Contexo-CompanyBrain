"""Slack Block Kit composition helpers.

These builders produce the dict shapes Slack's chat.postMessage / response_url
accept. Kept as pure functions (no I/O) so the slash-command + interactions
handlers can compose blocks without pulling the network layer.

Convention for action_ids: `<verb>_<noun>_<entity_id>` so the interactions
router can route by prefix match (verb_noun) and recover the entity from the
action_id payload without a database round-trip first.
"""
from __future__ import annotations

from typing import Any

# Slack hard-caps mrkdwn text at 3000 chars per block. We leave headroom for
# code-fence wrappers + the surrounding line.
_DRAFT_TEXT_CAP = 2700


def _truncate(text: str, cap: int = _DRAFT_TEXT_CAP) -> str:
    if len(text) <= cap:
        return text
    return text[: cap - 1] + "…"


def email_draft_blocks(
    *,
    draft_body: str,
    message_id: str,
    subject: str | None = None,
    recipient_hint: str | None = None,
    confidence_label: str | None = None,  # "high" | "medium" | "low"
    confidence_score: float | None = None,
) -> list[dict[str, Any]]:
    """Card surfaced when /brain draft email runs from Slack.

    The Send button posts to /slack/interactions with action_id
    `send_email_<message_id>`. The Edit button opens a modal (handled in a
    follow-up day — Day 1 only wires the dispatch table).
    """
    header_bits: list[str] = ["*Email draft ready*"]
    if subject:
        header_bits.append(f"_Subject:_ {subject}")
    if recipient_hint:
        header_bits.append(f"_To:_ {recipient_hint}")
    if confidence_label and confidence_score is not None:
        emoji = (
            "🟢" if confidence_label == "high"
            else "🟡" if confidence_label == "medium"
            else "🔴"
        )
        header_bits.append(f"{emoji} {confidence_label.title()} confidence ({confidence_score:.1f}/10)")
    header_text = "\n".join(header_bits)

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```{_truncate(draft_body)}```"},
        },
        {
            "type": "actions",
            "block_id": f"email_draft_actions_{message_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Send via Gmail"},
                    "style": "primary",
                    "action_id": f"send_email_{message_id}",
                    "value": message_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit first"},
                    "action_id": f"edit_email_{message_id}",
                    "value": message_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Discard"},
                    "style": "danger",
                    "action_id": f"discard_email_{message_id}",
                    "value": message_id,
                },
            ],
        },
    ]


def slack_post_blocks(
    *,
    draft_body: str,
    message_id: str,
    target_channel_hint: str | None = None,
) -> list[dict[str, Any]]:
    """Card for posting AI-generated content into another Slack channel."""
    headline = "*Post to Slack*"
    if target_channel_hint:
        headline += f"  ·  _{target_channel_hint}_"
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```{_truncate(draft_body)}```"},
        },
        {
            "type": "actions",
            "block_id": f"slack_post_actions_{message_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Post"},
                    "style": "primary",
                    "action_id": f"post_slack_{message_id}",
                    "value": message_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Discard"},
                    "style": "danger",
                    "action_id": f"discard_slack_{message_id}",
                    "value": message_id,
                },
            ],
        },
    ]


def ephemeral_text(text: str) -> dict[str, Any]:
    """A minimal Slack `response_url`-compatible payload."""
    return {"response_type": "ephemeral", "text": _truncate(text, 2700)}


# ── Agent Day 6: approval request DM ────────────────────────────────────────


def approval_request_blocks(
    *,
    approval_id: str,
    requester_name: str | None,
    channel: str,
    destination: str | None,
    preview_text: str | None,
    web_url: str,
) -> list[dict[str, Any]]:
    """Card surfaced when a requester pings an approver via Slack DM.

    Approve/Reject buttons hit the existing /slack/interactions handler.
    `action_id` format `approve_request_<approval_id>` / `reject_request_<approval_id>`
    is parsed by prefix → entity-id, matching the dispatch convention used
    elsewhere in this file (see email_draft_blocks).
    """
    channel_label = {
        "gmail": "Send via Gmail",
        "slack": "Post to Slack",
        "notion": "Create Notion page",
        "gdocs": "Create Google Doc",
    }.get(channel, channel.title())

    header_bits = ["*Approval requested*"]
    if requester_name:
        header_bits.append(f"_From:_ {requester_name}")
    header_bits.append(f"_Action:_ {channel_label}")
    if destination:
        header_bits.append(f"_Destination:_ {destination}")
    header = "\n".join(header_bits)

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "divider"},
    ]
    if preview_text:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```{_truncate(preview_text)}```"},
            }
        )
    blocks.append(
        {
            "type": "actions",
            "block_id": f"approval_actions_{approval_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": f"approve_request_{approval_id}",
                    "value": approval_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": f"reject_request_{approval_id}",
                    "value": approval_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open in browser"},
                    "action_id": f"open_approval_{approval_id}",
                    "url": web_url,
                },
            ],
        }
    )
    return blocks


def approval_resolved_blocks(
    *,
    requester_name: str | None,
    channel: str,
    action: str,  # 'approved' | 'rejected'
    approver_name: str | None,
    note: str | None,
) -> list[dict[str, Any]]:
    """Replacement card after the approver clicks a button — removes the
    buttons and shows the resolution + optional note. Posted via chat.update
    so the DM mutates in place rather than spawning a thread."""
    emoji = "✅" if action == "approved" else "🚫"
    verb = "Approved" if action == "approved" else "Rejected"
    channel_label = {
        "gmail": "Send via Gmail",
        "slack": "Post to Slack",
        "notion": "Create Notion page",
        "gdocs": "Create Google Doc",
    }.get(channel, channel.title())

    bits = [f"{emoji} *{verb}*"]
    if approver_name:
        bits.append(f"_by_ {approver_name}")
    bits.append(f"_Action:_ {channel_label}")
    if requester_name:
        bits.append(f"_Requested by:_ {requester_name}")
    header = "\n".join(bits)

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
    ]
    if note:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"> {_truncate(note, 1500)}"},
            }
        )
    return blocks
