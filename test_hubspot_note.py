#!/usr/bin/env python3
"""
Test script for HubSpot note creation
"""
import sys
sys.path.insert(0, "/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier")

from webhook_server import create_hubspot_note
from datetime import datetime

# Test deal ID
deal_id = "7468573939"

# Create test note (simulating a qualification action)
user = "claude_test"
deal_name = "Test Deal"
qualification = "Automated"
now = datetime.now().strftime("%d/%m/%Y alle %H:%M")

note_text = f"{user} ha qualificato {deal_name} come {qualification} il {now}"

print("=" * 80)
print(f"TEST: Creazione nota su deal {deal_id}")
print("=" * 80)
print(f"Testo nota: {note_text}")
print("\nCreazione nota in corso...")

success = create_hubspot_note(deal_id, note_text)

if success:
    print("\n✅ Nota creata con successo su HubSpot!")
    print(f"Controlla il deal: https://app.hubspot.com/contacts/26230674/deal/{deal_id}")
else:
    print("\n❌ Errore durante la creazione della nota")
print("=" * 80)
