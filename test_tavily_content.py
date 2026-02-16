#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier')

from webhook_server import _tavily_search

# Test con GRIVEL
results = _tavily_search("GRIVEL S.R.L. IT00139110076 site:ufficiocamerale.it", max_results=1)

if results:
    print("URL:", results[0]["url"])
    print("\n=== CONTENUTO ESTRATTO DA TAVILY ===")
    print(results[0]["content"][:1000])
    print("\n...")
