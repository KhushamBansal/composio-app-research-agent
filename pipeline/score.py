"""Score research passes against the hand-verified gold sample (data/gold_labels.json).

  python pipeline/score.py pass1 final
"""
import json
import sys

from common import DATA, RUNS  # noqa

STATIC = {"API key", "Basic", "Bearer token", "JWT / service account"}
FIELDS = ["auth", "access", "api_style", "official_mcp", "buildability"]


def load(pass_name: str) -> dict:
    out = {}
    for p in (RUNS / pass_name).glob("[0-9]*.json"):
        d = json.loads(p.read_text())
        if d["record"]:
            out[int(p.stem)] = d["record"]
    return out


def judge_field(field: str, rec: dict, gold: dict):
    if field == "auth":
        m = set(rec.get("auth_methods") or [])
        oauth_ok = gold["auth_oauth"] is None or gold["auth_oauth"] == ("OAuth2" in m)
        static_ok = gold["auth_static"] is None or gold["auth_static"] == bool(m & STATIC)
        return oauth_ok and static_ok, sorted(m)
    if field == "access":
        return rec["access"] in [gold["access"], *gold["access_alt"]], rec["access"]
    if field == "api_style":
        return gold["api_style"] in (rec.get("api_style") or []), rec.get("api_style")
    if field == "official_mcp":
        return rec["official_mcp"] in [gold["official_mcp"], *gold["mcp_alt"]], rec["official_mcp"]
    if field == "buildability":
        return rec["buildability"] in [gold["buildability"], *gold["build_alt"]], rec["buildability"]


def score(pass_name: str, gold: dict) -> dict:
    recs = load(pass_name)
    rows, per_field = [], {f: [0, 0] for f in FIELDS}
    for sid, g in gold.items():
        rec = recs.get(int(sid))
        for f in FIELDS:
            if rec is None:
                ok, got = False, "missing"
            else:
                ok, got = judge_field(f, rec, g)
            per_field[f][0] += ok
            per_field[f][1] += 1
            expected = {"auth": f"oauth={g['auth_oauth']} static={g['auth_static']}", "access": g["access"],
                        "api_style": g["api_style"], "official_mcp": g["official_mcp"],
                        "buildability": g["buildability"]}[f]
            rows.append({"id": int(sid), "app": g["app"], "field": f, "correct": ok, "got": got, "expected": expected})
    total = sum(v[0] for v in per_field.values())
    n = sum(v[1] for v in per_field.values())
    apps_all_right = sum(all(r["correct"] for r in rows if r["id"] == int(s)) for s in gold)
    return {"pass": pass_name, "correct": total, "total": n, "accuracy": round(total / n, 3),
            "apps_fully_correct": apps_all_right, "per_field": {f: {"correct": c, "total": t} for f, (c, t) in per_field.items()},
            "rows": rows}


def main():
    gold = json.loads((DATA / "gold_labels.json").read_text())["labels"]
    results = {}
    for p in sys.argv[1:] or ["pass1"]:
        s = score(p, gold)
        results[p] = s
        print(f"{p}: {s['correct']}/{s['total']} field judgments correct ({s['accuracy']:.0%}); "
              f"{s['apps_fully_correct']}/20 apps fully correct")
        for f, v in s["per_field"].items():
            print(f"   {f:13s} {v['correct']}/{v['total']}")
        for r in s["rows"]:
            if not r["correct"]:
                print(f"   MISS {r['app']:24s} {r['field']:13s} got={r['got']} expected={r['expected']}")
    (DATA / "accuracy.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
