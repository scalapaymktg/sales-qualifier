#!/usr/bin/env python3
"""
HubSpot Deal Monitor -> Slack Notifier

Monitors HubSpot for new deals in the Sales Pipeline and sends
summaries to a Slack channel every 5 minutes.

Usage:
    python deal_monitor.py              # Run once
    python deal_monitor.py --schedule   # Run every 5 minutes
"""

import os
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# Paths
SCRIPT_DIR = Path(__file__).parent
PROCESSED_DEALS_FILE = SCRIPT_DIR / "processed_deals.json"

# HubSpot Configuration
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "").strip()
HUBSPOT_BASE_URL = "https://api.hubapi.com"

# Slack Configuration
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "").strip()
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "").strip()  # e.g., "#sales-alerts" or channel ID

# Claude API Configuration
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# Deal properties to fetch
DEAL_PROPERTIES = [
    "dealname",
    "amount",
    "dealstage",
    "pipeline",
    "hs_object_id",
    "createdate",
    "closedate",
    "hubspot_owner_id",
    "generic_source",
]

# Global flag for dry-run mode
DRY_RUN = False

# Company properties to fetch (associated)
COMPANY_PROPERTIES = [
    "company_domain_name",
    "name",
    "iva_vat",
    "country",
    "industry",
    "website",
    "online_annual_revenue",
    "offline_annual_revenue",
]


def load_processed_deals() -> set:
    """Load the set of already processed deal IDs."""
    if PROCESSED_DEALS_FILE.exists():
        try:
            with open(PROCESSED_DEALS_FILE, "r") as f:
                data = json.load(f)
                return set(data.get("processed_ids", []))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Could not load processed deals: {e}")
    return set()


def save_processed_deals(deal_ids: set) -> None:
    """Save the set of processed deal IDs."""
    data = {
        "processed_ids": list(deal_ids),
        "last_updated": datetime.now(timezone.utc).isoformat()
    }
    with open(PROCESSED_DEALS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Saved {len(deal_ids)} processed deal IDs")


def get_sales_pipeline_id() -> Optional[str]:
    """Get the ID of the Sales Pipeline."""
    url = f"{HUBSPOT_BASE_URL}/crm/v3/pipelines/deals"
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}

    logger.info(f"[REQUEST] GET {url}")

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        pipelines = response.json().get("results", [])
        logger.info(f"[RESPONSE] Pipelines: {json.dumps(pipelines, indent=2)}")

        for pipeline in pipelines:
            # Match "Sales Pipeline" or similar names
            label = pipeline.get("label", "").lower()
            if "sales" in label and "pipeline" in label:
                logger.info(f"Found Sales Pipeline: {pipeline['label']} (ID: {pipeline['id']})")
                return pipeline["id"]

        # If no exact match, return first pipeline
        if pipelines:
            logger.warning(f"No 'Sales Pipeline' found, using first: {pipelines[0]['label']}")
            return pipelines[0]["id"]

    except requests.RequestException as e:
        logger.error(f"Failed to fetch pipelines: {e}")

    return None


def get_pipeline_stages(pipeline_id: str) -> dict:
    """Get stage ID to label mapping for a pipeline."""
    url = f"{HUBSPOT_BASE_URL}/crm/v3/pipelines/deals/{pipeline_id}"
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        stages = response.json().get("stages", [])
        return {stage["id"]: stage["label"] for stage in stages}
    except requests.RequestException as e:
        logger.error(f"Failed to fetch pipeline stages: {e}")
        return {}


def get_owner_name(owner_id: str) -> str:
    """Get owner name from owner ID."""
    if not owner_id:
        return "Unassigned"

    url = f"{HUBSPOT_BASE_URL}/crm/v3/owners/{owner_id}"
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        first = data.get("firstName", "")
        last = data.get("lastName", "")
        return f"{first} {last}".strip() or data.get("email", "Unknown")
    except requests.RequestException:
        return f"Owner {owner_id}"


def search_new_deals(pipeline_id: str) -> list:
    """Search for deals in the specified pipeline with filters."""
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals/search"
    headers = {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type": "application/json"
    }

    # Get today's start timestamp (midnight UTC)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_ms = int(today_start.timestamp() * 1000)

    payload = {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "pipeline",
                        "operator": "EQ",
                        "value": pipeline_id
                    },
                    {
                        "propertyName": "generic_source",
                        "operator": "EQ",
                        "value": "Marketing - Interactions & Inbound requests"
                    },
                    {
                        "propertyName": "createdate",
                        "operator": "GTE",
                        "value": str(today_start_ms)
                    }
                ]
            }
        ],
        "properties": DEAL_PROPERTIES,
        "limit": 100,
        "sorts": [
            {
                "propertyName": "createdate",
                "direction": "DESCENDING"
            }
        ]
    }

    logger.info(f"[REQUEST] POST {url}")
    logger.info(f"[PAYLOAD] {json.dumps(payload, indent=2)}")

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        results = response.json()
        logger.info(f"[RESPONSE] Found {len(results.get('results', []))} deals")
        logger.info(f"[RESPONSE] {json.dumps(results, indent=2)}")
        return results.get("results", [])
    except requests.RequestException as e:
        logger.error(f"Failed to search deals: {e}")
        return []


def get_associated_company(deal_id: str) -> Optional[dict]:
    """Get the company associated with a deal."""
    url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/deals/{deal_id}/associations/companies"
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        associations = response.json().get("results", [])

        if not associations:
            return None

        company_id = associations[0].get("id")
        if not company_id:
            return None

        # Fetch company details
        company_url = f"{HUBSPOT_BASE_URL}/crm/v3/objects/companies/{company_id}"
        params = {"properties": ",".join(COMPANY_PROPERTIES)}

        company_response = requests.get(company_url, headers=headers, params=params)
        company_response.raise_for_status()
        return company_response.json().get("properties", {})

    except requests.RequestException as e:
        logger.warning(f"Failed to get company for deal {deal_id}: {e}")
        return None


def fetch_website_content(url: str) -> Optional[str]:
    """Fetch website content and convert to text."""
    if not url or url == "N/A":
        return None

    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Basic HTML to text conversion
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
                if not self.skip:
                    text = data.strip()
                    if text:
                        self.text.append(text)

        parser = TextExtractor()
        parser.feed(response.text)
        text = " ".join(parser.text)

        # Limit to first 8000 chars to avoid token limits
        return text[:8000] if text else None

    except Exception as e:
        logger.warning(f"Failed to fetch website {url}: {e}")
        return None


def analyze_website_with_claude(website_content: str, website_url: str) -> Optional[str]:
    """Use Claude API to analyze website content."""
    if not ANTHROPIC_API_KEY or not website_content:
        return None

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01"
    }

    prompt = f"""Analizza questo sito web e rispondi in modo conciso (max 5 righe totali):

URL: {website_url}

Contenuto del sito:
{website_content}

Rispondi con:
- Tecnologie: (ecommerce platform, CMS, etc.)
- Cosa vende: (categoria prodotti/servizi)
- Catalogo: (ampio/medio/piccolo, categorie principali)
- Social: (quali social sono presenti)
- Pagamenti: (metodi di pagamento visibili)

Se non trovi un'informazione, scrivi "N/D"."""

    payload = {
        "model": "claude-3-haiku-20240307",
        "max_tokens": 500,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("content", [{}])[0].get("text", "")
    except Exception as e:
        logger.warning(f"Claude API error: {e}")
        return None


def format_deal_summary(deal: dict, company: Optional[dict], stage_name: str, owner_name: str, website_analysis: Optional[str] = None) -> str:
    """Format a deal summary for Slack."""
    props = deal.get("properties", {})

    deal_name = props.get("dealname", "Unnamed Deal")
    amount = props.get("amount")
    amount_str = f"EUR {float(amount):,.2f}" if amount else "Not set"

    create_date = props.get("createdate", "")
    if create_date:
        try:
            dt = datetime.fromisoformat(create_date.replace("Z", "+00:00"))
            create_date = dt.strftime("%d/%m/%Y %H:%M")
        except ValueError:
            pass

    # Company info
    domain = company.get("company_domain_name", "N/A") if company else "N/A"
    website = company.get("website", "N/A") if company else "N/A"
    vat = company.get("iva_vat", "N/A") if company else "N/A"
    company_name = company.get("name", "N/A") if company else "N/A"
    online_revenue = company.get("online_annual_revenue", "N/A") if company else "N/A"
    offline_revenue = company.get("offline_annual_revenue", "N/A") if company else "N/A"

    # Website analysis section
    analysis_section = ""
    if website_analysis:
        analysis_section = f"""
*--- Analisi Sito ---*
{website_analysis}
"""

    summary = f"""
:sparkles: *New Deal Created*

*Deal:* {deal_name}
*Amount:* {amount_str}
*Stage:* {stage_name}
*Owner:* {owner_name}
*Created:* {create_date}

*Company:* {company_name}
*Domain:* {domain}
*Website:* {website}
*VAT:* {vat}
*Online Revenue:* {online_revenue}
*Offline Revenue:* {offline_revenue}
{analysis_section}
:link: <https://app-eu1.hubspot.com/contacts/26230674/deal/{deal['id']}|View in HubSpot>
"""
    return summary.strip()


def send_slack_message(message: str) -> bool:
    """Send a message to Slack channel."""
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL:
        logger.error("Slack configuration missing (SLACK_BOT_TOKEN or SLACK_CHANNEL)")
        return False

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "channel": SLACK_CHANNEL,
        "text": message,
        "mrkdwn": True
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            logger.error(f"Slack API error: {data.get('error', 'Unknown error')}")
            return False

        logger.info(f"Message sent to Slack channel {SLACK_CHANNEL}")
        return True

    except requests.RequestException as e:
        logger.error(f"Failed to send Slack message: {e}")
        return False


def check_for_new_deals() -> int:
    """Main function to check for new deals and notify Slack."""
    if not HUBSPOT_TOKEN:
        logger.error("HUBSPOT_TOKEN environment variable not set")
        return 0

    logger.info("Checking for new deals...")

    # Get pipeline info
    pipeline_id = get_sales_pipeline_id()
    if not pipeline_id:
        logger.error("Could not find Sales Pipeline")
        return 0

    stages = get_pipeline_stages(pipeline_id)

    # Load already processed deals
    processed_ids = load_processed_deals()
    logger.info(f"Already processed: {len(processed_ids)} deals")

    # Search for deals
    deals = search_new_deals(pipeline_id)
    logger.info(f"Found {len(deals)} deals in pipeline")

    # Find new deals
    new_deals = [d for d in deals if d["id"] not in processed_ids]
    logger.info(f"New deals to process: {len(new_deals)}")

    # Process each new deal
    notified_count = 0
    for deal in new_deals:
        deal_id = deal["id"]
        props = deal.get("properties", {})

        logger.info(f"Processing deal: {props.get('dealname', deal_id)}")

        # Get associated company
        company = get_associated_company(deal_id)

        # Get stage name
        stage_id = props.get("dealstage", "")
        stage_name = stages.get(stage_id, stage_id)

        # Get owner name
        owner_id = props.get("hubspot_owner_id", "")
        owner_name = get_owner_name(owner_id)

        # Analyze website with Claude
        website_analysis = None
        website_url = company.get("website") or company.get("company_domain_name") if company else None
        if website_url and ANTHROPIC_API_KEY:
            logger.info(f"Fetching website: {website_url}")
            website_content = fetch_website_content(website_url)
            if website_content:
                logger.info(f"Analyzing website with Claude...")
                website_analysis = analyze_website_with_claude(website_content, website_url)

        # Format and send to Slack
        summary = format_deal_summary(deal, company, stage_name, owner_name, website_analysis)

        if SLACK_BOT_TOKEN and SLACK_CHANNEL:
            if send_slack_message(summary):
                notified_count += 1
        else:
            # Print to console if Slack not configured
            logger.info(f"Would send to Slack:\n{summary}")
            notified_count += 1

        # Mark as processed
        processed_ids.add(deal_id)

    # Save updated processed IDs (skip in dry-run mode)
    if not DRY_RUN:
        save_processed_deals(processed_ids)
    else:
        logger.info("[DRY-RUN] Skipping save of processed deals")

    return notified_count


def run_scheduled():
    """Run the check every 5 minutes."""
    import schedule

    logger.info("Starting scheduled mode - checking every 5 minutes")
    logger.info("Press Ctrl+C to stop")

    # Run immediately on start
    check_for_new_deals()

    # Schedule every 5 minutes
    schedule.every(5).minutes.do(check_for_new_deals)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopped by user")


def main():
    global DRY_RUN

    parser = argparse.ArgumentParser(description="Monitor HubSpot deals and notify Slack")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run continuously, checking every 5 minutes"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset processed deals (will re-notify all existing deals)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test mode: don't save processed deals"
    )

    args = parser.parse_args()

    if args.dry_run:
        DRY_RUN = True
        logger.info("[DRY-RUN] Test mode enabled - not saving processed deals")

    if args.reset:
        if PROCESSED_DEALS_FILE.exists():
            PROCESSED_DEALS_FILE.unlink()
            logger.info("Processed deals file reset")
        return

    if args.schedule:
        run_scheduled()
    else:
        count = check_for_new_deals()
        logger.info(f"Notified {count} new deals")


if __name__ == "__main__":
    main()
