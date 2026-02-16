#!/usr/bin/env python3
"""
Test positivo: VAT che dovrebbe passare la validazione
Usa IT09073100720 (Gruppo Campari) - dovrebbe avere nome/P.IVA validato
"""
import sys
sys.path.insert(0, "/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier")

from webhook_server import search_company_revenue

print("=" * 80)
print("TEST POSITIVO: Gruppo Campari (VAT IT09073100720)")
print("=" * 80)
print("Questo dovrebbe passare la validazione (nome + P.IVA verificati)\n")

result = search_company_revenue(
    company_name="DAVIDE CAMPARI-MILANO N.V.",
    vat="IT09073100720"
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
if result['fatturato'] != "N/D" and result['confidence'] in ("high", "medium"):
    print("✅ PASS - Valore trovato con confidence adeguato")
elif result['fatturato'] == "N/D":
    print("⚠️  INFO - Nessun dato trovato (può essere normale)")
elif result['confidence'] == "low":
    print("⚠️  WARN - Confidence low (verificare validazione)")
print("=" * 80)
