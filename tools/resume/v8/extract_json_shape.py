#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path


def shape(value):
    if isinstance(value, dict):
        return {
            key: shape(val)
            for key, val in value.items()
        }

    if isinstance(value, list):
        if not value:
            return ["<any>"]
        return [shape(value[0])]

    if isinstance(value, bool):
        return "<boolean>"

    if isinstance(value, int):
        return "<integer>"

    if isinstance(value, float):
        return "<number>"

    if value is None:
        return "<null>"

    return "<string>"


parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

data = json.loads(Path(args.input).read_text())

Path(args.output).write_text(
    json.dumps(
        shape(data),
        indent=2,
        ensure_ascii=False,
    ) + "\n"
)

print("Created:", args.output)
