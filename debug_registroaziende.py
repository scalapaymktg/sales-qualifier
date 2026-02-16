#!/usr/bin/env python3
"""
Debug script: scarica la pagina di ricerca registroaziende per analisi.
"""
import sys
import re
sys.path.insert(0, "/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier")

from webhook_server import _get_browser_headers
import requests

vat = "IT09073100720"
url = f"https://registroaziende.it/ricerca?q={vat}"

print(f"Fetching {url}...")
try:
    resp = requests.get(url, timeout=10, headers=_get_browser_headers())

    if resp.status_code == 200:
        html = resp.text
        output_file = "/tmp/registroaziende_search.html"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"\n✅ HTML salvato in: {output_file}")
        print(f"Lunghezza: {len(html)} chars")
        print(f"Status: {resp.status_code}")

        # Cerca pattern "fatturato" nel testo
        matches = re.findall(r'.{0,150}[Ff]atturato.{0,150}', html, re.IGNORECASE)
        if matches:
            print(f"\n🔍 Trovati {len(matches)} match per 'fatturato':")
            for i, m in enumerate(matches[:10], 1):
                print(f"\n{i}. {m.strip()}")
        else:
            print("\n❌ Nessun match per 'fatturato' trovato")

        # Cerca anche "ricavi"
        matches_ricavi = re.findall(r'.{0,150}[Rr]icavi.{0,150}', html, re.IGNORECASE)
        if matches_ricavi:
            print(f"\n🔍 Trovati {len(matches_ricavi)} match per 'ricavi':")
            for i, m in enumerate(matches_ricavi[:10], 1):
                print(f"\n{i}. {m.strip()}")

        # Cerca il nome dell'azienda o risultati
        print(f"\n\n🔍 Primi 2000 caratteri della pagina:")
        print(html[:2000])

    else:
        print(f"❌ HTTP {resp.status_code}")

except Exception as e:
    print(f"❌ Errore: {e}")
