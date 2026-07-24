"""Meeting transcript parsers — Zoom WebVTT + Microsoft Teams JSON + Google
Meet plain text.

What's returned (canonical `MeetingTranscript`):

  * `format`      — 'zoom_vtt' | 'teams_json' | 'google_meet'
  * `utterances`  — ordered list of (speaker, text, start_ms, end_ms)
  * `speakers`    — deduped speaker labels in first-appearance order
  * `duration_ms` — last cue's end_ms, or None if unknown
  * `started_at`  — ISO timestamp parsed from the file header (Teams sets one;
                    Zoom doesn't), else None

Why parse to a structured shape rather than just rendering plain text:

  * The MeetingNotesAgent runs an LLM extraction step. Including speaker
    attribution massively improves the quality of action-item assignment
    ("Alice: I'll send the SOC2 evidence" → action item with owner=Alice).
  * The summary-derived document indexes utterances as
    `Speaker: text` lines, which keeps full-text search useful when somebody
    later asks "what did Alice say about SOC2".

Both parsers tolerate the most common real-world weirdness (BOMs, CRLFs,
missing speaker tags). They do NOT attempt OCR-grade VTT validation —
anything that doesn't look like a transcript returns an empty result and
the agent skips structured extraction.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

log = logging.getLogger(__name__)

TranscriptFormat = Literal["zoom_vtt", "teams_json", "google_meet", "unknown"]


@dataclass(frozen=True)
class ParsedUtterance:
    """Single speaker turn. Times in ms from the start of the meeting; None
    if the source didn't provide reliable cue timing."""

    speaker: str
    text: str
    start_ms: int | None = None
    end_ms: int | None = None


@dataclass(frozen=True)
class MeetingTranscript:
    format: TranscriptFormat
    utterances: tuple[ParsedUtterance, ...] = ()
    speakers: tuple[str, ...] = ()
    duration_ms: int | None = None
    started_at: str | None = None
    title: str | None = None

    def is_empty(self) -> bool:
        return not self.utterances

    def to_dialog_text(self, *, max_chars: int = 12_000) -> str:
        """Render utterances as `Speaker: text` lines, capped. Used by the
        agent to feed the LLM extraction prompt and as the body of the
        derived "Meeting summary" document."""
        out: list[str] = []
        total = 0
        for u in self.utterances:
            line = f"{u.speaker}: {u.text}".strip()
            if not line:
                continue
            if total + len(line) > max_chars:
                break
            out.append(line)
            total += len(line) + 1
        return "\n".join(out)


# ── Detection ───────────────────────────────────────────────────────────────

# Teams's transcript JSON has a top-level `recognizedPhrases` or `entries`
# array; if neither exists we treat it as a non-transcript JSON.
_TEAMS_TOP_KEYS = ("recognizedPhrases", "entries")


def detect_transcript_format(*, file_type: str | None, content: str) -> TranscriptFormat:
    """Cheap detection — extension wins, content sniffing is a fallback for
    the rare case a user uploads a transcript with the wrong extension."""
    ft = (file_type or "").lower()
    if ft == "vtt":
        return "zoom_vtt"
    if ft == "teams_transcript":
        return "teams_json"
    if ft == "transcript":
        return "google_meet"

    head = (content or "")[:400].lstrip("﻿").strip()
    if head.startswith("WEBVTT"):
        return "zoom_vtt"
    if head.startswith("{"):
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return "unknown"
        if isinstance(data, dict) and any(k in data for k in _TEAMS_TOP_KEYS):
            return "teams_json"
    return "unknown"


def parse_transcript(*, file_type: str | None, content: str) -> MeetingTranscript:
    """Dispatch to the right parser. Always returns a MeetingTranscript;
    `is_empty()` is true when the content didn't look like a transcript."""
    fmt = detect_transcript_format(file_type=file_type, content=content)
    if fmt == "zoom_vtt":
        return parse_zoom_vtt(content)
    if fmt == "teams_json":
        return parse_teams_transcript(content)
    if fmt == "google_meet":
        return parse_google_meet_transcript(content)
    return MeetingTranscript(format="unknown")


# ── Zoom WebVTT ─────────────────────────────────────────────────────────────

# VTT cue header:
#   00:00:01.500 --> 00:00:04.200        (hh:mm:ss.ttt)
#   00:01.500 --> 00:04.200              (mm:ss.ttt — the hours group is
#                                         optional per the WebVTT spec for
#                                         cues under an hour; some tooling and
#                                         non-Zoom exports emit this form)
# Optional cue identifier line above the timestamp (digits or any string).
_VTT_TS_RE = re.compile(
    r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{1,3})\s+-->\s+((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{1,3})"
)
# Speaker line: "Speaker Name: <text>". Zoom sometimes prefixes a <v Speaker>
# tag — strip both forms.
_VTT_VOICE_TAG_RE = re.compile(r"^<v\s+([^>]+)>\s*(.*?)(?:</v>)?\s*$", re.IGNORECASE)
_VTT_SPEAKER_RE = re.compile(r"^([A-Za-z][^:\n]{1,80}):\s*(.*)$")


def _parse_ts(ts: str) -> int:
    """`HH:MM:SS.sss` or `MM:SS.sss` → milliseconds."""
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, rest = parts
    elif len(parts) == 2:
        h, m, rest = "0", parts[0], parts[1]
    else:
        raise ValueError(f"unrecognized timestamp: {ts!r}")
    s, _, frac = rest.partition(".")
    ms_part = int((frac or "0").ljust(3, "0")[:3])
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + ms_part


def parse_zoom_vtt(content: str) -> MeetingTranscript:
    """Parse Zoom-style WebVTT.

    Tolerant of:
      * BOM and Windows line endings
      * Missing speaker labels (uses 'Unknown Speaker')
      * Multi-line cue text
      * <v Speaker>text</v> voice-tag variant
      * Cue identifier lines before timestamps
    """
    if not content:
        return MeetingTranscript(format="zoom_vtt")

    text = content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if not text.lstrip().startswith("WEBVTT"):
        # Not a real WebVTT file. Rather than bailing, fall back to the
        # bracketed-timestamp transcript form ("[00:00:24]\nSpeaker: text"),
        # which many tools export with a misleading `.vtt` extension. If that
        # finds nothing either, the empty result becomes EmptyDocumentError
        # upstream — same as genuine garbage.
        return _parse_bracketed_transcript(text)

    lines = text.split("\n")
    utterances: list[ParsedUtterance] = []
    speakers_order: list[str] = []
    speakers_seen: set[str] = set()
    duration_ms: int | None = None
    last_speaker: str = "Unknown Speaker"

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        ts_match = _VTT_TS_RE.match(line)
        if not ts_match:
            i += 1
            continue

        start_ms = _parse_ts(ts_match.group(1))
        end_ms = _parse_ts(ts_match.group(2))
        duration_ms = max(duration_ms or 0, end_ms)

        # Cue text: every non-empty line until the next blank.
        cue_text_lines: list[str] = []
        i += 1
        while i < len(lines) and lines[i].strip() != "":
            cue_text_lines.append(lines[i].strip())
            i += 1
        cue_text = " ".join(cue_text_lines).strip()
        if not cue_text:
            continue

        # Speaker extraction.
        speaker = last_speaker
        body_text = cue_text
        voice_m = _VTT_VOICE_TAG_RE.match(cue_text)
        if voice_m:
            speaker = voice_m.group(1).strip() or last_speaker
            body_text = voice_m.group(2).strip()
        else:
            speaker_m = _VTT_SPEAKER_RE.match(cue_text)
            if speaker_m:
                candidate = speaker_m.group(1).strip()
                # Reject speaker labels that are obviously part of running
                # text (e.g. a sentence beginning with "Note:"). Keep only
                # short ALL/Title-Case names without internal punctuation.
                if len(candidate) <= 80 and not any(ch in candidate for ch in ".!?"):
                    speaker = candidate
                    body_text = speaker_m.group(2).strip()

        body_text = body_text.strip()
        if not body_text:
            continue

        if speaker not in speakers_seen:
            speakers_seen.add(speaker)
            speakers_order.append(speaker)
        last_speaker = speaker

        utterances.append(
            ParsedUtterance(
                speaker=speaker,
                text=body_text,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )

    return MeetingTranscript(
        format="zoom_vtt",
        utterances=tuple(utterances),
        speakers=tuple(speakers_order),
        duration_ms=duration_ms,
    )


# A timestamp on its own line: "[00:00:24]" or "[12:34]". The hours group is
# optional. Anchored to the full (stripped) line so inline markers embedded in
# running text — e.g. "…the [00:02:00] floppy disk…" — are left untouched.
_BRACKET_TS_LINE_RE = re.compile(r"^\[((?:\d{1,2}:)?\d{1,2}:\d{2})\]\s*$")


def _parse_bracketed_transcript(
    text: str, *, format: TranscriptFormat = "zoom_vtt"
) -> MeetingTranscript:
    """Parse a plain-text transcript that uses bracketed timestamps rather
    than WebVTT cues:

        [00:00:24]
        Interviewer 1: Just this week you surpassed Microsoft...

        [00:00:35]
        Interviewee: Um... it doesn't matter very much.

    The timestamp sits alone on a line; the speaker/text block follows until a
    blank line or the next timestamp. Speaker attribution reuses the same
    `Speaker: text` extraction as the WebVTT path, and a turn with no explicit
    speaker inherits the previous one.

    Shared by two callers: `parse_zoom_vtt`'s fallback for a .vtt file that
    doesn't actually start with `WEBVTT` (stays labelled `zoom_vtt`), and
    `parse_google_meet_transcript` for a manually uploaded plain-text export
    (labelled `google_meet`) — hence the caller-supplied `format`.
    """
    lines = text.split("\n")
    utterances: list[ParsedUtterance] = []
    speakers_order: list[str] = []
    speakers_seen: set[str] = set()
    duration_ms: int | None = None
    last_speaker: str = "Unknown Speaker"
    pending_ts: int | None = None

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        ts_m = _BRACKET_TS_LINE_RE.match(line)
        if ts_m:
            try:
                pending_ts = _parse_ts(ts_m.group(1))
            except ValueError:
                pending_ts = None
            duration_ms = max(duration_ms or 0, pending_ts or 0)
            i += 1
            continue

        # Gather the utterance body: consecutive non-empty lines that aren't a
        # new timestamp line.
        block_lines: list[str] = []
        while i < n:
            cur = lines[i].strip()
            if cur == "" or _BRACKET_TS_LINE_RE.match(cur):
                break
            block_lines.append(cur)
            i += 1
        block_text = " ".join(block_lines).strip()
        if not block_text:
            continue

        speaker = last_speaker
        body_text = block_text
        speaker_m = _VTT_SPEAKER_RE.match(block_text)
        if speaker_m:
            candidate = speaker_m.group(1).strip()
            if len(candidate) <= 80 and not any(ch in candidate for ch in ".!?"):
                speaker = candidate
                body_text = speaker_m.group(2).strip()

        body_text = body_text.strip()
        if not body_text:
            continue

        if speaker not in speakers_seen:
            speakers_seen.add(speaker)
            speakers_order.append(speaker)
        last_speaker = speaker

        utterances.append(
            ParsedUtterance(speaker=speaker, text=body_text, start_ms=pending_ts)
        )
        pending_ts = None

    return MeetingTranscript(
        format=format,
        utterances=tuple(utterances),
        speakers=tuple(speakers_order),
        duration_ms=duration_ms or None,
    )


# ── Google Meet (manual plain-text upload) ──────────────────────────────────

def parse_google_meet_transcript(content: str) -> MeetingTranscript:
    """Parse a manually-uploaded plain-text Google Meet transcript.

    Reuses the bracketed-timestamp parser (`[00:00:24]\\nSpeaker: text`) —
    the shape of Google Docs' "Download as .txt" export for a Meet
    transcript, and what most other transcription tools export as plain
    text. Tagged file_type='transcript' by the dedicated upload button in
    `past-panel.tsx` (see `_resolve_file_type` in routers/documents.py).
    """
    if not content:
        return MeetingTranscript(format="google_meet")
    text = content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return _parse_bracketed_transcript(text, format="google_meet")


# ── Microsoft Teams transcript JSON ─────────────────────────────────────────

def parse_teams_transcript(content: str) -> MeetingTranscript:
    """Parse a Microsoft Teams transcript JSON export.

    Teams ships two shapes in the wild:

      shape A (Graph transcript export):
        { "recognizedPhrases": [
            { "speaker": {"displayName": "Alice"},
              "nBest": [{"display": "Hi everyone"}],
              "offset": "PT0M5S", "duration": "PT0M3S" },
            ...
        ]}

      shape B (older / simpler):
        { "entries": [
            { "speakerDisplayName": "Alice",
              "spokenText": "Hi everyone",
              "startOffset": "00:00:05.0",
              "endOffset": "00:00:08.0" },
            ...
        ]}

    We accept either; anything else returns an empty transcript.
    """
    if not content:
        return MeetingTranscript(format="teams_json")
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return MeetingTranscript(format="teams_json")
    if not isinstance(data, dict):
        return MeetingTranscript(format="teams_json")

    utterances: list[ParsedUtterance] = []

    if isinstance(data.get("entries"), list):
        for entry in data["entries"]:
            if not isinstance(entry, dict):
                continue
            speaker = (entry.get("speakerDisplayName") or "").strip() or "Unknown Speaker"
            text = (entry.get("spokenText") or "").strip()
            if not text:
                continue
            start_ms = _parse_teams_offset(entry.get("startOffset"))
            end_ms = _parse_teams_offset(entry.get("endOffset"))
            utterances.append(
                ParsedUtterance(
                    speaker=speaker, text=text,
                    start_ms=start_ms, end_ms=end_ms,
                )
            )
    elif isinstance(data.get("recognizedPhrases"), list):
        for phrase in data["recognizedPhrases"]:
            if not isinstance(phrase, dict):
                continue
            speaker = (
                ((phrase.get("speaker") or {}).get("displayName"))
                or phrase.get("speakerId")
                or "Unknown Speaker"
            )
            nbest = phrase.get("nBest") or []
            text = ""
            if isinstance(nbest, list) and nbest and isinstance(nbest[0], dict):
                text = (nbest[0].get("display") or nbest[0].get("lexical") or "").strip()
            if not text:
                continue
            start_ms = _parse_iso8601_duration(phrase.get("offset"))
            dur_ms = _parse_iso8601_duration(phrase.get("duration")) or 0
            end_ms = (start_ms + dur_ms) if start_ms is not None else None
            utterances.append(
                ParsedUtterance(
                    speaker=str(speaker), text=text,
                    start_ms=start_ms, end_ms=end_ms,
                )
            )
    else:
        return MeetingTranscript(format="teams_json")

    speakers: list[str] = []
    seen: set[str] = set()
    for u in utterances:
        if u.speaker not in seen:
            seen.add(u.speaker)
            speakers.append(u.speaker)

    duration_ms = max((u.end_ms or 0) for u in utterances) if utterances else None
    started_at = _extract_teams_started_at(data)

    return MeetingTranscript(
        format="teams_json",
        utterances=tuple(utterances),
        speakers=tuple(speakers),
        duration_ms=duration_ms,
        started_at=started_at,
        title=(data.get("topic") or data.get("subject") or None),
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

_ISO_DURATION_RE = re.compile(
    r"^PT(?:(?P<h>\d+(?:\.\d+)?)H)?(?:(?P<m>\d+(?:\.\d+)?)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?$"
)


def _parse_iso8601_duration(value: object) -> int | None:
    """`PT1M3S` → 63000 ms. None on malformed input."""
    if not isinstance(value, str) or not value:
        return None
    m = _ISO_DURATION_RE.match(value.strip())
    if not m:
        return None
    h = float(m.group("h") or 0)
    mn = float(m.group("m") or 0)
    s = float(m.group("s") or 0)
    return int(((h * 60 + mn) * 60 + s) * 1000)


def _parse_teams_offset(value: object) -> int | None:
    """Teams writes `00:00:05.0`. Defer to the VTT timestamp parser when the
    shape matches; otherwise try the ISO-duration form."""
    if not isinstance(value, str) or not value:
        return None
    v = value.strip()
    if ":" in v and "PT" not in v.upper():
        try:
            return _parse_ts(v)
        except Exception:
            return None
    return _parse_iso8601_duration(v)


def _extract_teams_started_at(data: dict) -> str | None:
    """Teams transcripts often include `meetingStartTime` (ISO 8601). Some
    exports use `startDateTime`. We normalise to UTC ISO so the DB column
    accepts it directly."""
    candidate = data.get("meetingStartTime") or data.get("startDateTime") or data.get("startTime")
    if not isinstance(candidate, str) or not candidate:
        return None
    try:
        # Tolerate trailing 'Z' and missing tz.
        cleaned = candidate.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    except ValueError:
        return None
