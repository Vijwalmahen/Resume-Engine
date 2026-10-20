"""Offline job-intake layer.

Turns a job URL into a normalized `jobs/batch_inbox/<job_key>.md` file and an
`Intake` worksheet row, so the V8.6 batch pipeline
(`tools/resume/v86/run_batch.py`) has a documented, deduplicated front door.

Every module here is offline by construction: nothing in this package opens a
socket. Live scraping is gated behind an explicit, fully-specified actor
configuration that does not exist yet (see `apify_client_shim.py`).
"""
