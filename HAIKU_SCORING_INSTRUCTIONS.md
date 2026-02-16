# Haiku Scoring Instructions

## Overview
This document explains how Haiku (Claude 4.5 Haiku) calculates the 1-10 potential score for deals during the triage phase.

## Scoring Prompt

Haiku receives the following JSON response instruction:

```json
{
  "score": <1-10 potenziale per Scalapay BNPL>,
  "is_ecommerce": <true/false - dal nome/categoria sembra vendere prodotti?>,
  "monthly_visits": <numero visite mensili dai dati, 0 se N/D>,
  "has_bnpl_competitor": <true/false se vedi Klarna/Clearpay/Afterpay nei dati o nel payment_info>,
  "category": "<settore: Fashion/Electronics/Home/Beauty/Food/Services/Other>",
  "summary": "<2-3 frasi: tipo di business, dimensione stimata, fit con Scalapay>"
}
```

## Input Data Provided to Haiku

- **Deal Name**: Nome del deal/azienda
- **Domain**: Dominio del sito web (se disponibile)
- **VAT Number**: Partita IVA (usata per migliorare accuracy ricerca fatturato)
- **Fatturato**: Revenue stimato (from Ollama web search + VAT lookup)
  - **Expected accuracy: 90%** thanks to VAT + company name
  - Sources: reportaziende.it, fatturatoitalia.it, bilanci pubblici
- **Payment Providers**: Lista payment provider rilevati (Stripe, PayPal, Nexi, etc.)
  - Detected via agent-browser navigation + HTTP fetch
- **BNPL Competitors**: Lista competitor BNPL rilevati (Klarna, Clearpay, Afterpay, Scalapay, Alma, Oney, etc.)
  - Detected at HP/PDP/Checkout stages
- **SEMrush Data**:
  - Rank Italia
  - Keywords organiche
  - Traffico organico stimato (visite/mese)
  - Valore traffico
  - Keywords Adwords
  - Traffico Adwords
  - Top keywords organiche
- **SimilarWeb Data**:
  - Rank globale e paese
  - Categoria
  - Visite mensili
  - Tempo medio sul sito
  - Pagine per visita
  - Bounce rate
  - Fonti traffico (search, social, direct, referral, email, paid)
  - Top paesi

## Scoring Criteria (Explicit - Updated 2026-02-04)

I criteri di scoring sono **diversi** per E-commerce e Physical Store.

### E-COMMERCE — Score 7-10 richiede TUTTI i 4 criteri:

1. **✅ Fatturato > €1M** (MANDATORY)
   - Threshold: Revenue must exceed €1 million annually
   - Source: VIES + fatturatoitalia.it + Atoka + Ollama
   - **If revenue NOT found or < €1M → Score MUST be < 7** (automatic rejection)

2. **✅ Payment Stack Moderno** (from agent-browser detection)
   - Modern payment providers detected: Stripe, PayPal, Adyen, Nexi, Square, etc.

3. **✅ AOV Medio-Alto** (from agent-browser price detection)
   - Average Order Value: €120+ (estimated from product prices on site)

4. **✅ Tech Stack Moderno** (from Wappalyzer detection)
   - Solo Shopify o WooCommerce contano come tech stack moderno
   - La categoria merceologica NON è un criterio

### PHYSICAL STORE — Score basato SOLO su fatturato (deterministico):

| Fatturato | Score |
|-----------|-------|
| N/D | 2 |
| < €500K | 3 |
| €500K - €1M | 5 |
| €1M - €5M | 6 |
| €6M | 7 |
| €7M | 8 |
| €8M | 9 |
| ≥ €9M | 10 |

Il score è calcolato in Python (non da Haiku). Haiku genera solo il summary.

### Medium Potential (Score 4-6) — solo E-commerce
- Revenue €500K - €1M (below threshold)
- Some criteria met but not all 4
- Basic payment stack (only PayPal or bank transfer)
- Low AOV (<€120)
- No Shopify/WooCommerce detected

### Low Potential (Score 1-3) — solo E-commerce
- Revenue < €500K or not found
- No payment stack detected
- Very low AOV (<€20)
- Incompatible sector (B2B services, lead generation, etc.)
- Not a retail business

## Escalation Criteria

**Current Rule** (Updated 2026-01-24):
- **Score ≥ 7** → Escalate to Opus for full analysis

**Previous Rules** (Deprecated):
- ~~Score ≥ 6~~ → Escalate
- ~~E-commerce AND monthly_visits > 5000~~ → Escalate
- ~~Has BNPL competitor~~ → Escalate

## Examples

### Example 1: High Score (9/10) ✅
- **Deal**: Velasca (luxury fashion footwear) — E-commerce
- **Fatturato**: €26 mld (✅ > €1M)
- **Payment Stack**: Stripe, PayPal, Apple Pay (✅ modern)
- **AOV**: €200-400 (✅ high ticket)
- **Tech Stack**: Shopify (✅ modern)
- **Result**: ✅ All 4 e-commerce criteria met

### Example 2: Low Score (2/10) ❌
- **Deal**: Amendola Srl
- **Fatturato**: N/D (❌ not found)
- **Payment Stack**: Basic
- **Category**: Unclear
- **Result**: ❌ Do not escalate (revenue not found = automatic low score)

### Example 3: Medium Score (6/10) ❌
- **Deal**: Small fashion e-commerce
- **Fatturato**: €800K (❌ below €1M threshold)
- **Payment Stack**: PayPal only (❌ basic)
- **AOV**: €60 (✅ decent)
- **Category**: Fashion (✅ BNPL-friendly)
- **Result**: ❌ Do not escalate (only 2/4 criteria met, revenue below threshold)

### Example 4: In-Store Retail (8/10) ✅
- **Deal**: Luxury furniture chain (20 stores)
- **Store Type**: Physical Store
- **Fatturato**: €8M → Score 8 (deterministico: €8M = 8)
- **Result**: ✅ Score calcolato automaticamente dal fatturato (Physical Store)

## Iteration Notes

This scoring system is **data-driven and explicit** (updated 2026-01-24). To improve:

1. **Monitor revenue detection accuracy**:
   - Target: 90% accuracy thanks to VAT + company name
   - Track: How many times revenue is "N/D" vs actually found
   - Improve: Add more sources (Atoka, Infocamere, etc.) if accuracy < 90%

2. **Adjust revenue threshold**:
   - Current: €1M (very restrictive)
   - Can be lowered to €500K if missing good opportunities
   - Monitor: % of deals rejected only due to revenue threshold

3. **Refine AOV estimation**:
   - Currently: Estimated from category + brand positioning
   - Future: Parse actual product prices from agent-browser snapshots
   - Add regex to extract "€XX" from HTML

4. **Payment stack scoring**:
   - Current: Binary (modern vs basic)
   - Future: Weight by sophistication (Stripe/Adyen > PayPal > basic)

5. **Feedback loop**:
   - Track: Haiku score vs actual deal conversion
   - Identify: False negatives (good deals scored < 7) and false positives (bad deals scored ≥ 7)
   - Adjust: Thresholds and weights based on outcomes

## Cost Impact

- **Haiku triage**: ~€0.0001 per deal (250-500 tokens @ $0.25/$1.25 per 1M)
- **Opus full analysis**: ~€0.10-0.50 per deal (15K-50K tokens @ $15/$75 per 1M)
- **Savings**: ~97-99.9% on low-potential deals

**Target**: Filter out 60-70% of deals at triage stage, reducing overall cost by ~60-70%.

## Version History

- **v2.0** (2026-02-04): Split scoring Physical Store (solo fatturato, deterministico) vs E-commerce (fatturato + payment + AOV + tech stack Shopify/WooCommerce). Rimossa categoria come criterio e-commerce. Aggiunto salvataggio JSON su HubSpot.
- **v1.0** (2026-01-24): Initial scoring system with score ≥ 7 escalation rule
- **v0.1** (2026-01-23): Initial implementation with 3 escalation criteria (deprecated)
