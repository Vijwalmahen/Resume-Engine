# Tests

## Portable public tests

Run:

    python3 tests/run_public_tests.py

This test entrypoint works without private candidate data,
historical backup snapshots, generated benchmark artifacts,
or personalized resume templates.

The older V8.x test files remain useful as development history,
but some require local private artifacts that are intentionally
excluded from the public repository.
