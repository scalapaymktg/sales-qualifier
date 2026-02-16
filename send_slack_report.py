#!/usr/bin/env python3
"""
Send agent report to Slack after Claude completes.
Usage: python3 send_slack_report.py <deal_name> <log_file> [usage_log] [deal_id]
"""

import sys
import os
import re
import requests
from pathlib import Path
from datetime import datetime

# Load .env file if exists
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "C0A9K3A9WA3")  # inbound-sql-qualifier


def convert_markdown_to_slack(text: str) -> str:
    """Convert markdown formatting to Slack mrkdwn format."""

    # First: Convert **bold** to *bold* (must be done before other * processing)
    # Handle multi-line bold with DOTALL flag
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text, flags=re.DOTALL)

    # Remove markdown headers (## Header -> *Header*)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)

    # Convert markdown tables to bullet lists
    lines = text.split('\n')
    new_lines = []
    in_table = False

    for line in lines:
        # Skip table separator lines (|---|---|)
        if re.match(r'^\|[\s\-:]+\|', line):
            continue

        # Check if it's a table row
        if line.strip().startswith('|') and line.strip().endswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if not in_table:
                # This is likely a header row - skip it
                in_table = True
            else:
                # Data row - convert to bullet point
                if len(cells) >= 2:
                    # Clean any remaining ** from cells
                    cell0 = cells[0].replace('**', '')
                    cell1 = cells[1].replace('**', '')
                    new_lines.append(f"• *{cell0}*: {cell1}")
        else:
            in_table = False
            new_lines.append(line)

    text = '\n'.join(new_lines)

    # Convert markdown links [text](url) to Slack format <url|text>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', text)

    # Convert markdown bullet points (- item) to Slack (• item)
    text = re.sub(r'^-\s+', '• ', text, flags=re.MULTILINE)

    # Clean up any remaining double asterisks
    text = text.replace('**', '*')

    # Remove excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text


def extract_latest_report(log_file: str) -> str:
    """Extract the latest agent report from the log file."""
    try:
        with open(log_file, "r") as f:
            content = f.read()

        # Find the last "=== Agent started" marker
        marker = "=== Agent started at"
        last_idx = content.rfind(marker)

        if last_idx != -1:
            report = content[last_idx:]
        else:
            report = content[-10000:]  # Last 10k chars if no marker found

        # Convert markdown to Slack format
        return convert_markdown_to_slack(report)
    except Exception as e:
        return f"Error reading log: {e}"


def send_to_slack(message: str, deal_name: str = "", deal_id: str = "", usage_stats: dict = None) -> bool:
    """Send a message to Slack channel using blocks for better formatting."""
    if not SLACK_BOT_TOKEN:
        print("SLACK_BOT_TOKEN not set - skipping Slack notification")
        return False

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }

    # Truncate message if too long (Slack block limit is ~3000 chars per section)
    # Split into multiple sections if needed
    max_section_len = 2900
    sections = []

    # Add header
    sections.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"🎯 [OPUS] Deal Qualification Report - {deal_name}",
            "emoji": True
        }
    })

    # Split message into chunks
    if len(message) > max_section_len:
        chunks = [message[i:i+max_section_len] for i in range(0, len(message), max_section_len)]
    else:
        chunks = [message]

    for chunk in chunks[:10]:  # Slack allows max 50 blocks, we use 10 for safety
        sections.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": chunk
            }
        })

    # Add usage stats if available
    if usage_stats and usage_stats.get("total_tokens", 0) > 0:
        sections.append({"type": "divider"})
        sections.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": format_usage_block(usage_stats)
            }
        })

    # Add divider before buttons
    sections.append({"type": "divider"})

    # Add qualification buttons if deal_id is provided
    if deal_id:
        sections.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*🎯 Qualifica questo deal:*"
            }
        })
        sections.append({
            "type": "actions",
            "block_id": f"qualify_deal_{deal_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "🤖 Automated",
                        "emoji": True
                    },
                    "value": f"{deal_id}|autoamted|{deal_name}",
                    "action_id": "qualify_automated"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "👤 Sales",
                        "emoji": True
                    },
                    "value": f"{deal_id}|sales|{deal_name}",
                    "action_id": "qualify_sales"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "🔗 Apri in HubSpot",
                        "emoji": True
                    },
                    "url": f"https://app-eu1.hubspot.com/contacts/26230674/record/0-3/{deal_id}",
                    "action_id": "open_hubspot"
                }
            ]
        })

    payload = {
        "channel": SLACK_CHANNEL,
        "blocks": sections,
        "text": f"Deal Qualification Report - {deal_name}"  # Fallback for notifications
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        result = response.json()
        if result.get("ok"):
            print(f"Slack message sent to {SLACK_CHANNEL}")
            return True
        else:
            print(f"Slack API error: {result.get('error')}")
            return False
    except Exception as e:
        print(f"Failed to send Slack message: {e}")
        return False


def log_output_tokens(usage_log: str, deal_name: str, report: str):
    """Log estimated output tokens to usage.log."""
    try:
        output_chars = len(report)
        estimated_output_tokens = output_chars // 4  # ~4 chars per token

        with open(usage_log, "a") as f:
            f.write(f"{datetime.now().isoformat()}|{deal_name}|OUTPUT|{estimated_output_tokens}|{output_chars}\n")

        print(f"📊 Token estimate - Output: ~{estimated_output_tokens:,} tokens ({output_chars:,} chars)")
    except Exception as e:
        print(f"Failed to log output tokens: {e}")


def get_usage_stats(usage_log: str, deal_name: str) -> dict:
    """
    Calculate usage statistics for a deal.
    Returns dict with tokens, costs, and percentages.
    """
    # Claude Opus 4.5 pricing
    INPUT_PRICE_PER_1M = 15.0   # $15 per 1M input tokens
    OUTPUT_PRICE_PER_1M = 75.0  # $75 per 1M output tokens
    USD_TO_EUR = 0.92

    # Claude Max limits (approximate)
    SESSION_5H_MESSAGES = 45  # ~45 messages per 5 hours
    AVG_TOKENS_PER_MESSAGE = 2500  # rough estimate (input + output)
    SESSION_5H_TOKENS = SESSION_5H_MESSAGES * AVG_TOKENS_PER_MESSAGE  # ~112,500 tokens
    WEEKLY_MULTIPLIER = 24 * 7 / 5  # ~33.6x the 5-hour limit
    WEEKLY_TOKENS = int(SESSION_5H_TOKENS * WEEKLY_MULTIPLIER)  # ~3,780,000 tokens

    stats = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "cost_eur": 0.0,
        "pct_5h_session": 0.0,
        "pct_weekly": 0.0,
        "session_5h_used": 0,
        "weekly_used": 0
    }

    try:
        with open(usage_log, "r") as f:
            lines = f.readlines()

        # Get tokens for this specific deal
        for line in lines:
            parts = line.strip().split("|")
            if len(parts) >= 4 and deal_name in parts[1]:
                tokens = int(parts[3])
                token_type = parts[2]
                # Handle both old (INPUT/OUTPUT) and new (OPUS_INPUT/HAIKU) formats
                if token_type in ("INPUT", "OPUS_INPUT"):
                    stats["input_tokens"] += tokens
                elif token_type == "OUTPUT":
                    stats["output_tokens"] += tokens
                elif token_type == "HAIKU":
                    # Haiku tokens are much cheaper, track separately
                    stats["input_tokens"] += tokens  # Still count towards total

        # Calculate totals for this deal
        stats["total_tokens"] = stats["input_tokens"] + stats["output_tokens"]

        # Calculate cost
        input_cost = (stats["input_tokens"] / 1_000_000) * INPUT_PRICE_PER_1M
        output_cost = (stats["output_tokens"] / 1_000_000) * OUTPUT_PRICE_PER_1M
        stats["cost_usd"] = input_cost + output_cost
        stats["cost_eur"] = stats["cost_usd"] * USD_TO_EUR

        # Calculate session totals (last 5 hours)
        from datetime import timedelta
        now = datetime.now()
        five_hours_ago = now - timedelta(hours=5)
        week_ago = now - timedelta(days=7)

        session_tokens = 0
        weekly_tokens = 0

        for line in lines:
            parts = line.strip().split("|")
            if len(parts) >= 4:
                try:
                    timestamp = datetime.fromisoformat(parts[0])
                    tokens = int(parts[3])

                    if timestamp >= five_hours_ago:
                        session_tokens += tokens
                    if timestamp >= week_ago:
                        weekly_tokens += tokens
                except:
                    continue

        stats["session_5h_used"] = session_tokens
        stats["weekly_used"] = weekly_tokens
        stats["pct_5h_session"] = min((session_tokens / SESSION_5H_TOKENS) * 100, 100)
        stats["pct_weekly"] = min((weekly_tokens / WEEKLY_TOKENS) * 100, 100)

    except Exception as e:
        print(f"Failed to calculate usage stats: {e}")

    return stats


def format_usage_block(stats: dict) -> str:
    """Format usage statistics for Slack message."""
    return (
        f"📊 *Usage Stats*\n"
        f"• Tokens: {stats['input_tokens']:,} in + {stats['output_tokens']:,} out = *{stats['total_tokens']:,}*\n"
        f"• Costo: *€{stats['cost_eur']:.3f}* (${stats['cost_usd']:.3f})\n"
        f"• Sessione 5h: *{stats['pct_5h_session']:.1f}%* ({stats['session_5h_used']:,} tokens)\n"
        f"• Settimana: *{stats['pct_weekly']:.2f}%* ({stats['weekly_used']:,} tokens)"
    )


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 send_slack_report.py <deal_name> <log_file> [usage_log] [deal_id]")
        sys.exit(1)

    deal_name = sys.argv[1]
    log_file = sys.argv[2]
    usage_log = sys.argv[3] if len(sys.argv) > 3 else None
    deal_id = sys.argv[4] if len(sys.argv) > 4 else ""

    report = extract_latest_report(log_file)

    # Log output tokens if usage_log provided
    usage_stats = None
    if usage_log:
        log_output_tokens(usage_log, deal_name, report)
        # Calculate usage stats after logging output
        usage_stats = get_usage_stats(usage_log, deal_name)
        print(f"💰 Cost: €{usage_stats['cost_eur']:.3f} | 5h: {usage_stats['pct_5h_session']:.1f}% | Week: {usage_stats['pct_weekly']:.2f}%")

    send_to_slack(report, deal_name, deal_id, usage_stats)
