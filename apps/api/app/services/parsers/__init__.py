"""Specialised parsers that operate above the standard chunk+embed pipeline.

The ingestion pipeline's `parse_document` handles raw text extraction from
PDF/DOCX/etc. Files in this package handle structured-content sources where
we want to preserve more than flat text — meeting transcripts being the
first such source (speakers, timestamps).
"""
from .meeting_transcript import (
    MeetingTranscript,
    ParsedUtterance,
    detect_transcript_format,
    parse_teams_transcript,
    parse_transcript,
    parse_zoom_vtt,
)

__all__ = [
    "MeetingTranscript",
    "ParsedUtterance",
    "detect_transcript_format",
    "parse_teams_transcript",
    "parse_transcript",
    "parse_zoom_vtt",
]
