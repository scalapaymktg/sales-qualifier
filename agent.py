#!/usr/bin/env python3
"""
Autonomous Deal Qualifier Agent

Uses Claude API with tool use to autonomously:
1. Fetch new deals from HubSpot
2. Analyze websites
3. Search for company info
4. Send summaries to Slack
"""

import os
import json
import time
import logging
import argparse
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# Configuration
SCRIPT_DIR = Path(__file__).parent
PROCESSED_DEALS_FILE = SCRIPT_DIR / "processed_deals.json"

HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "").strip()
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "").strip()
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "").strip()  # For web search

HUBSPOT_BASE_URL = "https://api.hubapi.com"


# ============ HubSpot Functions ============

def get_new_deals() -> list:
    """Fetch new deals from HubSpot."""
    # Get pipeline ID
    url = f"{HUBSPOT_BASE_URL}/crm/v3/pipelines/deals"
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    pipelines = response.json().get("results", [])

    pipeline_id = None
    for p in pipelines:
        if "sales" in p.get("label", "").lower():
            pipeline_id = p["id"]
            break

    if not pipeline_id:
        return []

    # Get today's start
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_ms = int(today_start.timestamp() * 1000)

    # Search deals
    search_url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals/search"
    payload = {
        "filterGroups": [{
            "filters": [
                {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
                {"propertyName": "generic_source", "operator": "EQ", "value": "Marketing - Interactions & Inbound requests"},
                {"propertyName": "createdate", "operator": "GTE", "value": str(today_start_ms)}
            ]
        }],
        "properties": ["dealname", "amount", "dealstage", "createdate", "hubspot_owner_id"],
        "limit": 100
    }

    response = requests.post(search_url, headers={**headers, "Content-Type": "application/json"}, json=payload)
    response.raise_for_status()
    deals = response.json().get("results", [])

    # Load processed
    processed_ids = set()
    if PROCESSED_DEALS_FILE.exists():
        with open(PROCESSED_DEALS_FILE) as f:
            processed_ids = set(json.load(f).get("processed_ids", []))

    # Filter new deals and get company info
    new_deals = []
    for deal in deals:
        if deal["id"] in processed_ids:
            continue

        # Get associated company
        assoc_url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals/{deal['id']}/associations/companies"
        assoc_resp = requests.get(assoc_url, headers=headers)
        company_info = {}

        if assoc_resp.ok:
            associations = assoc_resp.json().get("results", [])
            if associations:
                company_id = associations[0]["id"]
                company_url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/companies/{company_id}"
                company_resp = requests.get(company_url, headers=headers, params={
                    "properties": "domain,name,website,vatnumber,online_annual_revenue,offline_annual_revenue"
                })
                if company_resp.ok:
                    company_info = company_resp.json().get("properties", {})

        new_deals.append({
            "id": deal["id"],
            "name": deal["properties"].get("dealname", "Unknown"),
            "amount": deal["properties"].get("amount"),
            "website": company_info.get("website") or company_info.get("domain"),
            "company_name": company_info.get("name"),
            "vat": company_info.get("vatnumber"),
            "online_revenue": company_info.get("online_annual_revenue"),
            "offline_revenue": company_info.get("offline_annual_revenue"),
        })

    return new_deals


def save_processed_deal(deal_id: str):
    """Mark a deal as processed."""
    processed_ids = set()
    if PROCESSED_DEALS_FILE.exists():
        with open(PROCESSED_DEALS_FILE) as f:
            processed_ids = set(json.load(f).get("processed_ids", []))

    processed_ids.add(deal_id)

    with open(PROCESSED_DEALS_FILE, "w") as f:
        json.dump({
            "processed_ids": list(processed_ids),
            "last_updated": datetime.now(timezone.utc).isoformat()
        }, f, indent=2)


# ============ Tool Functions for Claude ============

def fetch_website(url: str) -> str:
    """Fetch website content."""
    if not url:
        return "No URL provided"

    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Simple HTML to text
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "noscript"):
                    self.skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "noscript"):
                    self.skip = False

            def handle_data(self, data):
                if not self.skip and data.strip():
                    self.text.append(data.strip())

        parser = TextExtractor()
        parser.feed(response.text)
        text = " ".join(parser.text)
        return text[:10000]

    except Exception as e:
        return f"Error fetching website: {e}"


def web_search(query: str) -> str:
    """Search the web using Serper API."""
    if not SERPER_API_KEY:
        return "Web search not configured (SERPER_API_KEY missing)"

    try:
        response = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": 5}
        )
        response.raise_for_status()
        results = response.json()

        output = []
        for item in results.get("organic", [])[:5]:
            output.append(f"- {item.get('title')}: {item.get('snippet')}")

        return "\n".join(output) if output else "No results found"

    except Exception as e:
        return f"Search error: {e}"


def send_to_slack(message: str) -> str:
    """Send message to Slack."""
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL:
        return "Slack not configured"

    try:
        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
            json={"channel": SLACK_CHANNEL, "text": message, "mrkdwn": True}
        )
        data = response.json()
        if data.get("ok"):
            return "Message sent successfully"
        return f"Slack error: {data.get('error')}"
    except Exception as e:
        return f"Slack error: {e}"


# ============ Claude Agent ============

TOOLS = [
    {
        "name": "fetch_website",
        "description": "Fetch and extract text content from a website URL",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The website URL to fetch"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "web_search",
        "description": "Search the web for information about a company, including financials, news, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "send_to_slack",
        "description": "Send a formatted message to the Slack channel",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The message to send (supports Slack markdown)"}
            },
            "required": ["message"]
        }
    }
]


def execute_tool(name: str, input_data: dict) -> str:
    """Execute a tool and return the result."""
    if name == "fetch_website":
        return fetch_website(input_data["url"])
    elif name == "web_search":
        return web_search(input_data["query"])
    elif name == "send_to_slack":
        return send_to_slack(input_data["message"])
    return f"Unknown tool: {name}"


def analyze_deal_with_agent(deal: dict) -> Optional[str]:
    """Use Claude as an agent to analyze a deal."""
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set")
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Sei un agente di sales qualification. Analizza questo nuovo deal e crea un report completo.

DEAL INFO:
- Nome: {deal['name']}
- Amount: EUR {float(deal['amount'] or 0):,.2f}
- Company: {deal['company_name']}
- Website: {deal['website']}
- VAT: {deal['vat']}
- Online Revenue: {deal['online_revenue']}
- Offline Revenue: {deal['offline_revenue']}

COMPITI:
1. Usa fetch_website per analizzare il sito web e capire:
   - Tecnologie (ecommerce, CMS)
   - Cosa vendono
   - Dimensione catalogo
   - Social presenti
   - Metodi di pagamento

2. Usa web_search per cercare:
   - "[company name] fatturato revenue 2024 2025"
   - "[company name] notizie news 2024 2025"

3. Usa send_to_slack per inviare un report formattato con TUTTE le info raccolte.

Il messaggio Slack deve includere:
- Emoji ✨ e formattazione Slack (*bold*, etc.)
- Tutte le info del deal
- Analisi del sito
- Info finanziarie trovate
- Eventuali red flag o notizie rilevanti
- Link HubSpot: https://app-eu1.hubspot.com/contacts/26230674/deal/{deal['id']}

Esegui tutti i tool necessari autonomamente."""

    messages = [{"role": "user", "content": prompt}]

    # Agentic loop
    max_iterations = 10
    for i in range(max_iterations):
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            tools=TOOLS,
            messages=messages
        )

        # Check if done
        if response.stop_reason == "end_turn":
            # Extract final text
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return "Analysis complete"

        # Process tool calls
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"Executing tool: {block.name}")
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            # Add assistant response and tool results
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return None


def run_once(dry_run: bool = False):
    """Run the agent once."""
    logger.info("Checking for new deals...")

    deals = get_new_deals()
    logger.info(f"Found {len(deals)} new deals")

    for deal in deals:
        logger.info(f"Analyzing deal: {deal['name']}")
        result = analyze_deal_with_agent(deal)

        if result and not dry_run:
            save_processed_deal(deal["id"])
            logger.info(f"Deal {deal['name']} processed and saved")
        elif dry_run:
            logger.info(f"[DRY-RUN] Would save deal {deal['id']}")

    logger.info(f"Processed {len(deals)} deals")


def run_scheduled():
    """Run every 5 minutes."""
    import schedule

    logger.info("Starting scheduled mode - checking every 5 minutes")

    run_once()
    schedule.every(5).minutes.do(run_once)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopped")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--schedule", action="store_true", help="Run every 5 minutes")
    parser.add_argument("--dry-run", action="store_true", help="Don't save processed deals")
    args = parser.parse_args()

    if args.schedule:
        run_scheduled()
    else:
        run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
