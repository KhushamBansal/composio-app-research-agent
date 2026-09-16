"""Assemble site/index.html by injecting real pipeline output into site/template.html.

  python pipeline/build_site.py --repo-url https://github.com/you/composio-research
"""
import argparse
import datetime
import json

from common import DATA, ROOT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-url", default="https://github.com/KhushamBansal/composio-app-research-agent")
    ap.add_argument("--hours", default="~6")
    args = ap.parse_args()

    apps_in = {a["id"]: a for a in json.loads((DATA / "apps_input.json").read_text())}
    final = json.loads((DATA / "research_final.json").read_text())
    insights = json.loads((DATA / "insights.json").read_text())
    accuracy = json.loads((DATA / "accuracy.json").read_text())
    gold = json.loads((DATA / "gold_labels.json").read_text())
    cgt_list = json.loads((DATA / "composio_ground_truth.json").read_text()) if (DATA / "composio_ground_truth.json").exists() else []
    cgt_by_id = {item["id"]: item for item in cgt_list}

    apps = []
    ready_unbuilt = []
    gated_outreach = []
    for sid, rec in sorted(final.items(), key=lambda kv: int(kv[0])):
        i = int(sid)
        cg = cgt_by_id.get(i, {})
        in_comp = bool(cg.get("in_composio"))
        app_entry = {
            **rec,
            "id": i,
            "category": apps_in[i]["category"],
            "in_composio": in_comp,
            "composio_tools_count": cg.get("composio_tools_count", 0),
        }
        apps.append(app_entry)

        if rec.get("buildability") == "ready" and not in_comp:
            ready_unbuilt.append({
                "id": i,
                "app": rec["app"],
                "category": apps_in[i]["category"],
                "auth": (rec.get("auth_methods") or ["OAuth2"])[0],
                "access": rec.get("access", "self_serve_free"),
            })
        elif rec.get("access") in ("partner_gated", "no_public_api") or rec.get("buildability") == "blocked":
            gated_outreach.append({
                "id": i,
                "app": rec["app"],
                "category": apps_in[i]["category"],
                "access": rec.get("access", "partner_gated"),
                "blocker": rec.get("main_blocker", "Requires partner / enterprise agreement"),
            })

    insights["ready_unbuilt_apps"] = ready_unbuilt
    insights["gated_outreach_apps"] = gated_outreach

    verify_summary = {"urls_checked": 0, "grounded": 0, "apps_flagged": 0, "apps_rechecked": 0}
    passes_present = sorted({d.get("source_pass") for d in apps if d.get("source_pass")})
    for p in passes_present:
        vpath = DATA / f"verify_{p}.json"
        fpath = DATA / f"feedback_{p}.json"
        if vpath.exists():
            v = json.loads(vpath.read_text())
            verify_summary["urls_checked"] += len({e["url"] for r in v.values() for e in r["evidence"]})
            verify_summary["grounded"] += sum(e["grounding"] == "grounded" for r in v.values() for e in r["evidence"])
        if fpath.exists():
            verify_summary["apps_flagged"] += len(json.loads(fpath.read_text()))
    verify_summary["apps_rechecked"] = sum(1 for d in apps if d.get("source_pass") not in (None, "pass1"))

    honesty_notes = [
        "Two apps in the set have no hosted API at all &mdash; <b>Sherlock</b> and <b>Mermaid CLI</b> are local "
        "CLI tools with community-only MCP wrappers, not vendor APIs. They're marked <code>not_applicable</code>, "
        "not force-fit into a buildability score.",
        "<b>iPayX</b>'s hinted docs URL (<code>ipayx.ai/docs</code>) 404s; the real docs live at a different path. "
        "The agent flagged the dead link rather than guessing, and the correct URL was found by hand.",
        "All 100 apps were researched through the same Claude Code pipeline &mdash; each run gets up to 2 attempts "
        "and is retried automatically if its JSON fails schema validation, with no human data entry.",
        "Verification quote-matching uses fuzzy sequence matching against freshly re-fetched pages. It catches fabricated "
        "or hallucinated quotes deterministically (656/713 grounded), while nuanced vendor pricing tiers were audited against primary documentation.",
        "The 20-app gold sample is stratified across all 10 categories (2 apps per category) to ensure representative evaluation without cherry-picking.",
    ]

    payload = {
        "apps": apps,
        "insights": insights,
        "accuracy": accuracy,
        "gold": gold,
        "verify_summary": verify_summary,
        "meta": {
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d"),
            "repo_url": args.repo_url,
            "hours_spent": args.hours,
            "n_passes": len(passes_present) or 1,
            "honesty_notes": honesty_notes,
        },
    }

    tmpl = (ROOT / "site" / "template.html").read_text()
    out = tmpl.replace("__DATA_JSON__", json.dumps(payload))
    import re
    patterns = [
        r"sk_test_[A-Za-z0-9]{20,}",
        r"sk_live_[A-Za-z0-9]{20,}",
        r"secret_[A-Za-z0-9]{40,}",
        r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+",
        r"xox[bps]-[0-9A-Za-z-]+",
    ]
    for pat in patterns:
        out = re.sub(pat, "[REDACTED]", out)
    (ROOT / "site" / "index.html").write_text(out)
    (ROOT / "index.html").write_text(out)
    print(f"wrote site/index.html & index.html ({len(out)/1024:.0f} KB), {len(apps)} apps")


if __name__ == "__main__":
    main()
