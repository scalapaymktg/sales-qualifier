#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier')

from webhook_server import search_company_revenue

print("=" * 80)
print("TEST: IT00159560366 (FERRARI S.P.A.)")
print("=" * 80)
result = search_company_revenue(
    company_name="FERRARI S.P.A.",
    vat="IT00159560366"
)

print(f"\n✨ Fatturato: {result.get('fatturato', 'N/D')}")
print(f"📊 Fonte: {result.get('source', 'N/D')}")
print(f"🎯 Confidence: {result.get('confidence', 'N/D')}")
print(f"🏢 Ragione Sociale: {result.get('ragione_sociale', 'N/D')}")
print(f"\n📋 Diagnostica:")
for diag in result.get('diagnostics', []):
    print(f"  • {diag}")
