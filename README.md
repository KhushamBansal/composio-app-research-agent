# App Research Agent — Composio take-home

Researches whether each of 100 given apps can become an agent toolkit today: auth method,
self-serve vs gated access, API surface, existing MCP, and a buildability verdict — with a
verbatim quote + URL behind every claim, then automatically verified and hand-checked.

**Live Case Study:** [https://composio-app-research.surge.sh](https://composio-app-research.surge.sh)  
**Source Repository:** [https://github.com/KhushamBansal/composio-app-research-agent](https://github.com/KhushamBansal/composio-app-research-agent)

## How it works

1. **Composio ground truth** (`pipeline/composio_lookup.py`) — pulls Composio's live toolkit
   catalog (1,543 toolkits) via the Composio Python SDK and matches it against the 100 apps.
   This needs no LLM and is the most reliable signal available: if Composio already ships a
   working toolkit for an app, that toolkit's auth schemes and tool count are ground truth,
   not a claim.
2. **The research agent** (`pipeline/research_agent.py`, brief in `pipeline/agent_brief.md`) —
   for every app, launches one headless Claude Code run (`claude -p`) that researches the
   vendor's own docs using a **Composio MCP server** (`pipeline/common.py::mcp_config_path`
   creates it via `client.mcp.create(...)` with Composio's no-auth `composio_search` toolkit:
   web search + `COMPOSIO_SEARCH_FETCH_URL_CONTENT`) with `WebSearch`/`WebFetch` as fallback.
   It returns one JSON record per app with a strict schema and a verbatim quote + URL for
   every disputed field. Runs in parallel, retries on schema errors, logs every tool call,
   token cost and wall time per app to `data/runs/<pass>/<id>.json`.
3. **Verification loops** (`pipeline/verify.py`), fully automatic, no gold labels involved:
   - **L1 lint** — internal consistency (e.g. `access: partner_gated` but `buildability: ready`).
   - **L2 grounding** — re-fetches every cited URL independently and fuzzy-matches the quote
     against the live page text. A quote that isn't really on the page is flagged.
   - **L3 cross-source** — checks the agent's claim against Composio's own catalog (stage 1),
     a second independent source the agent didn't necessarily see.
   - **L4 judge** — a second, separate Claude call reads each grounded quote and the claim it's
     supposed to support and says whether it actually does (catches "technically on the page,
     doesn't prove the claim").
   Every flagged app gets fed back to the research agent for a **second pass**, told exactly
   which fields are disputed and why (`--feedback`), and re-researches only those.
4. **Human verification** (`data/gold_labels.json`) — a 20-app stratified random sample (2 per
   category, seeded) was independently researched by hand (a separate Claude Code session
   reading primary docs directly, before looking at any pass-1 output) and used only to *score*
   pass 1 vs. the final pass — never fed back into the pipeline. See `pipeline/score.py` and the
   case study's verification section for the before/after accuracy numbers and the honest misses.
5. **Synthesis** — `pipeline/insights.py` clusters the 100 final records into the patterns shown
   on the case-study page (auth mix, self-serve vs gated by category, blocker frequency, etc).

## Running it yourself

```bash
python3 -m venv venv && source venv/bin/activate
pip install composio_client httpx

echo "COMPOSIO_API_KEY=sk_..." > .env   # get one at platform.composio.dev

cd pipeline
python3 composio_lookup.py                                  # stage 1, ~10s, no LLM
python3 research_agent.py --pass pass1 --ids all --parallel 8   # stage 2, ~90 min, needs `claude` CLI on PATH
python3 verify.py --pass pass1                               # stage 3, verification loops
python3 research_agent.py --pass pass2 --feedback ../data/feedback_pass1.json --parallel 8
python3 verify.py --pass pass2
python3 build_final.py pass1 pass2                            # merge, later pass wins
python3 score.py pass1 final                                  # accuracy vs data/gold_labels.json
python3 insights.py                                           # data/insights.json for the page
```

Needs the `claude` CLI authenticated (this pipeline shells out to `claude -p`, the same binary
you're reading this in) and a Composio API key. No Anthropic/OpenAI API key is required — the
research agent runs *as* a Claude Code agent rather than calling a model API directly, which is
also why it can use `WebSearch`/`WebFetch` as a fallback alongside the Composio MCP tools.

## Where it needed a human

- **Drawing and labeling the verification sample** — an LLM grading its own work is not
  verification; `data/gold_labels.json` was built by a separate reasoning pass over primary
  sources, deliberately kept apart from the pipeline.
- **A few apps had no usable primary docs** (e.g. `iPayX`'s hinted `/docs` URL 404s; `Waterfall.io`
  is barely indexed) — the agent said so, but confirming there really was nothing better took a
  manual search pass.
- **Judgment calls on ambiguous categories** — e.g. whether a CLI tool with no hosted API
  (`Sherlock`, `Mermaid CLI`) counts as "no public API" or "not applicable" was decided by hand
  and written into `agent_brief.md` as an explicit rule, not left to the agent to invent per app.
- **Aliasing** apps to Composio's own slugs (`pipeline/composio_lookup.py::ALIASES`) where a
  simple name match would miss or misfire (e.g. `Squarespace` vs. `square`, `Monday.com` vs.
  `monday`) — reviewed by hand once, then reused for every run.

## Repo layout

```
pipeline/            the agent + verification code (read this to understand what ran)
data/apps_input.json         the 100 apps as given
data/composio_ground_truth.json   stage 1 output
data/runs/<pass>/<id>.json   one file per app per pass — full record + every tool call + cost
data/gold_labels.json        the 20-app hand-verified sample (never fed to the pipeline)
data/verify_<pass>.json      L1-L4 verification output
data/accuracy.json           pass-over-pass score vs. gold_labels.json
data/insights.json           clustered patterns behind the case-study page
site/index.html              the case study (also published as a Claude Artifact)
```
