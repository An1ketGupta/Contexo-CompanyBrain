"""Pure-function tests for the Zoom opt-in/attendee-resolution logic in
services/integrations/zoom.py. No DB, no network — these just check the
consent-matching rules that decide whose meetings get ingested and who gets
a share grant.
"""
from app.services.integrations import zoom as zoom_svc


def _row(transcript_optins=None, attendee_optins=None) -> dict:
    return {
        "metadata": {
            zoom_svc.OPTINS_KEY: transcript_optins or {},
            zoom_svc.ATTENDEE_OPTINS_KEY: attendee_optins or {},
        }
    }


class TestResolveOptedInHost:
    def test_matches_case_insensitive_email(self):
        row = _row(transcript_optins={"host@example.com": "user-1"})
        assert zoom_svc.resolve_opted_in_host(row, "Host@Example.com") == "user-1"

    def test_strips_whitespace(self):
        row = _row(transcript_optins={"host@example.com": "user-1"})
        assert zoom_svc.resolve_opted_in_host(row, "  host@example.com  ") == "user-1"

    def test_none_when_host_never_opted_in(self):
        row = _row(transcript_optins={"someone-else@example.com": "user-2"})
        assert zoom_svc.resolve_opted_in_host(row, "host@example.com") is None

    def test_none_when_host_email_missing(self):
        row = _row(transcript_optins={"host@example.com": "user-1"})
        assert zoom_svc.resolve_opted_in_host(row, None) is None

    def test_none_when_row_missing_metadata(self):
        assert zoom_svc.resolve_opted_in_host({}, "host@example.com") is None


class TestIsUserOptedIn:
    def test_true_when_user_id_present_in_map_values(self):
        row = _row(transcript_optins={"a@x.com": "user-1", "b@x.com": "user-2"})
        assert zoom_svc.is_user_opted_in(row, "user-2") is True

    def test_false_when_absent(self):
        row = _row(transcript_optins={"a@x.com": "user-1"})
        assert zoom_svc.is_user_opted_in(row, "user-9") is False

    def test_false_when_row_is_none(self):
        assert zoom_svc.is_user_opted_in(None, "user-1") is False


class TestIsAttendeeOptedIn:
    def test_reads_the_separate_attendee_map_not_host_map(self):
        row = _row(
            transcript_optins={"a@x.com": "user-1"},
            attendee_optins={"b@x.com": "user-2"},
        )
        assert zoom_svc.is_attendee_opted_in(row, "user-1") is False
        assert zoom_svc.is_attendee_opted_in(row, "user-2") is True


class TestResolveOptedInAttendees:
    def test_resolves_opted_in_participants_only(self):
        row = _row(
            attendee_optins={
                "alice@example.com": "user-alice",
                "bob@example.com": "user-bob",
            }
        )
        result = zoom_svc.resolve_opted_in_attendees(
            row,
            participant_emails=["alice@example.com", "stranger@example.com"],
            exclude_user_id=None,
        )
        assert result == ["user-alice"]

    def test_excludes_the_host(self):
        row = _row(
            attendee_optins={
                "alice@example.com": "user-alice",
                "host@example.com": "user-host",
            }
        )
        result = zoom_svc.resolve_opted_in_attendees(
            row,
            participant_emails=["alice@example.com", "host@example.com"],
            exclude_user_id="user-host",
        )
        assert result == ["user-alice"]

    def test_dedupes_same_user_from_multiple_emails(self):
        row = _row(
            attendee_optins={
                "alice@example.com": "user-alice",
                "alice-alt@example.com": "user-alice",
            }
        )
        result = zoom_svc.resolve_opted_in_attendees(
            row,
            participant_emails=["alice@example.com", "alice-alt@example.com"],
            exclude_user_id=None,
        )
        assert result == ["user-alice"]

    def test_case_and_whitespace_insensitive_matching(self):
        row = _row(attendee_optins={"alice@example.com": "user-alice"})
        result = zoom_svc.resolve_opted_in_attendees(
            row,
            participant_emails=["  Alice@Example.com  "],
            exclude_user_id=None,
        )
        assert result == ["user-alice"]

    def test_empty_when_no_participants_opted_in(self):
        row = _row(attendee_optins={"alice@example.com": "user-alice"})
        result = zoom_svc.resolve_opted_in_attendees(
            row, participant_emails=["stranger@example.com"], exclude_user_id=None
        )
        assert result == []

    def test_result_is_sorted(self):
        row = _row(
            attendee_optins={
                "z@example.com": "user-zzz",
                "a@example.com": "user-aaa",
            }
        )
        result = zoom_svc.resolve_opted_in_attendees(
            row,
            participant_emails=["z@example.com", "a@example.com"],
            exclude_user_id=None,
        )
        assert result == ["user-aaa", "user-zzz"]
