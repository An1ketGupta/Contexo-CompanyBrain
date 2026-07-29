"""The canonical candidate profile.

Assembled from job_requisitions, recruiting_candidates, onboarding_runs and
organizations — deliberately a read-layer, not a new table, because all of that
data already exists and duplicating it would create a second thing to keep in
sync with the ATS.
"""
