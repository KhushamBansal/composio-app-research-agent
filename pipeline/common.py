import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from composio_client import Composio

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RUNS = DATA / "runs"
MCP_TOOLS = [
    "COMPOSIO_SEARCH_WEB",
    "COMPOSIO_SEARCH_TAVILY",
    "COMPOSIO_SEARCH_DUCK_DUCK_GO",
    "COMPOSIO_SEARCH_FETCH_URL_CONTENT",
]


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def client() -> Composio:
    load_env()
    return Composio(api_key=os.environ["COMPOSIO_API_KEY"])


def mcp_config_path() -> str:
    """Create (once) a Composio MCP server exposing search+fetch, and write a claude --mcp-config file."""
    load_env()
    server_id = os.environ.get("COMPOSIO_MCP_SERVER_ID")
    if not server_id:
        s = client().mcp.create(name=f"app-research-agent-{int(time.time())}", auth_config_ids=[],
                                no_auth_apps=["composio_search"], allowed_tools=MCP_TOOLS)
        server_id = s.id
        with open(ROOT / ".env", "a") as f:
            f.write(f"COMPOSIO_MCP_SERVER_ID={server_id}\n")
        os.environ["COMPOSIO_MCP_SERVER_ID"] = server_id
    cfg = {"mcpServers": {"composio": {
        "type": "http",
        "url": f"https://backend.composio.dev/v3/mcp/{server_id}/mcp?user_id=research-agent",
        "headers": {"x-api-key": os.environ["COMPOSIO_API_KEY"]},
    }}}
    path = Path(tempfile.gettempdir()) / "composio_research_mcp.json"
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)
    return str(path)


def composio_fetch(url: str) -> str:
    r = client().tools.execute("COMPOSIO_SEARCH_FETCH_URL_CONTENT", user_id="research-agent",
                               arguments={"urls": [url]})
    d = r.model_dump()
    if not d.get("successful"):
        return ""
    results = (d.get("data") or {}).get("results") or []
    return "\n".join((x.get("text") or "") for x in results)


def extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def run_claude(prompt: str, model: str, mcp_cfg: str, allowed_tools: list, timeout: int = 900) -> dict:
    """Run one headless Claude Code agent; return final text plus a log of tool calls, cost and turns."""
    cmd = ["claude", "-p", prompt, "--model", model, "--strict-mcp-config",
           "--disallowedTools", "Bash", "Write", "Edit", "NotebookEdit",
           "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    if mcp_cfg:
        cmd += ["--mcp-config", mcp_cfg]
    if allowed_tools:
        cmd += ["--allowedTools", *allowed_tools]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=tempfile.gettempdir())
    tool_calls, result, cost, turns = [], "", None, None
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tool_calls.append({"tool": block.get("name"), "input": block.get("input")})
        elif ev.get("type") == "result":
            result = ev.get("result") or ""
            cost = ev.get("total_cost_usd")
            turns = ev.get("num_turns")
    return {"result": result, "tool_calls": tool_calls, "cost_usd": cost, "turns": turns,
            "seconds": round(time.time() - t0, 1), "stderr": proc.stderr[-2000:]}
