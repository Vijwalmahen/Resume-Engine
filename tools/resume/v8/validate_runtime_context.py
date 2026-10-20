#!/usr/bin/env python3

from pathlib import Path
import json


p = Path(
    "master/runtime/runtime_resume_context.json"
)

if not p.exists():
    raise SystemExit(
        "FAIL: runtime_resume_context.json missing"
    )

raw = p.read_text()

try:
    data = json.loads(raw)
except json.JSONDecodeError as e:
    raise SystemExit(
        f"FAIL: invalid JSON: {e}"
    )

required = [
    "schema_version",
    "candidate",
    "education",
    "experience",
    "projects",
    "skills",
    "truth_constraints",
]

missing = [
    key for key in required
    if key not in data
]

if missing:
    raise SystemExit(
        f"FAIL: missing sections: {missing}"
    )

size = len(raw.encode("utf-8"))

# Deliberately small compared with the old repeated
# master/evidence context.
MAX_BYTES = 65000

print(
    f"Runtime context size: "
    f"{size:,} bytes"
)

if size > MAX_BYTES:
    raise SystemExit(
        f"FAIL: context too large "
        f"({size:,} > {MAX_BYTES:,})"
    )

print("Required sections: PASS")
print("JSON: PASS")
print("Size budget: PASS")
print("STATUS: RUNTIME CONTEXT VALID")
