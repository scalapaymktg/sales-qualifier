#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier')

from webhook_server import search_company_revenue

print("=" * 80)
print("TEST 1: IT00139110076 (GRIVEL S.R.L.)")
print("=" * 80)
result1 = search_company_revenue(
    company_name="GRIVEL S.R.L.",
    vat="IT00139110076"
)

print(f"\nFatturato: {result1.get('fatturato', 'N/D')}")
print(f"Fonte: {result1.get('source', 'N/D')}")
print(f"Confidence: {result1.get('confidence', 'N/D')}")
print(f"Ragione Sociale: {result1.get('ragione_sociale', 'N/D')}")
print(f"\nDiagnostica:")
for diag in result1.get('diagnostics', []):
    print(f"  • {diag}")

print("\n" + "=" * 80)
print("TEST 2: 02972340844 (GLOBAL DI BONAFEDE PELLEGRINO)")
print("=" * 80)
result2 = search_company_revenue(
    company_name="GLOBAL DI BONAFEDE PELLEGRINO",
    vat="02972340844"
)

print(f"\nFatturato: {result2.get('fatturato', 'N/D')}")
print(f"Fonte: {result2.get('source', 'N/D')}")
print(f"Confidence: {result2.get('confidence', 'N/D')}")
print(f"Ragione Sociale: {result2.get('ragione_sociale', 'N/D')}")
print(f"\nDiagnostica:")
for diag in result2.get('diagnostics', []):
    print(f"  • {diag}")
