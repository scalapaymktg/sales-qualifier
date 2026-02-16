#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier')

from webhook_server import search_company_revenue

result = search_company_revenue(
    company_name="GRIVEL S.R.L.",
    vat="IT00139110076"
)

print("\n=== RISULTATO RICERCA FATTURATO ===\n")
print(f"Fatturato: {result.get('fatturato', 'N/D')}")
print(f"Fonte: {result.get('source', 'N/D')}")
print(f"Confidence: {result.get('confidence', 'N/D')}")
print(f"Ragione Sociale: {result.get('ragione_sociale', 'N/D')}")
print(f"Raw: {result.get('raw', '')}")

print("\nDiagnostica:")
for diag in result.get('diagnostics', []):
    print(f"  {diag}")
