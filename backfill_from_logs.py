#!/usr/bin/env python3
"""
Script per recuperare qualifiche da webhook.error.log e creare note HubSpot retroattive.

Usage:
    python3 backfill_from_logs.py
"""

import os
import re
import requests
from datetime import datetime, timedelta

# Config
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
LOG_FILE = "/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier/webhook.error.log"

# Pattern log: "2026-02-16 16:15:53,319 - INFO - User jessica691 qualified deal 472175140069 as automated"
PATTERN = r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - INFO - User (\S+) qualified deal (\d+) as (automated|sales)"


def get_deal_name(deal_id):
    """Recupera deal name da HubSpot."""
    url = f"https://api.hubapi.com/crm/v3/objects/deals/{deal_id}"
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}"}
    params = {"properties": "dealname"}

    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            return data.get("properties", {}).get("dealname", "Unknown")
        return "Unknown"
    except:
        return "Unknown"


def create_hubspot_note(deal_id, note_body, timestamp):
    """Crea nota HubSpot con timestamp."""
    url = "https://api.hubapi.com/crm/v3/objects/notes"
    headers = {
        "Authorization": f"Bearer {HUBSPOT_TOKEN}",
        "Content-Type": "application/json"
    }

    # Converti timestamp in millisecondi (epoch)
    ts_ms = int(timestamp.timestamp() * 1000)

    payload = {
        "properties": {
            "hs_note_body": note_body,
            "hs_timestamp": ts_ms
        },
        "associations": [
            {
                "to": {"id": deal_id},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 214}]
            }
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 201:
            return True
        elif response.status_code == 409:
            # Nota già esistente (possibile se abbiamo già creato retroattivamente)
            return None  # Skip silenzioso
        else:
            print(f"  ❌ Errore {response.status_code}: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"  ❌ Errore: {e}")
        return False


def main():
    print("=" * 70)
    print("BACKFILL NOTE HUBSPOT DA LOG - Ultime 24h")
    print("=" * 70)
    print()

    # Timestamp 24 ore fa
    cutoff = datetime.now() - timedelta(hours=24)
    print(f"🕐 Cutoff: {cutoff.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Leggi log
    qualifications = []

    with open(LOG_FILE, "r") as f:
        for line in f:
            match = re.match(PATTERN, line)
            if match:
                timestamp_str = match.group(1)  # "2026-02-16 16:15:53"
                user = match.group(2)
                deal_id = match.group(3)
                qualification = match.group(4)

                # Parse timestamp
                ts = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")

                # Filtra solo ultime 24h
                if ts >= cutoff:
                    qualifications.append({
                        "timestamp": ts,
                        "timestamp_str": timestamp_str,
                        "user": user,
                        "deal_id": deal_id,
                        "qualification": qualification
                    })

    # Deduplica (stesso deal qualificato più volte)
    seen = set()
    unique_quals = []
    for q in reversed(qualifications):  # Reverse per tenere l'ultima qualifica
        if q["deal_id"] not in seen:
            unique_quals.append(q)
            seen.add(q["deal_id"])

    unique_quals.reverse()  # Riordina cronologicamente

    print(f"📊 Trovate {len(qualifications)} qualifiche totali, {len(unique_quals)} uniche")
    print()

    if not unique_quals:
        print("⚠️ Nessuna qualifica nelle ultime 24h")
        return

    # Crea note
    print("=" * 70)
    print("CREAZIONE NOTE HUBSPOT")
    print("=" * 70)
    print()

    success_count = 0
    skip_count = 0

    for q in unique_quals:
        deal_id = q["deal_id"]
        user = q["user"]
        qualification = q["qualification"]
        ts = q["timestamp"]

        # Recupera deal name
        deal_name = get_deal_name(deal_id)

        # Format timestamp italiano
        ts_ita = ts.strftime("%d/%m/%Y alle %H:%M")

        # Map qualification
        qual_display = "Automated" if qualification == "automated" else "Sales"

        # Crea nota
        note_body = f"{user} ha qualificato {deal_name} come {qual_display} il {ts_ita}"

        print(f"📝 Deal {deal_id} ({deal_name}):")
        print(f"   {note_body}")

        result = create_hubspot_note(deal_id, note_body, ts)

        if result is True:
            print(f"   ✅ Nota creata")
            success_count += 1
        elif result is None:
            print(f"   ⏭️  Nota già esistente, skip")
            skip_count += 1
        else:
            print(f"   ❌ Errore creazione")

        print()

    print("=" * 70)
    print(f"✅ Completato: {success_count} create, {skip_count} skipped, {len(unique_quals) - success_count - skip_count} errori")
    print("=" * 70)


if __name__ == "__main__":
    main()
