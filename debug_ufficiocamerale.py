#!/usr/bin/env python3
"""
Debug script: scarica la pagina ufficiocamerale di GRIVEL e salva HTML per analisi regex.
"""
import sys
sys.path.insert(0, "/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier")

from webhook_server import _fetch_with_playwright

url = "https://www.ufficiocamerale.it/7569/grivel-srl?srsltid=AfmBOop51KPQ9AJqF23yOEdsnNq54935Z9Uatw69fOzY9KINwkNOK-93"

print(f"Fetching {url} with Playwright...")
html = _fetch_with_playwright(url)

if html:
    output_file = "/tmp/grivel_ufficiocamerale.html"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ HTML salvato in: {output_file}")
    print(f"Lunghezza: {len(html)} chars")

    # Cerca pattern "fatturato" nel testo
    import re
    matches = re.findall(r'.{0,100}[Ff]atturato.{0,100}', html, re.IGNORECASE)
    if matches:
        print(f"\n🔍 Trovati {len(matches)} match per 'fatturato':")
        for i, m in enumerate(matches[:5], 1):
            print(f"\n{i}. {m.strip()}")
    else:
        print("\n❌ Nessun match per 'fatturato' trovato")

    # Cerca anche "ricavi"
    matches_ricavi = re.findall(r'.{0,100}[Rr]icavi.{0,100}', html, re.IGNORECASE)
    if matches_ricavi:
        print(f"\n🔍 Trovati {len(matches_ricavi)} match per 'ricavi':")
        for i, m in enumerate(matches_ricavi[:5], 1):
            print(f"\n{i}. {m.strip()}")
else:
    print("❌ Playwright fetch fallito")
