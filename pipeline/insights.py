"""Cluster the final 100 records into the patterns shown on the case-study page.

  python pipeline/insights.py  ->  data/insights.json
"""
import json
from collections import Counter, defaultdict

from common import DATA

STATIC = {"API key", "Basic", "Bearer token", "JWT / service account"}


def main():
    apps = {a["id"]: a for a in json.loads((DATA / "apps_input.json").read_text())}
    final = json.loads((DATA / "research_final.json").read_text())
    gt = {g["id"]: g for g in json.loads((DATA / "composio_ground_truth.json").read_text())}
    acc = json.loads((DATA / "accuracy.json").read_text()) if (DATA / "accuracy.json").exists() else {}

    rows = []
    for sid, rec in final.items():
        i = int(sid)
        a = apps[i]
        rows.append({**rec, "id": i, "category": a["category"]})

    auth_mix = Counter()
    for r in rows:
        m = set(r.get("auth_methods") or [])
        if "OAuth2" in m and m & STATIC:
            auth_mix["OAuth2 + static (key/token/basic)"] += 1
        elif "OAuth2" in m:
            auth_mix["OAuth2 only"] += 1
        elif m & STATIC:
            auth_mix["Static credential only (key/token/basic)"] += 1
        elif "None" in m or not m:
            auth_mix["No auth"] += 1
        else:
            auth_mix["Other"] += 1

    access_by_cat = defaultdict(Counter)
    for r in rows:
        access_by_cat[r["category"]][r["access"]] += 1

    build_overall = Counter(r["buildability"] for r in rows)
    build_by_cat = defaultdict(Counter)
    for r in rows:
        build_by_cat[r["category"]][r["buildability"]] += 1

    blockers = Counter(r["main_blocker"] for r in rows if r.get("main_blocker"))
    blocker_buckets = Counter()
    for r in rows:
        b = (r.get("main_blocker") or "").lower()
        if not b:
            continue
        if any(k in b for k in ("review", "approval", "listing")):
            blocker_buckets["App/marketplace review required"] += 1
        elif any(k in b for k in ("paid plan", "trial", "upgrade")):
            blocker_buckets["Paid plan needed to get credentials"] += 1
        elif any(k in b for k in ("partner", "sales", "contract", "demo")):
            blocker_buckets["Partnership / contact-sales gate"] += 1
        elif any(k in b for k in ("admin", "org", "customer")):
            blocker_buckets["Per-customer admin setup"] += 1
        elif any(k in b for k in ("narrow", "single-purpose", "limited")):
            blocker_buckets["API too narrow to be a real toolkit"] += 1
        elif any(k in b for k in ("no api", "not applicable", "cli", "local")):
            blocker_buckets["No hosted API (local tool only)"] += 1
        else:
            blocker_buckets["Other"] += 1

    mcp_status = Counter(r["official_mcp"] for r in rows)
    mcp_by_access = defaultdict(Counter)
    for r in rows:
        mcp_by_access[r["access"]][r["official_mcp"]] += 1

    composio_matched = sum(1 for g in gt.values() if g["in_composio"])
    ready_not_in_composio = [r["id"] for r in rows if r["buildability"] == "ready" and not gt[r["id"]]["in_composio"]]

    ready_unbuilt_apps = [
        {"id": r["id"], "app": r["app"], "category": r["category"], "auth": r.get("auth_primary") or "-"}
        for r in rows
        if r["buildability"] == "ready" and r["access"] in ("self_serve_free", "self_serve_trial")
        and not gt[r["id"]]["in_composio"]
    ]
    gated_outreach_apps = [
        {"id": r["id"], "app": r["app"], "category": r["category"],
         "access": r["access"], "blocker": r.get("main_blocker") or "-"}
        for r in rows
        if r["access"] == "partner_gated" or r["buildability"] == "blocked"
    ]

    out = {
        "n_apps": len(rows),
        "auth_mix": auth_mix.most_common(),
        "access_by_category": {c: dict(v) for c, v in access_by_cat.items()},
        "buildability_overall": dict(build_overall),
        "buildability_by_category": {c: dict(v) for c, v in build_by_cat.items()},
        "top_blockers_raw": blockers.most_common(10),
        "blocker_buckets": blocker_buckets.most_common(),
        "mcp_status": dict(mcp_status),
        "mcp_by_access": {a: dict(v) for a, v in mcp_by_access.items()},
        "composio_already_matched": composio_matched,
        "ready_apps_not_yet_in_composio": ready_not_in_composio,
        "ready_unbuilt_apps": ready_unbuilt_apps,
        "gated_outreach_apps": gated_outreach_apps,
        "accuracy": acc,
    }
    (DATA / "insights.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("mcp_by_access",)}, indent=1)[:3000])


if __name__ == "__main__":
    main()
