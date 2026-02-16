#!/usr/bin/env python3
"""
Test positivo finale: GRIVEL S.R.L. (VAT IT00139110076)
Questo dovrebbe passare la validazione se la pagina corretta viene trovata.
"""
import sys
sys.path.insert(0, "/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier")

from webhook_server import search_company_revenue

print("=" * 80)
print("TEST: GRIVEL S.R.L. (VAT IT00139110076)")
print("=" * 80)

result = search_company_revenue(
    company_name="GRIVEL S.R.L.",
    vat="IT00139110076"
)

print(f"Fatturato: {result['fatturato']}")
print(f"Fonte: {result['source']}")
print(f"Confidence: {result['confidence']}")
print(f"Ragione Sociale: {result['ragione_sociale']}")
print("\nDiagnostica (ultime 5):")
for diag in result['diagnostics'][-5:]:
    print(f"  • {diag}")

print("\n" + "=" * 80)
print("ANALISI:")
if result['fatturato'] != "N/D":
    if result['confidence'] == "high":
        print("✅ Valore trovato con confidence HIGH (validazione passata o multiple fonti)")
    elif result['confidence'] == "medium":
        print("✅ Valore trovato con confidence MEDIUM")
    elif result['confidence'] == "low":
        print("⚠️  Valore trovato con confidence LOW (validazione fallita)")

    # Verifica se c'è stata validazione
    has_validation = any("validato" in d.lower() or "verified" in d.lower() for d in result['diagnostics'])
    has_downgrade = any("abbassato" in d.lower() or "downgrade" in d.lower() for d in result['diagnostics'])

    if has_validation and not has_downgrade:
        print("✅ Validazione nome/P.IVA passata!")
    elif has_downgrade:
        print("⚠️  Confidence downgrade applicato (validazione fallita)")
else:
    print("ℹ️  Nessun dato trovato (N/D)")
print("=" * 80)
