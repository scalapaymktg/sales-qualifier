#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier')

from webhook_server import _tavily_search
import re

results = _tavily_search("GRIVEL S.R.L. IT00139110076 fatturato site:ufficiocamerale.it", max_results=1)

if results:
    content = results[0]["content"]
    print("=== CONTENUTO COMPLETO ===")
    print(content)
    print("\n=== RICERCA FATTURATO ===")
    
    # Prova regex per fatturato
    patterns = [
        r'(?:Fatturato|Ricavi)[:\s]+€?\s*([\d]{1,3}(?:\.[\d]{3})+)',
        r'€\s*([\d]{1,3}(?:\.[\d]{3})+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            print(f"✅ Trovato: {match.group(0)}")
            break
    else:
        print("❌ Fatturato non trovato nel contenuto Tavily")
