"""Stage 1: ground truth from Composio's own toolkit catalog (no LLM).

For each of the 100 apps, find the matching Composio toolkit (and any *_mcp variant)
and record Composio's structured metadata: auth schemes, managed auth, tool/trigger counts.
"""
import json
import os
import re
from pathlib import Path

from composio_client import Composio

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Human-reviewed: names where normalized-name matching is ambiguous or wrong.
# Built by running with --candidates and inspecting the output (see README).
ALIASES = {
    "Lark (Larksuite)": ["lark", "larksuite"],
    "Meta Ads": ["metaads", "facebookads", "meta_ads"],
    "LinkedIn Ads": ["linkedinads", "linkedin_ads"],
    "Threads (Meta)": ["threads", "metathreads"],
    "Magento (Adobe Commerce)": ["magento", "adobecommerce"],
    "Amazon Selling Partner": ["amazonsellingpartner", "amazonsp", "amazonsellercentral", "amazon_selling_partner"],
    "Salesforce Commerce Cloud": ["salesforcecommercecloud", "commercecloud", "sfcc"],
    "Monday.com": ["monday", "mondaycom"],
    "Otter AI": ["otter", "otterai"],
    "YouTube Transcript": ["youtubetranscript", "transcriptapi"],
    "WhatsApp Business": ["whatsapp", "whatsappbusiness"],
    "Zoho CRM": ["zohocrm", "zoho"],
    "Help Scout": ["helpscout"],
    "SE Ranking": ["seranking"],
    "Bright Data": ["brightdata"],
    "Waterfall.io": ["waterfall", "waterfallio"],
    "MongoDB Atlas": ["mongodbatlas", "mongodb"],
    "Google Ads": ["googleads"],
    "Paygent Connect": ["paygent", "paygentconnect", "nmi"],
    "Mermaid CLI": ["mermaid", "mermaidcli"],
    "Close": ["close", "closecrm", "closeio"],
    "Copper": ["copper", "coppercrm"],
    "Front": ["front", "frontapp"],
    "Plain": ["plain", "plaincom"],
    "Pylon": ["pylon", "usepylon"],
    "Twenty": ["twenty", "twentycrm"],
    "Grain": ["grain", "grainhq"],
    "Consensus": ["consensus", "consensusapp"],
    "Devin": ["devin", "cognition"],
    "higgsfield": ["higgsfield", "higgsfieldai"],
    "fanbasis": ["fanbasis"],
    "Clay": ["clay", "clayhq"],
    "Sherlock": ["sherlock"],
    "Neo4j": ["neo4j", "neo4jaura"],
    "Fathom": ["fathom", "fathomvideo"],
    "NotebookLM": ["notebooklm", "notebook_lm"],
    "iPayX": ["ipayx"],
}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def fetch_catalog(client: Composio):
    items, cursor = [], None
    while True:
        kwargs = {"limit": 1000}
        if cursor:
            kwargs["cursor"] = cursor
        page = client.toolkits.list(**kwargs)
        for t in page.items:
            items.append({
                "slug": t.slug,
                "name": t.name,
                "auth_schemes": list(t.auth_schemes or []),
                "composio_managed_auth_schemes": list(t.composio_managed_auth_schemes or []),
                "no_auth": bool(t.no_auth),
                "tools_count": int(t.meta.tools_count or 0),
                "triggers_count": int(t.meta.triggers_count or 0),
                "categories": [c.name for c in (t.meta.categories or [])],
                "description": t.meta.description,
                "app_url": t.meta.app_url,
            })
        cursor = page.next_cursor
        if not cursor or not page.items:
            break
    return items


def match(app: dict, catalog: list):
    keys = {norm(app["app"]), norm(re.sub(r"\(.*?\)", "", app["app"]))}
    keys |= {norm(a) for a in ALIASES.get(app["app"], [])}
    keys.discard("")
    primary, mcp = [], []
    for t in catalog:
        s, n = norm(t["slug"]), norm(t["name"])
        base_s = norm(re.sub(r"_?mcp$", "", t["slug"]))
        base_n = norm(re.sub(r"\s*mcp$", "", t["name"], flags=re.I))
        is_mcp = t["slug"].endswith("_mcp") or t["name"].lower().endswith(" mcp")
        if is_mcp and (base_s in keys or base_n in keys):
            mcp.append(t)
        elif not is_mcp and (s in keys or n in keys):
            primary.append(t)
    return primary, mcp


def main():
    load_env()
    client = Composio(api_key=os.environ["COMPOSIO_API_KEY"])
    catalog = fetch_catalog(client)
    (DATA / "composio_catalog.json").write_text(json.dumps(catalog, indent=1))
    print(f"catalog: {len(catalog)} toolkits")

    apps = json.loads((DATA / "apps_input.json").read_text())
    out = []
    for app in apps:
        primary, mcp = match(app, catalog)
        best = max(primary, key=lambda t: t["tools_count"]) if primary else None
        out.append({
            "id": app["id"],
            "app": app["app"],
            "in_composio": best is not None,
            "composio_slug": best["slug"] if best else None,
            "composio_auth_schemes": best["auth_schemes"] if best else [],
            "composio_managed_auth": best["composio_managed_auth_schemes"] if best else [],
            "composio_tools_count": best["tools_count"] if best else 0,
            "composio_triggers_count": best["triggers_count"] if best else 0,
            "composio_categories": best["categories"] if best else [],
            "composio_mcp_variants": [m["slug"] for m in mcp],
            "other_primary_matches": [t["slug"] for t in primary if best and t["slug"] != best["slug"]],
        })
    (DATA / "composio_ground_truth.json").write_text(json.dumps(out, indent=1))
    hit = sum(o["in_composio"] for o in out)
    print(f"matched {hit}/100 apps to a native Composio toolkit")
    for o in out:
        if not o["in_composio"]:
            print("  no toolkit:", o["app"], "| mcp variants:", o["composio_mcp_variants"])


if __name__ == "__main__":
    main()
