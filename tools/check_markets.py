#!/usr/bin/env python3
"""Check every value in the market filter files against the dimension schema.

Worth running after you edit a cohort. A filter value that is not in the schema
does not raise an error anywhere in the pipeline. It just matches nobody, and
you get an empty cohort with no explanation, which is a slow way to lose an
afternoon.

Usage:
    python tools/check_markets.py
    python tools/check_markets.py task/markets/india.json
"""

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = HERE / "reference" / "dimensions-values.json"


def load_schema():
    if not SCHEMA.exists():
        sys.exit(f"missing {SCHEMA}. It should be committed alongside this script.")
    data = json.loads(SCHEMA.read_text())
    return {d["id"]: set(d["values"]) for d in data["dimensions"]}


def check(path, allowed):
    problems = []
    filters = json.loads(path.read_text()).get("dimensionFilters", {})
    for dimension, values in filters.items():
        key = dimension.replace("dimensions.", "")
        if key not in allowed:
            problems.append(f"unknown dimension '{key}'")
            continue
        for value in values:
            if value not in allowed[key]:
                near = [v for v in allowed[key] if v.lower().startswith(value[:3].lower())]
                hint = f" Did you mean {near[0]}?" if near else ""
                problems.append(f"'{key}' has no value '{value}'.{hint}")
    return problems


def main():
    allowed = load_schema()
    targets = [pathlib.Path(a) for a in sys.argv[1:]]
    if not targets:
        targets = sorted((HERE / "task" / "markets").glob("*.json"))
    if not targets:
        sys.exit("no market files found")

    bad = 0
    for path in targets:
        problems = check(path, allowed)
        if problems:
            bad += 1
            print(f"{path.name}")
            for problem in problems:
                print(f"    {problem}")
        else:
            print(f"{path.name} ok")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
