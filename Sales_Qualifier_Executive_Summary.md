# Sales Qualifier
## Sistema Autonomo di Qualificazione Lead

---

# Executive Summary

**Sales Qualifier** è un sistema di automazione intelligente che qualifica automaticamente i deal inbound, riducendo il carico operativo del team Sales e accelerando il tempo di risposta ai prospect ad alto potenziale.

---

# Il Problema

| Sfida | Impatto |
|-------|---------|
| **Volume elevato di deal inbound** | Il team Sales spende tempo prezioso su lead a basso potenziale |
| **Qualificazione manuale** | Processo lento, inconsistente e soggetto a errori umani |
| **Dati frammentati** | Informazioni sparse tra HubSpot, siti web, database aziendali |
| **Tempo di risposta** | I deal ad alto potenziale non vengono prioritizzati rapidamente |

---

# La Soluzione

Sales Qualifier automatizza l'intero processo di qualificazione:

```
Deal creato su HubSpot
        ↓
   Raccolta dati automatica
   (fatturato, traffico, pagamenti, tech stack)
        ↓
   Scoring intelligente (1-10)
        ↓
   Report Slack con pulsanti interattivi
        ↓
   Sales qualifica con 1 click
```

---

# Come Funziona

## 1. Data Enrichment Automatico

Il sistema raccoglie automaticamente dati da **8+ fonti**:

| Fonte | Dato Raccolto |
|-------|---------------|
| **VIES (EU)** | Validazione P.IVA, ragione sociale |
| **fatturatoitalia.it / Atoka** | Fatturato annuo |
| **SEMrush** | Rank SEO, keywords, traffico organico |
| **SimilarWeb** | Visite mensili (Italia vs Estero), engagement |
| **Wappalyzer** | Tech stack (Shopify, WooCommerce, etc.) |
| **Agent Browser** | Payment provider e BNPL competitor |
| **HubSpot** | Dati deal, company, revenue dichiarato |

## 2. Scoring Intelligente

### E-commerce (4 criteri obbligatori per score 7-10)

| Criterio | Soglia |
|----------|--------|
| **Fatturato** | > €1M annuo |
| **Payment Stack** | Provider moderno (Stripe, Adyen, Nexi...) |
| **AOV** | > €120 medio |
| **Tech Stack** | Shopify o WooCommerce |

### Physical Store (score deterministico)

| Fatturato | Score |
|-----------|-------|
| N/D | 2 |
| < €500K | 3 |
| €500K - €1M | 5 |
| €1M - €5M | 6 |
| €6M+ | 7-10 (proporzionale) |

## 3. Report Slack Interattivo

Ogni deal genera un report strutturato con:
- **Score 1-10** con motivazione
- **Dati fatturato** (fonte e anno)
- **Traffico web** (Italia vs Estero, YoY)
- **Payment provider** rilevati
- **BNPL competitor** (Klarna, Clearpay, etc.)
- **Pulsanti interattivi**: `[Automated]` `[Sales]` `[Apri HubSpot]`

---

# Benefici

## Per il Team Sales

| Beneficio | Impatto |
|-----------|---------|
| **Zero data entry** | Tutti i dati raccolti automaticamente |
| **Prioritizzazione immediata** | Deal ad alto potenziale identificati subito |
| **Decisione in 1 click** | Qualifica direttamente da Slack |
| **Contesto completo** | Fatturato, traffico, competitor in un'unica vista |

## Per il Business

| Metrica | Valore |
|---------|--------|
| **Tempo risposta** | Da ore/giorni a minuti |
| **Copertura** | 100% dei deal qualificati |
| **Consistenza** | Criteri oggettivi e ripetibili |
| **Costo per deal** | ~€0.0001 (Claude Haiku) |

---

# Architettura Tecnica (High-Level)

```
┌─────────────────────────────────────────────────────────────┐
│                        HubSpot                               │
│                   (CRM - Deal Source)                        │
└─────────────────────────┬───────────────────────────────────┘
                          │ Webhook
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   Sales Qualifier                            │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐│
│  │   Revenue   │ │   Traffic   │ │   Payment Detection     ││
│  │  Detection  │ │   Analysis  │ │   (BNPL, Providers)     ││
│  │  (4 tier)   │ │  SEMrush +  │ │   Agent Browser +       ││
│  │  VIES/Atoka │ │  SimilarWeb │ │   HTTP Scraping         ││
│  └─────────────┘ └─────────────┘ └─────────────────────────┘│
│                          │                                   │
│                          ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐│
│  │              Claude Haiku (AI Scoring)                  ││
│  │              Score 1-10 + Summary                       ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                        Slack                                 │
│              Report + Pulsanti Interattivi                   │
│              [Automated] [Sales] [HubSpot]                   │
└─────────────────────────────────────────────────────────────┘
```

---

# Integrazioni

| Sistema | Direzione | Scopo |
|---------|-----------|-------|
| **HubSpot** | ↔ Bidirezionale | Riceve deal, scrive qualificazione |
| **Slack** | → Output | Report + interazione team |
| **SEMrush** | ← Input | Dati traffico SEO |
| **SimilarWeb** | ← Input | Visite e engagement |
| **VIES / Atoka** | ← Input | Fatturato aziende |
| **Wappalyzer** | ← Input | Tech stack detection |

---

# Costi Operativi

| Voce | Costo |
|------|-------|
| **Per deal (Haiku)** | ~€0.0001 |
| **100 deal/giorno** | ~€0.01/giorno |
| **1000 deal/mese** | ~€0.10/mese |

**Nota**: Il sistema usa esclusivamente Claude Haiku (modello economico). Nessun costo infrastruttura aggiuntivo (gira su macchina locale).

---

# Sicurezza e Compliance

- **Dati**: Solo dati pubblici (fatturato da registri camerali, traffico web)
- **P.IVA**: Validata tramite VIES ufficiale EU
- **Nessun dato sensibile**: Non accede a dati personali clienti
- **Audit trail**: Ogni qualificazione loggata con timestamp e operatore

---

# Roadmap Futura

| Fase | Funzionalità |
|------|--------------|
| **Q1 2026** | ✅ MVP live (E-commerce + Physical Store scoring) |
| **Q2 2026** | Integrazione CRM score su HubSpot deal card |
| **Q2 2026** | Dashboard analytics qualificazione |
| **Q3 2026** | Multi-country support (non solo Italia) |
| **Q3 2026** | Auto-assignment a Sales rep per score |

---

# Contatti

**Owner**: Growth Tech Team
**Repository**: `sales-qualifier`
**Slack Channel**: #sales-qualifier-alerts

---

*Documento generato il 5 Febbraio 2026*
