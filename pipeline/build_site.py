"""Assemble site/index.html by injecting real pipeline output into site/template.html.

  python pipeline/build_site.py --repo-url https://github.com/you/composio-research
"""
import argparse
import datetime
import json

from common import DATA, ROOT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-url", default="")
    ap.add_argument("--hours", default="~6")
    args = ap.parse_args()

    apps_in = {a["id"]: a for a in json.loads((DATA / "apps_input.json").read_text())}
    final = json.loads((DATA / "research_final.json").read_text())
    insights = json.loads((DATA / "insights.json").read_text())
    accuracy = json.loads((DATA / "accuracy.json").read_text())
    gold = json.loads((DATA / "gold_labels.json").read_text())

    apps = []
    for sid, rec in sorted(final.items(), key=lambda kv: int(kv[0])):
        i = int(sid)
        apps.append({**rec, "id": i, "category": apps_in[i]["category"]})

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
        "The research agent runs as a Claude Code subprocess with a session/rate limit shared across the account &mdash; "
        "the first full pass hit that limit partway through and had to be resumed; run logs "
        "(<code>data/runs/pass1/*.json</code>) show exactly which apps needed a retry.",
        "Verification quote-matching is fuzzy string matching against re-fetched pages, not a human reading every "
        "citation &mdash; it catches fabricated or wrong quotes reliably, but a technically-present-but-misleading "
        "quote needs the LLM-judge pass (L4) or a human to catch, which is why both exist.",
        "The 20-app gold sample is small on purpose (time budget) &mdash; it's stratified across all 10 categories "
        "so no category is unchecked, but it does not claim 95%-confidence-interval statistical power over 100 apps.",
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
    (ROOT / "site" / "index.html").write_text(out)
    print(f"wrote site/index.html ({len(out)/1024:.0f} KB), {len(apps)} apps")


if __name__ == "__main__":
    main()
