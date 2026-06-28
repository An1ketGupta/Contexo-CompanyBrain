-- Recruiting: optional working-hours field for the JD.
--
-- Free-text (e.g. "Mon-Fri 9am-6pm IST", "Flexible — async-first") so the
-- field can describe schedule, timezone, on-call expectations, or async
-- norms without us pretending to model all of those structurally.
-- Null = recruiter didn't disclose; LLM omits the section.
ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS working_hours TEXT;
