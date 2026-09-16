"""Merge research passes (later passes win) into data/runs/final and data/research_final.json.

  python pipeline/build_final.py pass1 pass2
"""
import json
import shutil
import sys

from common import DATA, RUNS


def valid(path):
    d = json.loads(path.read_text())
    return d if d["record"] and not d["schema_errors"] else None


def main():
    passes = sys.argv[1:] or ["pass1"]
    final_dir = RUNS / "final"
    shutil.rmtree(final_dir, ignore_errors=True)
    final_dir.mkdir(parents=True)
    merged = {}
    for name in passes:
        for p in sorted((RUNS / name).glob("[0-9]*.json")):
            d = valid(p)
            if d:
                d["source_pass"] = name
                merged[int(p.stem)] = d
    for i, d in merged.items():
        (final_dir / f"{i:03d}.json").write_text(json.dumps(d, indent=1))
    out = {i: {**d["record"], "source_pass": d["source_pass"]} for i, d in sorted(merged.items())}
    (DATA / "research_final.json").write_text(json.dumps(out, indent=1))
    by_pass = {n: sum(d["source_pass"] == n for d in merged.values()) for n in passes}
    print(f"final: {len(merged)} records; from {by_pass}")


if __name__ == "__main__":
    main()
