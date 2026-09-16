"""Research agent runner using OpenAI API and Composio tools.
Used to research apps when Claude CLI hits session rate limits.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from openai import OpenAI

from common import DATA, ROOT, RUNS, client, extract_json, load_env
from research_agent import ENUMS, REQUIRED, build_prompt, schema_errors
from verify import fetch_page

load_env()
openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for documentation, API references, authentication methods, or pricing pages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query, e.g. 'site:grain.com API docs OAuth'"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch the full text content of a web page URL. ALWAYS fetch a page before citing it for evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch, e.g. 'https://developers.grain.com/docs/authentication'"}
                },
                "required": ["url"],
            },
        },
    },
]


def execute_search(query: str) -> str:
    try:
        c = client()
        res = c.tools.execute("COMPOSIO_SEARCH_WEB", user_id="research-agent", arguments={"query": query})
        d = res.model_dump()
        data = d.get("data") or {}
        ans = data.get("answer") or ""
        cits = data.get("citations") or []
        cit_str = "\n".join(f"[{i+1}] {c.get('id', '')} - {c.get('title', '')}" for i, c in enumerate(cits[:6]))
        return f"Summary: {ans}\n\nCitations:\n{cit_str}"
    except Exception as e:
        return f"Search error: {e}"


def execute_fetch(url: str) -> str:
    text = fetch_page(url)
    if len(text) > 8000:
        return text[:8000] + "\n...[truncated]"
    return text if text else "Error: page could not be fetched or was empty."


def research_app_openai(app: dict, pass_name: str = "pass1", model: str = "gpt-4o") -> dict:
    prompt = build_prompt(app, feedback=None)
    messages = [
        {"role": "system", "content": "You are an expert developer research agent. Work from primary sources, follow the brief strictly, and return ONLY a JSON object at the end."},
        {"role": "user", "content": prompt},
    ]

    tool_calls_log = []
    t0 = time.time()
    total_tokens = 0

    for turn in range(12):
        resp = openai_client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS_SCHEMA,
            tool_choice="auto",
            temperature=0.1,
        )
        msg = resp.choices[0].message
        messages.append(msg)
        total_tokens += resp.usage.total_tokens if resp.usage else 0

        if not msg.tool_calls:
            # Model is finished and outputting final message
            rec = extract_json(msg.content or "")
            errs = schema_errors(rec) if rec else ["no JSON"]
            if not errs:
                cost = (resp.usage.prompt_tokens * 2.5 + resp.usage.completion_tokens * 10) / 1e6 if resp.usage else 0.05
                run_entry = {
                    "tool_calls": tool_calls_log,
                    "cost_usd": round(cost, 4),
                    "turns": turn + 1,
                    "seconds": round(time.time() - t0, 1),
                }
                out = {"record": rec, "schema_errors": [], "runs": [run_entry], "raw_result": None}
                path = RUNS / pass_name / f"{app['id']:03d}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(out, indent=1))
                return out
            else:
                # Ask model to correct
                messages.append({
                    "role": "user",
                    "content": f"Your output had schema errors: {'; '.join(errs)}. Return ONLY the corrected JSON object matching the required schema."
                })
                continue

        # Execute tool calls
        for tc in msg.tool_calls:
            fn_name = tc.function.name
            args = json.loads(tc.function.arguments)
            tool_calls_log.append({"tool": fn_name, "input": args})

            if fn_name == "search_web":
                tool_output = execute_search(args.get("query", ""))
            elif fn_name == "fetch_url":
                tool_output = execute_fetch(args.get("url", ""))
            else:
                tool_output = f"Unknown tool: {fn_name}"

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_output,
            })

    # If exceeded turns
    out = {"record": None, "schema_errors": ["exceeded turn limit"], "runs": [], "raw_result": "Exceeded max turns"}
    path = RUNS / pass_name / f"{app['id']:03d}.json"
    path.write_text(json.dumps(out, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="99,100")
    ap.add_argument("--pass", dest="pass_name", default="pass1")
    ap.add_argument("--model", default="gpt-4o")
    args = ap.parse_args()

    apps = json.loads((DATA / "apps_input.json").read_text())
    wanted = {int(x) for x in args.ids.split(",")}
    target_apps = [a for a in apps if a["id"] in wanted]

    print(f"Researching {len(target_apps)} apps via OpenAI ({args.model})...")
    for a in target_apps:
        print(f"Starting [{a['id']:03d}] {a['app']}...")
        res = research_app_openai(a, pass_name=args.pass_name, model=args.model)
        r = res.get("record") or {}
        print(f"  Done [{a['id']:03d}] {a['app']}: access={r.get('access')} build={r.get('buildability')} errors={res.get('schema_errors')}")


if __name__ == "__main__":
    main()
