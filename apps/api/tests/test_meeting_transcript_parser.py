"""Meeting transcript parser tests (Agent Day 13).

Covers the Zoom WebVTT + Microsoft Teams JSON parsers in
`app.services.parsers.meeting_transcript`. The agent's downstream behaviour
(LLM extraction, derived doc creation) is exercised separately; these tests
isolate the pure functions so a parser regression doesn't masquerade as an
LLM regression.

Test surface:
    * detect_transcript_format — extension wins; content sniff fallback
    * parse_zoom_vtt — voice tags, multi-line cues, speaker fall-through
    * parse_teams_transcript — both Teams shapes (entries / recognizedPhrases)
    * tolerance for BOM, CRLF, malformed JSON, missing speakers
    * is_empty() invariants on unknown content
"""
from __future__ import annotations

import json

import pytest

from app.services.parsers.meeting_transcript import (
    detect_transcript_format,
    parse_teams_transcript,
    parse_transcript,
    parse_zoom_vtt,
)


# ── detect_transcript_format ───────────────────────────────────────────────


def test_detect_extension_wins_over_content():
    """`file_type` is authoritative — even if content looks like JSON we
    trust the upload classifier."""
    assert detect_transcript_format(file_type="vtt", content="not really vtt") == "zoom_vtt"
    assert (
        detect_transcript_format(file_type="teams_transcript", content="not really json")
        == "teams_json"
    )


def test_detect_falls_back_to_content_sniff_when_filetype_missing():
    vtt_body = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nAlice: Hi"
    assert detect_transcript_format(file_type=None, content=vtt_body) == "zoom_vtt"

    teams_body = json.dumps({"recognizedPhrases": []})
    assert detect_transcript_format(file_type=None, content=teams_body) == "teams_json"


def test_detect_returns_unknown_for_arbitrary_json():
    """A JSON document that doesn't carry Teams' top-level keys is NOT a
    transcript — important so a generic .json upload doesn't get routed
    into the MeetingNotesAgent."""
    arbitrary = json.dumps({"hello": "world"})
    assert detect_transcript_format(file_type=None, content=arbitrary) == "unknown"


def test_detect_tolerates_bom_and_whitespace():
    body = "﻿   WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nA: hi"
    assert detect_transcript_format(file_type=None, content=body) == "zoom_vtt"


# ── parse_zoom_vtt ─────────────────────────────────────────────────────────


def test_parse_zoom_vtt_extracts_speakers_and_text():
    body = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.500\n"
        "Alice: Welcome everyone.\n\n"
        "00:00:03.600 --> 00:00:07.000\n"
        "Bob: Thanks Alice — let's start.\n"
    )
    result = parse_zoom_vtt(body)
    assert result.format == "zoom_vtt"
    assert len(result.utterances) == 2
    assert result.utterances[0].speaker == "Alice"
    assert "Welcome everyone" in result.utterances[0].text
    assert result.utterances[1].speaker == "Bob"
    assert result.speakers == ("Alice", "Bob")
    assert result.duration_ms == 7_000


def test_parse_zoom_vtt_handles_voice_tag_variant():
    body = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.000\n"
        "<v Alice>Hi everyone.</v>\n"
    )
    result = parse_zoom_vtt(body)
    assert result.utterances[0].speaker == "Alice"
    assert result.utterances[0].text == "Hi everyone."


def test_parse_zoom_vtt_falls_through_speaker_on_continuation_cue():
    """Cues without an explicit speaker label inherit the previous speaker."""
    body = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.000\n"
        "Alice: First sentence.\n\n"
        "00:00:02.500 --> 00:00:03.500\n"
        "Continuation without a label.\n"
    )
    result = parse_zoom_vtt(body)
    assert len(result.utterances) == 2
    assert result.utterances[1].speaker == "Alice"


def test_parse_zoom_vtt_rejects_speaker_labels_with_punctuation():
    """A candidate "speaker" containing sentence punctuation (.!?) is
    treated as running text, not as a label. The line then inherits the
    prior speaker."""
    body = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.000\n"
        "Alice: Setup.\n\n"
        "00:00:03.000 --> 00:00:04.000\n"
        "i.e. some content: continuing.\n"
    )
    result = parse_zoom_vtt(body)
    # The "i.e. some content" before the colon contains a `.` so the parser
    # refuses to treat it as a speaker label — falls through to Alice.
    assert result.utterances[-1].speaker == "Alice"


def test_parse_zoom_vtt_empty_on_non_vtt_input():
    """Strict-mode bail when the file doesn't start with WEBVTT."""
    result = parse_zoom_vtt("nope, this isn't a VTT")
    assert result.is_empty()


def test_parse_zoom_vtt_tolerates_crlf_and_bom():
    body = "﻿WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.000\r\nAlice: Hi\r\n"
    result = parse_zoom_vtt(body)
    assert len(result.utterances) == 1


def test_parse_zoom_vtt_attributes_unlabeled_to_unknown_speaker():
    body = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.000\n"
        "Just text, no speaker label.\n"
    )
    result = parse_zoom_vtt(body)
    assert result.utterances[0].speaker == "Unknown Speaker"


# ── parse_teams_transcript ─────────────────────────────────────────────────


def test_parse_teams_transcript_entries_shape():
    body = json.dumps({
        "entries": [
            {
                "speakerDisplayName": "Alice",
                "spokenText": "Hi everyone",
                "startOffset": "00:00:05.0",
                "endOffset": "00:00:08.0",
            },
            {
                "speakerDisplayName": "Bob",
                "spokenText": "Thanks",
                "startOffset": "00:00:09.0",
                "endOffset": "00:00:10.0",
            },
        ]
    })
    result = parse_teams_transcript(body)
    assert result.format == "teams_json"
    assert len(result.utterances) == 2
    assert result.speakers == ("Alice", "Bob")
    assert result.utterances[0].start_ms == 5_000
    assert result.utterances[1].end_ms == 10_000


def test_parse_teams_transcript_recognized_phrases_shape():
    body = json.dumps({
        "recognizedPhrases": [
            {
                "speaker": {"displayName": "Alice"},
                "nBest": [{"display": "Welcome"}],
                "offset": "PT0M5S",
                "duration": "PT0M3S",
            }
        ],
        "topic": "Standup",
    })
    result = parse_teams_transcript(body)
    assert len(result.utterances) == 1
    assert result.utterances[0].speaker == "Alice"
    assert result.utterances[0].start_ms == 5_000
    assert result.utterances[0].end_ms == 8_000
    assert result.title == "Standup"


def test_parse_teams_transcript_handles_missing_speaker_with_fallback():
    body = json.dumps({
        "entries": [
            {"spokenText": "Anonymous comment"},
        ]
    })
    result = parse_teams_transcript(body)
    assert result.utterances[0].speaker == "Unknown Speaker"


def test_parse_teams_transcript_empty_on_malformed_json():
    result = parse_teams_transcript("{not actually json")
    assert result.is_empty()


def test_parse_teams_transcript_empty_on_wrong_shape():
    """JSON that lacks both Teams top-level keys returns empty rather than
    raising. The agent treats that the same as a blank transcript."""
    result = parse_teams_transcript(json.dumps({"foo": "bar"}))
    assert result.is_empty()


def test_parse_teams_transcript_extracts_started_at():
    body = json.dumps({
        "entries": [
            {"speakerDisplayName": "A", "spokenText": "Hi"},
        ],
        "meetingStartTime": "2026-05-01T10:00:00Z",
    })
    result = parse_teams_transcript(body)
    assert result.started_at is not None
    assert result.started_at.startswith("2026-05-01T10:00:00")


# ── parse_transcript dispatch ──────────────────────────────────────────────


def test_parse_transcript_dispatches_by_file_type():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nA: hi"
    assert parse_transcript(file_type="vtt", content=vtt).format == "zoom_vtt"

    teams = json.dumps({"entries": [{"speakerDisplayName": "A", "spokenText": "x"}]})
    assert (
        parse_transcript(file_type="teams_transcript", content=teams).format
        == "teams_json"
    )


def test_parse_transcript_unknown_format_returns_empty():
    result = parse_transcript(file_type=None, content="random text")
    assert result.format == "unknown"
    assert result.is_empty()


# ── to_dialog_text rendering ───────────────────────────────────────────────


def test_to_dialog_text_caps_output():
    body = "WEBVTT\n\n"
    for i in range(50):
        body += f"00:00:{i:02d}.000 --> 00:00:{i + 1:02d}.000\nAlice: Sentence number {i}.\n\n"
    result = parse_zoom_vtt(body)
    rendered = result.to_dialog_text(max_chars=120)
    assert len(rendered) <= 120
    assert rendered.startswith("Alice: Sentence number 0.")


def test_to_dialog_text_skips_blank_utterances():
    body = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.000\n"
        "Alice: Real content.\n"
    )
    result = parse_zoom_vtt(body)
    rendered = result.to_dialog_text()
    assert "Alice: Real content." in rendered
