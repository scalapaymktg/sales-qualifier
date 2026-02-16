#!/usr/bin/env python3
"""
Test script for geographical VAT validation with VIES
"""
import sys
sys.path.insert(0, "/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier")

from webhook_server import search_company_revenue

# Test 1: VAT francese - dovrebbe saltare fonti italiane
print("=" * 80)
print("TEST 1: VAT Francese FR45930881560 (con prefisso)")
print("=" * 80)
result = search_company_revenue(
    company_name="SARL DAMN ALL TO CROWN CYNIQUE",
    vat="FR45930881560"
)
print(f"Fatturato: {result['fatturato']}")
print(f"Fonte: {result['source']}")
print(f"Confidence: {result['confidence']}")
print(f"Ragione Sociale: {result['ragione_sociale']}")
print("\nDiagnostica:")
for diag in result['diagnostics']:
    print(f"  {diag}")

print("\n\n")

# Test 2: VAT italiano CON prefisso - dovrebbe usare fonti italiane
print("=" * 80)
print("TEST 2: VAT Italiano IT00139110076 (con prefisso IT)")
print("=" * 80)
result2 = search_company_revenue(
    company_name="GRIVEL S.R.L.",
    vat="IT00139110076"
)
print(f"Fatturato: {result2['fatturato']}")
print(f"Fonte: {result2['source']}")
print(f"Confidence: {result2['confidence']}")
print(f"Ragione Sociale: {result2['ragione_sociale']}")
print("\nDiagnostica:")
for diag in result2['diagnostics']:
    print(f"  {diag}")

print("\n\n")

# Test 3: VAT italiano SENZA prefisso - dovrebbe assumere IT e usare fonti italiane
print("=" * 80)
print("TEST 3: VAT Italiano 00139110076 (SENZA prefisso - assume IT)")
print("=" * 80)
result3 = search_company_revenue(
    company_name="GRIVEL S.R.L.",
    vat="00139110076"
)
print(f"Fatturato: {result3['fatturato']}")
print(f"Fonte: {result3['source']}")
print(f"Confidence: {result3['confidence']}")
print(f"Ragione Sociale: {result3['ragione_sociale']}")
print("\nDiagnostica:")
for diag in result3['diagnostics']:
    print(f"  {diag}")
