"""Stage 3: verification loops over a research pass. None of these use the gold labels.

  L1 lint        internal consistency of each record
  L2 grounding   re-fetch every evidence URL and check the quote is really on the page
  L3 cross-source compare against Composio's own toolkit catalog (independent of the agent)
  L4 judge       a separate LLM call checks each grounded quote actually supports its claim

  python pipeline/verify.py --pass pass1   ->  data/verify_pass1.json + data/feedback_pass1.json
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

import httpx

from common import DATA, RUNS, composio_fetch, extract_json, run_claude

CACHE = DATA / "cache" / "pages"
STATIC = {"API key", "Basic", "Bearer token", "JWT / service account"}


def load_pass(name: str) -> dict:
    recs = {}
    for p in sorted((RUNS / name).glob("[0-9]*.json")):
        d = json.loads(p.read_text())
        if d["record"] and not d["schema_errors"]:
            recs[int(p.stem)] = d["record"]
    return recs


def norm(s: str) -> str:
    s = html.unescape(s or "").lower()
    s = re.sub(r"[`*_#>\[\]()|\\\"'“”‘’]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_page(url: str) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".txt")
    if path.exists():
        return path.read_text()
    text = ""
    try:
        text = composio_fetch(url)
    except Exception:
        pass
    if len(text) < 200:
        try:
            r = httpx.get(url, follow_redirects=True, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            raw = re.sub(r"(?s)<(script|style).*?</\1>", " ", r.text)
            text = max(text, re.sub(r"<[^>]+>", " ", raw), key=len) if r.status_code < 400 else text
        except Exception:
            pass
    path.write_text(text)
    return text


def _segment_match(p: str, q: str) -> float:
    if q in p:
        return 1.0
    m = SequenceMatcher(None, p, q, autojunk=False).find_longest_match(0, len(p), 0, len(q))
    return m.size / len(q)


def quote_match(page: str, quote: str) -> float:
    # Agents stitch quotes with "..."; every stitched segment must be on the page.
    p = norm(page)
    segments = [norm(s) for s in re.split(r"\.\.\.|…", quote or "")]
    segments = [s for s in segments if len(s) >= 12] or [norm(quote)]
    if not segments[0]:
        return 0.0
    return min(_segment_match(p, s) for s in segments)


def lint(rec: dict) -> list:
    issues = []
    if rec["access"] in ("partner_gated", "no_public_api") and rec["buildability"] == "ready":
        issues.append(f"access is {rec['access']} but buildability is 'ready' — these contradict")
    if rec["official_mcp"] == "official" and not rec.get("mcp_url"):
        issues.append("official_mcp is 'official' but mcp_url is empty — link the vendor's MCP page")
    if "SDK/CLI only" in (rec.get("api_style") or []) and rec["buildability"] != "not_applicable":
        issues.append("api_style is 'SDK/CLI only' but buildability is not 'not_applicable'")
    if rec["buildability"] != "ready" and not rec.get("main_blocker"):
        issues.append("buildability is not 'ready' but main_blocker is empty")
    cited = {e.get("field") for e in rec.get("evidence") or []}
    for f in ("auth_methods", "access", "official_mcp"):
        if f not in cited:
            issues.append(f"no evidence entry for {f}")
    return issues


def cross_source(rec: dict, gt: dict) -> list:
    issues = []
    if not gt:
        return issues
    methods = set(rec.get("auth_methods") or [])
    schemes = set(gt["composio_auth_schemes"])
    if gt["in_composio"]:
        if schemes & {"OAUTH2"} and "OAuth2" not in methods:
            issues.append("Composio's production toolkit for this app authenticates with OAuth2, but auth_methods "
                          "does not include OAuth2 — check whether the vendor offers an OAuth2 flow")
        if schemes & {"API_KEY", "BASIC", "BEARER_TOKEN"} and not methods & STATIC:
            issues.append(f"Composio's toolkit supports {sorted(schemes & {'API_KEY', 'BASIC', 'BEARER_TOKEN'})}, "
                          "but auth_methods lists no API key/Basic/token method — check the vendor docs")
        if rec["access"] == "no_public_api" or rec["buildability"] in ("blocked", "not_applicable"):
            issues.append(f"Composio already ships a working toolkit for this app ({gt['composio_tools_count']} tools), "
                          f"which conflicts with access={rec['access']} / buildability={rec['buildability']}")
    if gt["composio_mcp_variants"] and rec["official_mcp"] != "official":
        issues.append(f"Composio's catalog lists an MCP toolkit for this app ({', '.join(gt['composio_mcp_variants'])}), "
                      "but official_mcp is not 'official' — check for a vendor-hosted MCP server")
    return issues


JUDGE_PROMPT = """You are auditing research claims. For each item, decide if the QUOTE, taken from the cited page, \
actually supports the CLAIM. Answer "supports", "partial" (related but does not establish the claim), or \
"does_not_support". Be strict: a quote about something else on the same page does not support the claim.
Return ONLY a JSON object: {"results": [{"i": <index>, "verdict": "...", "why": "<10 words>"}]}

Items:
"""


def judge(items: list) -> dict:
    body = "\n".join(f'{i}. CLAIM: {it["claim"]}\n   QUOTE: "{it["quote"]}"' for i, it in enumerate(items))
    run = run_claude(JUDGE_PROMPT + body, "sonnet", None, [], timeout=600)
    out = extract_json(run["result"]) or {}
    return {r["i"]: r for r in out.get("results", []) if isinstance(r, dict) and "i" in r}


def claim_text(rec: dict, field: str) -> str:
    v = rec.get(field)
    extra = {"access": rec.get("access_notes"), "official_mcp": rec.get("mcp_url")}.get(field)
    return f"{rec['app']}: {field} = {json.dumps(v)}" + (f" ({extra})" if extra else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="pass_name", default="pass1")
    ap.add_argument("--no-judge", action="store_true")
    args = ap.parse_args()

    recs = load_pass(args.pass_name)
    gt = {g["id"]: g for g in json.loads((DATA / "composio_ground_truth.json").read_text())}
    print(f"verifying {len(recs)} records from {args.pass_name}")

    urls = sorted({e["url"] for r in recs.values() for e in (r.get("evidence") or []) if e.get("url")})
    with ThreadPoolExecutor(8) as pool:
        pages = dict(zip(urls, pool.map(fetch_page, urls)))
    print(f"L2: fetched {len(urls)} evidence URLs, {sum(1 for t in pages.values() if len(t) >= 200)} readable")

    report = {}
    judge_items = []
    for i, rec in recs.items():
        ev_rows = []
        for e in rec.get("evidence") or []:
            page = pages.get(e.get("url"), "")
            if len(page) < 200:
                status, score = "unreadable", None
            elif re.search(r"\b404\b|page not found|page (?:does not|doesn.t) exist", page[:600], re.I):
                status, score = "dead_link", None
            else:
                score = round(quote_match(page, e.get("quote", "")), 2)
                status = "grounded" if score >= 0.8 else "partial" if score >= 0.5 else "not_found"
            row = {**e, "grounding": status, "match": score}
            ev_rows.append(row)
            if status == "grounded" and e.get("field") in ("auth_methods", "access", "official_mcp", "api_style"):
                judge_items.append((i, len(ev_rows) - 1, {"claim": claim_text(rec, e["field"]), "quote": e["quote"]}))
        report[i] = {"app": rec["app"], "L1_lint": lint(rec), "L3_cross_source": cross_source(rec, gt.get(i)),
                     "evidence": ev_rows}

    if not args.no_judge and judge_items:
        batches = [judge_items[k:k + 25] for k in range(0, len(judge_items), 25)]
        with ThreadPoolExecutor(4) as pool:
            verdicts = list(pool.map(lambda b: judge([x[2] for x in b]), batches))
        for b, v in zip(batches, verdicts):
            for idx, (i, row_idx, _) in enumerate(b):
                report[i]["evidence"][row_idx]["judge"] = v.get(idx, {}).get("verdict", "no_verdict")
        print(f"L4: judged {len(judge_items)} grounded quotes in {len(batches)} batches")

    feedback = {}
    for i, r in report.items():
        issues = list(r["L1_lint"]) + list(r["L3_cross_source"])
        fields_supported = set()
        for e in r["evidence"]:
            if e["grounding"] == "grounded" and e.get("judge") in (None, "supports"):
                fields_supported.add(e.get("field"))
        for e in r["evidence"]:
            f = e.get("field")
            if e["grounding"] == "dead_link" and f not in fields_supported:
                issues.append(f"evidence URL for {f} is a dead page ({e['url']}) — cite a live primary page")
            elif e["grounding"] in ("not_found", "partial") and f not in fields_supported:
                issues.append(f"evidence for {f} could not be found on {e['url']} (quote match {e['match']}) — "
                              "fetch a primary page and copy a verbatim quote, or correct the claim")
            elif e.get("judge") in ("partial", "does_not_support") and f not in fields_supported:
                issues.append(f"the quote cited for {f} does not actually establish the claim "
                              f"(\"{e['quote'][:80]}\") — find direct evidence or correct the value")
        r["issues"] = sorted(set(issues))
        if r["issues"]:
            feedback[str(i)] = {"record": recs[i], "issues": r["issues"]}

    (DATA / f"verify_{args.pass_name}.json").write_text(json.dumps(report, indent=1))
    (DATA / f"feedback_{args.pass_name}.json").write_text(json.dumps(feedback, indent=1))
    n_ev = sum(len(r["evidence"]) for r in report.values())
    g = sum(e["grounding"] == "grounded" for r in report.values() for e in r["evidence"])
    print(f"evidence grounded: {g}/{n_ev}; apps flagged: {len(feedback)}/{len(report)}")
    print(f"  L1 lint flags: {sum(bool(r['L1_lint']) for r in report.values())} apps; "
          f"L3 cross-source flags: {sum(bool(r['L3_cross_source']) for r in report.values())} apps")


if __name__ == "__main__":
    main()
