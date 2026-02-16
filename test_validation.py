#!/usr/bin/env python3
"""
Test script per validazione nome/P.IVA - caso Click cafe Italia srl
Questo test verifica che il sistema non accetti più falsi positivi da Tavily
quando la pagina trovata è per un'azienda diversa.
"""
import sys
sys.path.insert(0, "/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier")

from webhook_server import search_company_revenue

print("=" * 80)
print("TEST: Click cafe Italia srl (VAT 02694770642)")
print("=" * 80)
print("Questo VAT in passato dava €96M (CAMAC ARTI GRAFICHE SRL)")
print("Ora dovrebbe dare N/D o confidence LOW per validazione fallita\n")

result = search_company_revenue(
    company_name="Click cafe Italia srl",
    vat="02694770642"
)

print(f"Fatturato: {result['fatturato']}")
print(f"Fonte: {result['source']}")
print(f"Confidence: {result['confidence']}")
print(f"Ragione Sociale: {result['ragione_sociale']}")
print("\nDiagnostica:")
for diag in result['diagnostics']:
    print(f"  • {diag}")

print("\n" + "=" * 80)
print("VERIFICA:")
if result['fatturato'] == "N/D":
    print("✅ PASS - Nessun falso positivo (N/D)")
elif result['confidence'] == "low":
    print("✅ PASS - Confidence downgrade applicato (low)")
elif "96" in result['fatturato'] and result['confidence'] == "high":
    print("❌ FAIL - Ancora falso positivo con confidence high!")
else:
    print(f"⚠️  UNKNOWN - Fatturato: {result['fatturato']}, Confidence: {result['confidence']}")
print("=" * 80)
