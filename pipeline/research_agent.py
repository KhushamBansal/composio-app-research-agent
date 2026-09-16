"""Stage 2: the research agent. One headless Claude Code run per app, researching via a Composio MCP server.

  python pipeline/research_agent.py --pass pass1 --ids all --parallel 8
  python pipeline/research_agent.py --pass pass2 --ids 12,40 --feedback data/flags.json
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import DATA, ROOT, RUNS, extract_json, mcp_config_path, run_claude

REQUIRED = ["id", "app", "what_it_does", "auth_methods", "auth_primary", "access", "api_style",
            "api_breadth", "official_mcp", "buildability", "evidence", "confidence"]
ENUMS = {
    "access": {"self_serve_free", "self_serve_trial", "paid_plan", "approval_required", "partner_gated", "no_public_api"},
    "api_breadth": {"broad", "moderate", "narrow", "none"},
    "official_mcp": {"official", "community_only", "none"},
    "buildability": {"ready", "ready_with_caveats", "blocked", "not_applicable"},
    "confidence": {"high", "medium", "low"},
}
TOOLS = ["mcp__composio__COMPOSIO_SEARCH_WEB", "mcp__composio__COMPOSIO_SEARCH_TAVILY",
         "mcp__composio__COMPOSIO_SEARCH_DUCK_DUCK_GO", "mcp__composio__COMPOSIO_SEARCH_FETCH_URL_CONTENT",
         "WebFetch", "WebSearch", "Read", "Grep"]


def schema_errors(rec: dict) -> list:
    errs = [f"missing {k}" for k in REQUIRED if k not in rec]
    errs += [f"bad {k}={rec.get(k)!r}" for k, allowed in ENUMS.items() if k in rec and rec[k] not in allowed]
    return errs


def build_prompt(app: dict, feedback: dict | None) -> str:
    brief = (ROOT / "pipeline" / "agent_brief.md").read_text()
    p = f"{brief}\n\n## Your app\nid: {app['id']}\napp: {app['app']}\ncategory: {app['category']}\nhint: {app['hint']}\n"
    if feedback:
        p += ("\n## Re-check requested\nA previous research pass produced the record below, and automated "
              "verification raised the issues listed. Re-research ONLY what is needed to resolve each issue, "
              "from primary sources, and return a complete corrected record. Keep fields that were not "
              "disputed unless you find they are wrong. If an issue turns out to be a false alarm, keep the "
              "original value and say why in open_questions.\n\n"
              f"Previous record:\n{json.dumps(feedback['record'], indent=1)}\n\nIssues:\n"
              + "\n".join(f"- {i}" for i in feedback["issues"]) + "\n")
    return p


def research_one(app: dict, pass_name: str, model: str, mcp_cfg: str, feedback: dict | None) -> dict:
    prompt = build_prompt(app, feedback)
    attempts = []
    rec = None
    for attempt in range(2):
        run = run_claude(prompt, model, mcp_cfg, TOOLS)
        attempts.append({k: run[k] for k in ("tool_calls", "cost_usd", "turns", "seconds")})
        if any(m in run["result"].lower() for m in ("hit your session limit", "usage limit", "credit balance")):
            rec = None
            break
        rec = extract_json(run["result"])
        errs = schema_errors(rec) if rec else ["no JSON in final message"]
        if not errs:
            break
        prompt += f"\n\nYour previous answer was invalid ({'; '.join(errs)}). Return ONLY the corrected JSON object."
    out = {"record": rec, "schema_errors": schema_errors(rec) if rec else ["no JSON"], "runs": attempts,
           "raw_result": None if rec else run["result"][-3000:]}
    path = RUNS / pass_name / f"{app['id']:03d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="pass_name", default="pass1")
    ap.add_argument("--ids", default="all")
    ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--feedback", help="JSON file {id: {record, issues}} for a re-check pass")
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--only-failed", action="store_true", help="rerun apps with no valid record in this pass")
    args = ap.parse_args()

    apps = json.loads((DATA / "apps_input.json").read_text())
    if args.ids != "all":
        wanted = {int(x) for x in args.ids.split(",")}
        apps = [a for a in apps if a["id"] in wanted]
    feedback = json.loads(open(args.feedback).read()) if args.feedback else {}
    if feedback:
        apps = [a for a in apps if str(a["id"]) in feedback]
    if args.skip_existing:
        apps = [a for a in apps if not (RUNS / args.pass_name / f"{a['id']:03d}.json").exists()]

    if args.only_failed:
        def valid(a):
            p = RUNS / args.pass_name / f"{a['id']:03d}.json"
            if not p.exists():
                return False
            d = json.loads(p.read_text())
            return bool(d["record"]) and not d["schema_errors"]
        apps = [a for a in apps if not valid(a)]

    mcp_cfg = mcp_config_path()
    (RUNS / args.pass_name).mkdir(parents=True, exist_ok=True)
    (RUNS / args.pass_name / "_brief_used.md").write_text((ROOT / "pipeline" / "agent_brief.md").read_text())
    print(f"{args.pass_name}: researching {len(apps)} apps with {args.model}, parallel={args.parallel}", flush=True)
    with ThreadPoolExecutor(args.parallel) as pool:
        futs = {pool.submit(research_one, a, args.pass_name, args.model, mcp_cfg, feedback.get(str(a["id"]))): a
                for a in apps}
        for f in as_completed(futs):
            a = futs[f]
            try:
                out = f.result()
                r = out["record"] or {}
                ncalls = sum(len(x["tool_calls"]) for x in out["runs"])
                print(f"  [{a['id']:3d}] {a['app']:26s} access={r.get('access')} build={r.get('buildability')} "
                      f"tools={ncalls} errors={out['schema_errors']}", flush=True)
            except Exception as e:
                print(f"  [{a['id']:3d}] {a['app']:26s} FAILED {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
