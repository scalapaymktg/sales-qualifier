# Sales Qualifier

Sistema autonomo di qualificazione deal per Scalapay. Riceve webhook da HubSpot alla creazione di nuovi deal, esegue analisi multi-fonte (fatturato, payment stack, traffico, BNPL detection) e invia report strutturati su Slack con scoring automatico e pulsanti di qualificazione interattivi.

---

## Indice

- [Changelog](#changelog)
- [Architettura](#architettura)
- [Flusso End-to-End](#flusso-end-to-end)
- [Moduli](#moduli)
- [API Endpoints](#api-endpoints)
- [Enhanced Payment Detection](#enhanced-payment-detection)
- [Revenue Detection](#revenue-detection)
- [Haiku Triage Scoring](#haiku-triage-scoring)
- [Integrazioni Esterne](#integrazioni-esterne)
- [Setup e Installazione](#setup-e-installazione)
- [Configurazione](#configurazione)
- [Deployment](#deployment)
- [Comandi Utili](#comandi-utili)
- [Struttura File](#struttura-file)
- [Limiti Noti](#limiti-noti)

---

## Changelog

### 2026-02-17 - Critical Fix: Infinite loop Slack messages (deal 472789463251)

**Problema**: Deal 472789463251 (ALOHA viaggi e turismo) ha ricevuto **34 messaggi Slack identici** in un loop infinito ogni ~15 secondi.

**Root cause**: Combinazione di 3 bug:
1. **Server restart loop**: `process_pending_deals()` allo startup processava il deal, poi `app.run()` crashava (porta 5001 occupata da istanza precedente). LaunchAgent (KeepAlive=true) riavviava il server, `slack_message_sent` (dict in-memory) veniva azzerato, e il deal veniva riprocessato.
2. **HubSpot search eventual consistency**: La query per `to_start/in_progress/failed` ritornava deal con status `done` perché l'indice di ricerca HubSpot non si aggiornava in tempo reale. Lo status nel risultato era `done` ma il deal era incluso nei risultati.
3. **Dedup non persistente e non thread-safe**: `slack_message_sent` era un semplice `dict` Python senza lock threading e senza persistenza su disco.

**Soluzione (4 fix)**:
1. **Dedup persistente su file** (`.slack_sent_deals.json`): stato salvato su disco, sopravvive ai restart
2. **Threading Lock** (`_dedup_lock`): check-then-set atomico, elimina race condition TOCTOU
3. **Rimosso `in_progress` dalla query**: `process_pending_deals()` cerca solo `to_start`/`failed`
4. **Validazione status**: skip deal il cui status attuale (dal risultato API) non è `to_start`/`failed`

**Modifiche codice**:
- `webhook_server.py`: Aggiunta `_load_dedup_state()`, `_save_dedup_state()`, `_dedup_lock` (threading.Lock)
- `webhook_server.py`: `trigger_agent()` usa `with _dedup_lock:` per check+set atomico
- `webhook_server.py`: `process_pending_deals()` query solo `to_start`/`failed`, skip deals con status diverso
- Nuovo file `.slack_sent_deals.json` (in .gitignore) per persistenza dedup

**Impatto**:
- ✅ Impossibile loop infinito da restart server (stato dedup persistente)
- ✅ Impossibile race condition webhook/scheduler (lock atomico)
- ✅ Impossibile riprocessare deal `done`/`in_progress` via eventual consistency (doppio check)

---

### 2026-02-16 - Removal: Ollama fallback eliminato (inaccurato e fuorviante)

**Problema**: Ollama fallback (scraping search pages + gemma3:4b extraction) restituiva sistematicamente dati sbagliati: aziende con P.IVA diverse (es. "Gestore Dei Mercati Energetici" per beauty center), fatturati assurdi (€14.5 miliardi per negozi piccoli), ragioni sociali non correlate.

**Analisi accuratezza**:
- **20/55 volte** → restituiva `N/D` (inutile)
- **35/55 volte** → "trovava" un valore, ma **100% SBAGLIATO**:
  - "Saras S.p.a." (€9.6 miliardi) → ripetuto 17 volte per aziende diverse
  - "Gestore Dei Mercati Energetici" (€14.5 miliardi) → beauty center e piccole aziende
  - "Lidl Italia" (€7.1 miliardi) → aziende completamente diverse
- Campo `piva_verificata: true` **inaffidabile** (diceva true quando restituiva P.IVA sbagliate)

**Causa**: Ollama stava copiando i primi risultati dalle pagine di ricerca (sempre le stesse grandi aziende) e restituendoli come se fossero l'azienda cercata, ignorando l'istruzione di verificare la P.IVA.

**Soluzione**: Rimosso completamente Ollama fallback. Meglio avere **"N/D" onesto** che **dati sbagliati con confidence falsa**. Le fonti primarie (VIES, fatturatoitalia, ufficiocamerale, registroaziende, Atoka) sono sufficienti.

**Modifiche codice**:
- Rimossa funzione `_ollama_fallback()` (era ~110 righe)
- Rimossa chiamata in `fetch_revenue_from_various_sources()` (STEP 4)
- Mantenuto `_check_ollama()` e `_extract_fatturato_from_detail_page()` per estrazione da pagine dettagliate (più affidabile)
- Aggiornati log di startup per chiarire che Ollama è usato solo per detail pages

**Impatto**:
- ✅ Eliminati falsi positivi su fatturato (aziende sbagliate)
- ✅ Più veloce: -30 secondi quando nessuna fonte primaria trova dati
- ✅ Più onesto: N/D quando non sappiamo, invece di inventare dati
- ⚠️ Meno deal con fatturato (ma solo quelli che prima avevano dati SBAGLIATI)

---

### 2026-02-16 - Feature: Nota HubSpot automatica quando utente qualifica deal via Slack

**Problema**: Quando un utente cliccava i pulsanti Slack (🤖 Automated / 👤 Sales) per qualificare un deal, il sistema aggiornava solo la property `sql_qualifier` ma NON creava alcuna nota sul deal in HubSpot. Impossibile tracciare chi, quando e come ha qualificato il deal direttamente su HubSpot.

**Causa**: La funzione `slack_interactions()` chiamava solo `update_hubspot_deal_property()` ma non aveva logica per creare note HubSpot.

**Soluzione**:
- Creata nuova funzione `create_hubspot_note()` che crea una nota associata a un deal
- Endpoint HubSpot: `POST /crm/v3/objects/notes` con association typeId 214 (Note to Deal)
- Nota include: username, deal name, qualifica (Automated/Sales), timestamp
- Chiamata automatica in `slack_interactions()` dopo update property

**Modifiche codice**:
- Nuova funzione `create_hubspot_note()` (righe 3742-3792) - include property `hs_timestamp` (richiesta da HubSpot API)
- Chiamata a `create_hubspot_note()` in `slack_interactions()` dopo update property (righe 3845-3847)
- Script backfill: `backfill_from_logs.py` per recupero retroattivo qualifiche da log

**Impatto**:
- ✅ Ogni qualifica via Slack ora crea nota permanente su HubSpot
- ✅ Tracciabilità completa: chi (username Slack), cosa (Automated/Sales), quando (timestamp)
- ✅ Note visibili nella timeline del deal in HubSpot UI
- ✅ Formato nota: "{user} ha qualificato {deal_name} come {Automated|Sales} il {DD/MM/YYYY alle HH:MM}"

**Esempio nota**:
```
jessica691 ha qualificato Newmoon (Pay in X) come Automated il 16/02/2026 alle 16:15
```

**Backfill retroattivo**:
- Script `backfill_from_logs.py` eseguito per recuperare qualifiche ultime 24h da `webhook.error.log`
- ✅ **17 note create retroattivamente** con timestamp originale delle qualifiche
- Include deal ID menzionato dall'utente: 472175140069 (Newmoon)

---

### 2026-02-13 - Fix: De-duplicazione Slack a livello applicazione (in-memory tracking)

**Problema**: Deal 468477009128 e altri ricevevano messaggi Slack duplicati nonostante il check `sql_qualifier_status`.

**Causa**: Anche con protezione webhook, possibili race condition o chiamate multiple prima che HubSpot property venga aggiornata.

**Soluzione**:
- Aggiunto dizionario globale `slack_message_sent` (in-memory) che traccia deal ID già processati
- Check pre-invio: se `deal_id in slack_message_sent` → skip invio con log warning
- Tracking post-invio: dopo invio riuscito, aggiungi `deal_id` al dizionario
- Protezione **a livello applicazione** (indipendente da HubSpot API latency)

**Modifiche codice**:
- Dizionario globale `slack_message_sent = {}` (riga ~81)
- Check pre-invio in `trigger_agent()` prima di `send_haiku_report_to_slack()` (righe ~3505-3523)

**Impatto**:
- ✅ Eliminati duplicati Slack anche in caso di race condition webhook
- ✅ Protezione immediata (no dipendenza da HubSpot API)
- ✅ Log chiari per troubleshooting: "⚠️ Slack message già inviato per deal XXX, skip duplicato"

**Limiti**:
- Tracking in-memory (reset a ogni restart del server Flask)
- Per persistenza cross-restart, considerare file JSON o Redis (non necessario al momento)

---

### 2026-02-10 - Enhancement: Validazione multi-livello per revenue detection (fuzzy matching + P.IVA + confidence downgrade)

**Problema**: Falsi positivi su fatturato quando Tavily/WebSearchAPI restituiscono pagina azienda sbagliata (es. Click cafe Italia srl VAT 02694770642 → €96M da CAMAC ARTI GRAFICHE SRL).

**Causa**: Sistema estraeva fatturato senza verificare che:
1. Nome azienda nella pagina corrisponda al nome cercato
2. P.IVA nella pagina corrisponda al P.IVA cercato
3. Con singola fonte + dati non validati → confidence dovrebbe essere LOW, non HIGH

**Soluzione**: Implementati 3 livelli di validazione:

1. **Fuzzy matching (nome azienda)**:
   - Funzione `_fuzzy_match_company_name()` con threshold 60% (usa `difflib.SequenceMatcher`)
   - Normalizzazione: lowercase, rimozione forme giuridiche (srl, spa, snc, sas), punteggiatura
   - Cerca nome in `<h1>`, `<title>`, meta description della pagina
   - Se similarità >= 60% → validazione passata ✅

2. **P.IVA matching**:
   - Funzione `_find_vat_in_html()` cerca P.IVA nella pagina HTML
   - Pattern: `\bVAT\b`, `\bITVAT\b`, `P.IVA: VAT`, `Partita IVA VAT`
   - Se P.IVA trovato nella pagina → validazione passata ✅

3. **Confidence downgrade**:
   - Modificata `_validate_multi_source_revenue()` per downgrade confidence quando:
     - Solo 1 fonte ha trovato dati
     - Confidence originale = HIGH
     - Flag `validated=False` (né fuzzy match né P.IVA match passato)
   - Downgrade: HIGH → LOW con diagnostica "⚠️ Confidence abbassato a LOW - valore da singola fonte non validato (nome/P.IVA non verificato)"

**Modifiche codice**:
- `_fuzzy_match_company_name()`: nuova funzione helper (righe 264-297)
- `_find_vat_in_html()`: nuova funzione helper (righe 300-330)
- `_ufficiocamerale_extract()`: aggiunta validazione post-estrazione (righe 798-950)
- `_registroaziende_extract()`: aggiunta validazione post-estrazione (righe 954-1130)
- `_atoka_extract()`: aggiunta validazione post-estrazione (righe 1133-1510)
- `_validate_multi_source_revenue()`: downgrade logic per singola fonte non validata (righe 332-425)
- `search_company_revenue()`: propagazione flag `validated` a `all_sources` (righe 1512-1755)

**Test**:
- **Click cafe (02694770642)**: Tavily → CAMAC ARTI GRAFICHE SRL ❌ → Confidence LOW (downgrade) ✅
- **Campari/SOGNA (09073100720)**: Tavily → 3P SOLUTIONS SRL ❌ → Confidence LOW (downgrade) ✅
- **GRIVEL (00139110076)**: Tavily → GRIVEL SRL ✅ → P.IVA validato → Confidence HIGH (no downgrade) ✅

**Impatto**:
- ✅ Eliminati falsi positivi da pagine azienda sbagliate (confidence downgrade segnala dato non affidabile)
- ✅ Validazione robusta: doppio check (nome + P.IVA)
- ✅ Diagnostica trasparente: utente vede "(⚠️ nome/P.IVA non verificato)" quando validazione fallisce
- ✅ Nessun impatto su aziende corrette: se P.IVA o nome match → confidence rimane HIGH

---

### 2026-02-09 - Fix: Protezione duplicati webhook HubSpot

**Problema**: Deal processati più volte generando messaggi Slack duplicati (es. Borzì Gianpaolo Concetto).

**Causa**: HubSpot può inviare webhook multipli per lo stesso deal (subscription duplicate, retry automatici). Il sistema non verificava se deal già processato.

**Soluzione**:
- Aggiunto check `sql_qualifier_status` in `check_deal_matches_filters()`
- Se status è `done` o `in_progress` → webhook ignorato con log "⏭️ already {status} - skipping duplicate"
- Protezione efficace contro race condition e webhook duplicati

**Impatto**:
- ✅ Eliminati messaggi Slack duplicati
- ✅ Riduzione carico server (no elaborazioni inutili)
- ✅ Log più chiari per troubleshooting

---

### 2026-02-09 - Fix: Validazione geografica VAT via VIES (fonti italiane solo per P.IVA italiane)

**Problema**: Fonti italiane (ufficiocamerale, registroaziende, atoka, fatturatoitalia) venivano consultate anche per VAT non italiani (es. FR, ES, DE), generando:
- Falsi positivi (es. €96M estratto per VAT francese FR45930881560)
- Sprechi di risorse API (Tavily, WebSearchAPI)
- Diagnostica confusa

**Soluzione**:
- **VIES come fonte autoritativa**: chiamata VIES per primo, estrazione country code da `originalVatNumber`
- Se VAT senza prefisso (es. "00139110076") → assume `IT` (default Italia), poi VIES conferma/corregge
- Se VAT con prefisso (es. "FR45930881560") → VIES valida e restituisce country code
- Fonti italiane consultate SOLO se `country_code == "IT"` da VIES response
- Diagnostica migliorata: messaggio esplicito "VAT XX: fonti italiane non consultate"

**Impatto**:
- ✅ Eliminati falsi positivi su VAT esteri (FR45930881560: ora N/D invece di €96M)
- ✅ Supporto VAT senza prefisso (00139110076 funziona come IT00139110076)
- ✅ Riduzione consumi API (no chiamate inutili per VAT non italiani)
- ✅ Country code autoritativo da registro UE VIES

**Test**:
- `FR45930881560` → VIES=FR, fonti IT saltate, N/D ✅
- `IT00139110076` → VIES=IT, fonti IT consultate, €5.045.628 ✅
- `00139110076` → assume IT, VIES conferma IT, €5.045.628 ✅

---

### 2026-02-09 - Enhancement: Playwright fallback + Tavily + WebSearchAPI

**Problema**: ufficiocamerale.it bloccava requests con HTTP 403 (anti-bot), impedendo estrazione dati fatturato.

**Soluzione**:
- Implementato **Playwright** (browser headless Chromium) come fallback automatico quando requests riceve 403
- Sostituito DuckDuckGo con **Tavily API** (1000 query/mese gratuite, ottimizzata per AI agents) per ricerca URL
- Aggiunto **WebSearchAPI** come fallback automatico a Tavily (piano free generoso)
- Aggiunto pattern ricerca `/ricerca?q=VAT` per registroaziende.it (aumenta coverage)
- Aggiornato regex extraction per matchare struttura HTML di ufficiocamerale.it (`<strong>€&nbsp;5.045.628,00</strong>`)

**Impatto**:
- ✅ Bypassato blocco 403 su ufficiocamerale.it (testato con GRIVEL S.R.L., VAT IT00139110076: fatturato €5.045.628)
- Maggiore affidabilità ricerca URL (Tavily vs DuckDuckGo rate limiting)
- Coverage revenue detection aumentata per aziende con bilanci depositati alla Camera di Commercio

**Dipendenze**: Playwright richiede `playwright` Python package + `chromium` browser (~250MB download).

---

### 2026-02-06 - Enhancement: Nuovi fallback revenue detection

**Feature**: Aggiunti 2 nuovi tier alla catena di revenue detection.

**Nuove fonti**:
- **ufficiocamerale.it** (Tier 3 via DuckDuckGo)
- **registroaziende.it** (Tier 4 via DuckDuckGo)

**Impatto**: Maggiore coverage per aziende non presenti su fatturatoitalia.it. La catena ora ha 6 tier totali (era 4) con più fonti di fallback.

---

### 2026-02-06 - Fix: Payment detection false positives

**Problema**: "Alma" rilevato come BNPL competitor su siti luxury/fashion (false positive da "Louis Vuitton Alma" bag model).

**Soluzione**:
- Rimosso "alma" dalle keyword BNPL (troppo generico)
- Aggiunto "heylight" alle keyword BNPL

**Impatto**: Detection più accurata, meno falsi positivi su siti luxury. Provider BNPL "Alma" (Francia) non più rilevato automaticamente.

---

### 2026-02-05 - Fix: Import modulo `re` mancante

**Problema**: Deal rimanevano bloccati in status `failed` o `in_progress` con errore `name 're' is not defined`.

**Causa**: Il modulo Python `re` (regular expressions) veniva utilizzato in 20+ punti del codice (revenue detection, domain parsing, etc.) ma non era stato importato.

**Soluzione**: Aggiunto `import re` alla riga 8 di `webhook_server.py`.

**Impatto**: Tutti i deal pendenti ora completano correttamente il processo di qualifica e ricevono status `done` dopo l'invio del report a Slack.

---

## Architettura

Il sistema e' organizzato in 4 livelli con escalation progressiva:

```
                         HubSpot Webhook
                         deal.creation
                              │
                              ▼
                    ┌─────────────────────┐
                    │  Flask Server        │
                    │  POST /webhook/hubspot│
                    │  Filtro: pipeline +  │
                    │  generic_source      │
                    └─────────┬───────────┘
                              │
                              ▼
            ┌─────────────────────────────────────┐
            │         trigger_agent()              │
            │  Esecuzione parallela:               │
            │                                      │
            │  ┌────────────┐  ┌────────────────┐  │
            │  │ Revenue    │  │ Payment Stack  │  │
            │  │ Detection  │  │ Detection      │  │
            │  │ (VIES +    │  │ (agent-browser │  │
            │  │ Web Scrape │  │ + HTTP fetch)  │  │
            │  │ + Ollama)  │  │                │  │
            │  └────────────┘  └────────────────┘  │
            │                                      │
            │  ┌────────────┐  ┌────────────────┐  │
            │  │ SEMrush    │  │ SimilarWeb     │  │
            │  │ Traffic    │  │ Traffic        │  │
            │  │ API        │  │ API            │  │
            │  └────────────┘  └────────────────┘  │
            │                                      │
            │  ┌────────────────────────────────┐  │
            │  │ Wappalyzer                     │  │
            │  │ Technology Detection           │  │
            │  │ (CMS, Payment, Analytics, FW)  │  │
            │  └────────────────────────────────┘  │
            └─────────────────┬───────────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │  Haiku Triage       │
                    │  Claude Haiku CLI   │
                    │  Score 1-10         │
                    │  4 criteri obbligat.│
                    └─────────┬───────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │  Slack Report       │
                    │  Block Kit message  │
                    │  + Pulsanti:        │
                    │  [Automated] [Sales]│
                    │  [Apri HubSpot]     │
                    └─────────┬───────────┘
                              │
                              ▼ (click pulsante)
                    ┌─────────────────────┐
                    │  HubSpot Update     │
                    │  1. sql_qualifier = │
                    │     "automated"|    │
                    │     "sales"         │
                    │  2. Crea nota       │
                    │     (traccia user,  │
                    │      timestamp)     │
                    │  3. Conferma thread │
                    └─────────────────────┘

    ┌─────────────────────────────────────────────┐
    │         sql_qualifier_status lifecycle       │
    │                                             │
    │  HubSpot workflow    Server startup          │
    │  sets "to_start" ──► process_pending_deals() │
    │                      + ogni 10 min (bg thread)│
    │                              │               │
    │  Processa anche:             │               │
    │  - "in_progress" (interrotti)│               │
    │  - "failed" (retry)          ▼               │
    │                      "in_progress"           │
    │                              │               │
    │                     ┌────────┴────────┐      │
    │                     ▼                 ▼      │
    │                  "done"           "failed"   │
    │              (Slack OK)   (errore, retry)    │
    └─────────────────────────────────────────────┘
```

---

## Flusso End-to-End

1. **HubSpot workflow** crea deal e imposta `sql_qualifier_status = to_start` + chiama webhook
2. **Server Flask** riceve webhook `deal.creation`, filtra pipeline `77766861` + source `Marketing - Interactions & Inbound requests`
3. **Status update**: `sql_qualifier_status` → `in_progress`
4. **Data fetch**: recupera proprietà deal + dati company da HubSpot API
5. **Analisi parallela** (5 moduli indipendenti):
   - **Revenue**: VIES → fatturatoitalia.it → ufficiocamerale.it (Tavily + Playwright fallback) → registroaziende.it (Tavily) → Atoka (Tavily) + multi-source validation + diagnostica
   - **Payment stack**: agent-browser 3-step (HP → PDP → Checkout) + HTTP fallback
   - **SEMrush**: rank, keywords, traffico organico/paid
   - **SimilarWeb**: visite mensili (split IT vs Estero con YoY), engagement, fonti traffico, competitor
   - **Wappalyzer**: technology detection (CMS, payment, analytics, framework)
6. **Haiku triage**: Claude Haiku riceve tutti i dati e produce score 1-10 con analisi
7. **Slack**: messaggio formattato con Block Kit, pulsanti interattivi, costi giornalieri
8. **Status update**: `sql_qualifier_status` → `done` (successo) o `failed` (errore)
9. **Interazione**: click pulsante su Slack → `POST /slack/interactions` → aggiorna `sql_qualifier` su HubSpot + crea nota HubSpot (traccia chi/quando/come) + conferma pubblica in thread

**Recovery offline**: all'avvio del server e ogni 10 minuti, `process_pending_deals()` cerca deal con `sql_qualifier_status = to_start`, `in_progress` (interrotti) o `failed` (da riprovare) e li processa automaticamente. Questo garantisce che nessun deal venga perso se la macchina era spenta quando il workflow HubSpot li ha creati.

---

## Moduli

### `webhook_server.py` (3895 righe) — Orchestratore principale

Contiene il server Flask, tutta la logica di triage Haiku, revenue detection, payment detection, integrazioni API, e gestione lifecycle deal (`sql_qualifier_status`).

**Funzioni principali:**

| Funzione | Riga | Descrizione |
|----------|------|-------------|
| `trigger_agent()` | 3449 | Orchestratore: revenue + payments + SEMrush + SimilarWeb + Wappalyzer + Haiku + de-duplicazione Slack + status lifecycle |
| `process_pending_deals()` | 3541 | Cerca deal con `sql_qualifier_status = to_start/in_progress/failed` su HubSpot e li processa |
| `hubspot_webhook()` | 3609 | Endpoint webhook HubSpot, filtra deal, trigger agent |
| `slack_interactions()` | 3790 | Handler pulsanti Slack → update HubSpot + crea nota + conferma pubblica in thread |
| `create_hubspot_note()` | 3742 | Crea nota HubSpot associata a deal (usato dopo qualifica utente) |
| `update_hubspot_deal_property()` | 3715 | Aggiorna singola property su deal HubSpot |
| `process_pending_endpoint()` | 3833 | Endpoint manuale per trigger processing deal pendenti |
| `_start_pending_scheduler()` | 3839 | Background thread: controlla deal pendenti ogni 10 minuti |
| `triage_with_haiku()` | 2377 | Chiama Claude Haiku CLI, produce JSON con score 1-10 |
| `get_haiku_usage_stats()` | 2576 | Calcola costi per deal e cumulativi giornalieri (solo Haiku) |
| `send_haiku_report_to_slack()` | 2647 | Formatta report Slack con Block Kit + pulsanti interattivi |
| `enhanced_payment_detection()` | 1760 | BNPL detection 3-step con agent-browser + HTTP fallback |
| `search_company_revenue()` | 1512 | Revenue lookup 5-tier (VIES → fatturatoitalia → ufficiocamerale → registroaziende → Atoka) + multi-source validation |
| `_validate_multi_source_revenue()` | 332 | Validazione coerenza multi-fonte + confidence downgrade per fonte singola non validata |
| `_fuzzy_match_company_name()` | 264 | Helper: validazione nome azienda via fuzzy matching (difflib, threshold 60%) |
| `_find_vat_in_html()` | 300 | Helper: validazione P.IVA nella pagina HTML (pattern matching) |
| `_tavily_search()` | 130 | Helper: ricerca URL via Tavily API (1000 query/mese free, ottimizzata per AI agents) |
| `_websearch_api_search()` | 180 | Helper: ricerca URL via WebSearchAPI (fallback automatico a Tavily, piano free generoso) |
| `_fetch_with_playwright()` | 750 | Helper: fetch HTML con Playwright (Chromium headless) per bypassare anti-bot (es. HTTP 403) |
| `_ufficiocamerale_extract()` | 798 | Tavily/WebSearch → scrape ufficiocamerale.it con Playwright fallback se 403 + validazione nome/P.IVA |
| `_registroaziende_extract()` | 954 | Accesso diretto pattern URL + Tavily/WebSearch fallback → scrape registroaziende.it + validazione nome/P.IVA |
| `_atoka_extract()` | 1133 | Accesso diretto pattern URL + Tavily/WebSearch fallback → scrape Atoka (JSON-LD) + validazione nome/P.IVA |
| `get_semrush_traffic()` | 3004 | SEMrush API: rank, keywords, traffico |
| `get_similarweb_traffic()` | 3187 | SimilarWeb API: visite (split IT/Estero + YoY), engagement, competitor |
| `_get_similarweb_visits()` | 3119 | Helper: endpoint visits per country con periodo corrente + precedente |
| `get_wappalyzer_tech()` | 3374 | Wappalyzer: technology detection (CMS, payment, analytics, framework) |

### `agent.py` (419 righe) — Agente autonomo Opus (non attivo)

Entry point alternativo, non usato in produzione. Usa Claude Sonnet tramite Anthropic SDK (`ANTHROPIC_API_KEY` non configurata) con tool use (fetch_website, web_search, send_to_slack) per analisi approfondita dei deal. Troppo costoso, sostituito dal flusso Haiku CLI in `webhook_server.py`.

**Funzioni principali:**

| Funzione | Descrizione |
|----------|-------------|
| `get_new_deals()` | Cerca deal creati oggi su HubSpot (pipeline + source) |
| `analyze_deal_with_agent()` | Loop tool-use: fetch → search → report Slack |
| `execute_tool()` | Router per fetch_website, web_search, send_to_slack |
| `save_processed_deal()` | Stato persistente in `processed_deals.json` |

### `deal_monitor.py` (579 righe) — Monitor programmato (non attivo)

Entry point alternativo con polling ogni 5 minuti. Usa Claude Haiku tramite API diretta (non CLI), ma `ANTHROPIC_API_KEY` non e' configurata, quindi il modulo non e' funzionante. Invia report direttamente su Slack senza pulsanti.

**Funzioni principali:**

| Funzione | Descrizione |
|----------|-------------|
| `check_for_new_deals()` | Polling HubSpot + analisi + Slack |
| `fetch_website_content()` | HTTP fetch + HTML parsing |
| `analyze_website_with_claude()` | Haiku API diretta per analisi (non attiva, richiede ANTHROPIC_API_KEY) |

### `checkout_simulator.py` (363 righe) — Simulatore checkout standalone

Modulo standalone per navigazione e-commerce via agent-browser (Playwright). Usato come riferimento per la logica integrata in `enhanced_payment_detection()`.

**Funzioni principali:**

| Funzione | Descrizione |
|----------|-------------|
| `analyze_checkout()` | Flow 6-step: open → detect ecommerce → product → cart → checkout → payments |
| `find_element_ref()` | Parsing accessibility tree per ref elementi cliccabili |

### `send_slack_report.py` (361 righe) — Report post-elaborazione

Parser di `agent.log`, calcolo costi/usage token, formattazione Slack.

**Funzioni principali:**

| Funzione | Descrizione |
|----------|-------------|
| `extract_latest_report()` | Estrae ultimo report da agent.log |
| `get_usage_stats()` | Calcola token e costi |
| `convert_markdown_to_slack()` | Trasforma markdown → Slack mrkdwn |

---

## API Endpoints

Il server Flask espone 6 endpoint:

| Metodo | Path | Descrizione |
|--------|------|-------------|
| `POST` | `/webhook/hubspot` | Riceve webhook HubSpot `deal.creation`, filtra e lancia triage |
| `POST/GET` | `/webhook/test` | Trigger manuale con dati di esempio |
| `POST/GET` | `/webhook/test-slack` | Test invio messaggio Slack |
| `POST/GET` | `/webhook/process-pending` | Processa manualmente tutti i deal con `sql_qualifier_status = to_start` |
| `POST` | `/slack/interactions` | Handler pulsanti Slack (payload form-encoded) |
| `GET` | `/health` | Health check: stato server + Ollama |

### Autenticazione webhook

Il webhook HubSpot include header `X-HubSpot-Signature-v3` verificato con HMAC-SHA256 contro `HUBSPOT_CLIENT_SECRET`. La verifica e' opzionale (log warning se assente).

### Slack interactions

Il payload Slack arriva come form-encoded con campo `payload` (JSON stringificato). I pulsanti inviano:
- `action_id`: `qualify_automated` | `qualify_sales` | `open_hubspot`
- `value`: `{deal_id}|{qualification}|{deal_name}`

L'handler:
1. Aggiorna la proprietà `sql_qualifier` su HubSpot (`automated` o `sales`)
2. Invia un messaggio pubblico nel thread Slack (visibile a tutti) con conferma:
   `"✅ {nome_utente} ha qualificato {deal_name} come Automated/Sales il dd/mm/yyyy alle HH:MM"`
   Usa `chat.postMessage` con `thread_ts` (non ephemeral).

---

## Enhanced Payment Detection

Funzione core per identificare provider BNPL (Klarna, Scalapay, Clearpay, Afterpay, ecc.) sui siti e-commerce. Architettura ibrida agent-browser + HTTP.

### 3 Step obbligatori

```
Step 1: Homepage
  └─ HTTP fetch homepage → analisi HTML per payment/BNPL keywords
  └─ agent-browser open + snapshot (per trovare link prodotto)

Step 2: Product Page (PDP)
  └─ Multi-layer product discovery:
     ├─ 2a: Regex su HTML (find_product_urls_from_html)
     ├─ 2b: Haiku CLI analisi HTML (haiku_find_product_url)
     ├─ 2c: Collection drill-down (fetch collection → regex/Haiku interno)
     └─ 2d: Snapshot-based discovery (accessibility tree parsing)
  └─ HTTP fetch pagina prodotto trovata

Step 3: Cart + Checkout
  └─ JS eval per click "Aggiungi al carrello" (bypassa timeout Playwright)
  └─ JS eval per click "Checkout" / "Procedi al pagamento"
  └─ HTTP fallback diretto su path comuni:
     /cart, /carrello, /basket, /shopping-cart,
     /checkout, /cassa, /payment, /order
```

### Provider rilevati

**Payment providers** (esempi principali): Stripe, PayPal, Adyen, Nexi, Checkout.com, Braintree, Square, Satispay, Apple Pay, Google Pay, PostePay. Il report include tutti i provider trovati nel sito.

**BNPL providers** (esempi principali): Klarna, Clearpay, Afterpay, Scalapay, Alma, Oney, PagoLight, Cofidis, Soisy, PayPal Pay in 3, Pay in 4. Il report include tutti i BNPL trovati nel sito.

### Output

```python
{
    "providers": ["Stripe", "PayPal", "Apple Pay"],
    "has_bnpl": True,
    "bnpl_providers": ["Klarna"],
    "bnpl_locations": {
        "homepage": False,
        "pdp": False,
        "checkout": True      # Klarna trovato al checkout
    },
    "confidence": {
        "score": 95,           # 0-100
        "label": "very_high",  # low/medium/high/very_high
        "reason": "BNPL trovato al checkout via browser"
    },
    "method": "agent-browser",
    "blocked_by": None         # oppure "Cloudflare"
}
```

### Bot protection detection

Rileva Cloudflare e altre protezioni bot tramite:
- Header `cf-ray`, `server: cloudflare` nella response HTTP
- Pattern testo nel body (`"Verify you are human"`, `"cf-browser-verification"`)
- Contenuto snapshot agent-browser (`"Verifying you are human"`)

Siti bloccati ricevono `confidence.score: 20` e `blocked_by: "Cloudflare"`.

### JS eval bypass

L'agent-browser (basato su Playwright) ha un timeout di 10s hardcoded sui click che attendono navigazione. Per azioni AJAX (aggiungi al carrello), il click non genera navigazione e va in timeout. La soluzione usa `agent-browser eval 'javascript'` che esegue `.click()` direttamente nel DOM, bypassando il timeout.

```python
# Add-to-cart: cerca button per testo, poi selettori Shopify
add_js = '''(function(){
  var b=document.querySelectorAll("button,[role=button],input[type=submit]");
  var k=["aggiungi al carrello","add to cart","acquista ora","buy now",
         "buy it now","compra ora"];
  for(var i=0;i<b.length;i++){
    var t=b[i].textContent.toLowerCase().trim();
    for(var j=0;j<k.length;j++){
      if(t.indexOf(k[j])>=0){b[i].click();return"clicked:"+t.substring(0,40)}}
  }
  var s=document.querySelector("[name=add],.product-form__submit,[data-add-to-cart]");
  if(s){s.click();return"shopify"}
  return"none"
})()'''
```

---

## Revenue Detection

Ricerca fatturato aziendale con 6 tier + diagnostica per ogni step:

```
Tier 1: VIES API
  └─ EU VAT registry (ec.europa.eu)
  └─ Restituisce: ragione sociale ufficiale
  └─ Non contiene fatturato, usato per company name match
  └─ Diagnostica: "P.IVA valida" o "P.IVA non valida"

Tier 2: fatturatoitalia.it
  └─ URL diretto: fatturatoitalia.it/{slug}-{partita_iva}
  └─ 4 pattern regex (A/B/C/D):
     A: meta tag description
     B: <b>/<strong> con "fatturato" + cifra
     C: pattern generico "fatturato" + €
     D: Ollama gemma3:4b su testo pagina
  └─ Diagnostica: "fatturato trovato" o "azienda non trovata"

Tier 3: ufficiocamerale.it (via DuckDuckGo) 🆕
  └─ DuckDuckGo search: "{azienda} {vat} site:ufficiocamerale.it"
  └─ Scrape pagina azienda (HTML parsing)
  └─ Estrae fatturato da tabelle/div strutturati
  └─ Diagnostica: "fatturato trovato" o "azienda non trovata"

Tier 4: registroaziende.it (via DuckDuckGo) 🆕
  └─ DuckDuckGo search: "{azienda} {vat} site:registroaziende.it"
  └─ Scrape pagina azienda (HTML parsing)
  └─ Estrae fatturato da strutture HTML
  └─ Diagnostica: "fatturato trovato" o "azienda non trovata"

Tier 5: Atoka (via DuckDuckGo)
  └─ DuckDuckGo search: "{azienda} fatturato site:atoka.io"
  └─ Scrape pagina pubblica Atoka (JSON-LD FAQ)
  └─ Estrae ricavi/fatturato da dati strutturati
  └─ Diagnostica: "fatturato trovato su Atoka" o "DDG rate-limited"

⚠️ Tier 6 (Ollama fallback) RIMOSSO: era inaffidabile (restituiva aziende/P.IVA sbagliate).
   Se nessuna fonte primaria trova dati → restituisce N/D (meglio che dati sbagliati).
```

La diagnostica completa viene mostrata nel report Slack nella sezione Revenue.

**Output:**
```python
{
    "fatturato": "€12.500.000",
    "anno_bilancio": "2024",
    "ragione_sociale": "AZIENDA SRL",
    "source": "VIES + fatturatoitalia.it",  # o "ufficiocamerale.it", "registroaziende.it", "Atoka"
    "diagnostics": [
        "VIES: P.IVA valida, ragione sociale = AZIENDA SRL",
        "fatturatoitalia.it: fatturato trovato (€12.500.000)"
    ]
}
```

---

## Haiku Triage Scoring

Claude Haiku (4.5) riceve tutti i dati raccolti e produce uno score 1-10 basato su 4 criteri obbligatori.

### Criteri E-COMMERCE (tutti e 4 necessari per score 7-10)

| # | Criterio | Soglia | Fonte |
|---|----------|--------|-------|
| 1 | **Fatturato** | > €1M annuo | Revenue detection |
| 2 | **Payment stack** | Provider moderno (Stripe, Adyen, Nexi...) | Payment detection |
| 3 | **AOV** | > €120 medio | Stima da categoria/brand |
| 4 | **Tech stack** | Shopify o WooCommerce | Wappalyzer |

La categoria merceologica NON e' un criterio di scoring per e-commerce.

### Criteri PHYSICAL STORE (solo fatturato, score deterministico)

| Fatturato | Score |
|-----------|-------|
| N/D | 2 |
| < €500K | 3 |
| €500K - €1M | 5 |
| €1M - €5M | 6 |
| €6M | 7 |
| €7M | 8 |
| €8M | 9 |
| >= €9M | 10 |

Score calcolato in Python, Haiku genera solo il summary.

### Fasce di punteggio (E-commerce)

| Score | Significato |
|-------|-------------|
| 7-10 | Alto potenziale, tutti i 4 criteri soddisfatti |
| 4-6 | Potenziale medio, criteri parziali |
| 1-3 | Basso potenziale, non retail o dati mancanti |

### Output Haiku

```json
{
  "score": 8,
  "is_ecommerce": true,
  "monthly_visits": 125000,
  "has_bnpl_competitor": true,
  "category": "Fashion",
  "summary": "E-commerce fashion con fatturato >€10M, Klarna già integrato, AOV alto (~€150). Ottimo fit per Scalapay."
}
```

### Costi

Il sistema usa esclusivamente Claude Haiku (nessun Opus). Il report Slack mostra:
- **Costo per deal**: `€{cost}` + token count
- **Costo giornaliero cumulativo**: `€{today_cost}` + numero deal analizzati

| Modello | Costo per deal | Token medi |
|---------|---------------|------------|
| Haiku (unico modello) | ~€0.0001 | 250-500 |

Pricing Haiku: $0.25 / $1.25 per 1M token (input / output).

---

## Integrazioni Esterne

### API utilizzate

| Servizio | Endpoint | Scopo | Autenticazione |
|----------|----------|-------|----------------|
| HubSpot CRM | `api.hubapi.com` | Deal/Company CRUD, property updates | Bearer token |
| Slack API | `slack.com/api` | Messaggi, componenti interattivi | Bearer token |
| EU VIES | `ec.europa.eu/taxation_customs/vies/rest-api` | Validazione P.IVA, ragione sociale | Nessuna (pubblico) |
| fatturatoitalia.it | `fatturatoitalia.it` | Fatturato aziende italiane | HTTP scraping |
| reportaziende.it | `reportaziende.it` | Dati aziendali italiani | HTTP scraping |
| ufficiocamerale.it | `ufficiocamerale.it` | Dati camerali (Camera di Commercio). Usa Playwright fallback per bypassare HTTP 403 | HTTP scraping + Playwright |
| Atoka (Cerved) | `atoka.io/public` | Fatturato, ricavi, dati societari (via Tavily/WebSearch) | Nessuna (pubblico) |
| Tavily API | `api.tavily.com/search` | Ricerca URL per ufficiocamerale/registroaziende/Atoka (sostituisce DuckDuckGo) | API key (1000 query/mese free) |
| WebSearchAPI | `api.websearchapi.ai/ai-search` | Fallback automatico a Tavily per ricerca URL (piano free generoso, 1000 credits/mese) | Bearer token |
| Playwright | Locale (Chromium headless) | Browser automation per bypassare anti-bot su ufficiocamerale.it (fallback automatico su 403) | Nessuna |
| SEMrush | `api.semrush.com` | Rank, keywords, traffico organico/paid | API key |
| SimilarWeb | `api.similarweb.com` | Visite (split IT/Estero + YoY), engagement, competitor, fonti traffico | API key |
| Google Serper | `google.serper.dev` | Risultati ricerca web | API key |
| Ollama | `localhost:11434` | LLM locale (gemma3:4b) per estrazione da detail pages (fallback search RIMOSSO) | Nessuna |
| Claude CLI | Binary locale | Haiku per triage (Opus disponibile ma non usato) | Configurazione locale CLI |
| agent-browser | CLI (Vercel Labs) | Navigazione e-commerce headless | Nessuna |
| Wappalyzer | Libreria Python (`wappalyzer-next`) | Technology detection: identifica CMS, payment, analytics, framework | Nessuna (open source) |

### agent-browser

Tool CLI open source di [Vercel Labs](https://github.com/vercel-labs/agent-browser), costruito su Playwright. **Non autonomo**: richiede LLM esterno per ogni decisione di navigazione.

Comandi usati:
- `agent-browser --session <id> open <url>` — apre URL
- `agent-browser --session <id> snapshot` — accessibility tree della pagina
- `agent-browser --session <id> screenshot` — cattura schermo
- `agent-browser --session <id> click @e<N>` — click su elemento (timeout 10s hardcoded)
- `agent-browser --session <id> eval '<js>'` — esegue JavaScript (no timeout, usato per bypass)

### Ollama

LLM locale per estrazione revenue da **pagine dettagliate specifiche** dell'azienda (più affidabili). Modello: `gemma3:4b` (leggero, 4B parametri).

⚠️ **Fallback search pages RIMOSSO**: l'estrazione da pagine di ricerca generiche (fatturatoitalia, reportaziende) è stata rimossa perché restituiva sistematicamente dati sbagliati (aziende diverse, P.IVA errate). Mantenuto solo per detail pages dove il contesto è più affidabile.

---

## Setup e Installazione

### Prerequisiti

- Python 3.9+
- Node.js (v24+, via NVM) — per agent-browser
- Ollama installato e attivo con modello `gemma3:4b`
- ngrok — per esporre il webhook pubblicamente
- Claude CLI (installato via estensione Cursor/VS Code)
- agent-browser (`npm install -g @anthropic-ai/agent-browser` o equivalente)

### Installazione

```bash
# Clone repo
git clone <repo-url>
cd sales-qualifier

# Dipendenze Python
pip install -r requirements.txt
# N.B.: flask e' una dipendenza implicita, installarla manualmente:
pip install flask
# anthropic SDK serve solo per agent.py e deal_monitor.py (non attivi):
# pip install anthropic

# Configurazione
cp .env.example .env
# Editare .env con i propri token (vedi sezione Configurazione)

# Ollama (se non installato)
brew install ollama    # macOS
ollama serve           # avvia server
ollama pull gemma3:4b  # scarica modello

# Verifica
python3 -c "from webhook_server import _check_ollama; print(_check_ollama())"
```

---

## Configurazione

### Variabili d'ambiente (`.env`)

```bash
# === OBBLIGATORIE ===

# HubSpot private app token (scope: crm.objects.deals.read/write, crm.objects.companies.read)
HUBSPOT_TOKEN=pat-eu1-xxxx

# Slack Bot token (scope: chat:write, chat:write.public)
SLACK_BOT_TOKEN=xoxb-xxxx

# === OPZIONALI ===

# Canale Slack (default: C0A9K3A9WA3)
SLACK_CHANNEL=C0A9K3A9WA3

# HubSpot webhook signature secret
HUBSPOT_CLIENT_SECRET=xxxx

# Anthropic API key (usata solo da deal_monitor.py e agent.py, NON da webhook_server.py)
# Attualmente non configurata — questi moduli non sono attivi
ANTHROPIC_API_KEY=sk-ant-xxxx

# Tavily API key (ricerca URL per revenue detection, 1000 query/mese free)
TAVILY_API_KEY=tvly-dev-xxxx

# WebSearchAPI key (fallback automatico a Tavily, piano free generoso)
WEBSEARCH_API_KEY=wsa_xxxx

# Google Serper API key (per web search in agent.py)
SERPER_API_KEY=xxxx

# Porta Flask (default: 5001)
PORT=5001
```

### Costanti hardcoded in `webhook_server.py`

| Costante | Valore | Descrizione |
|----------|--------|-------------|
| `TARGET_PIPELINE_ID` | `77766861` | ID pipeline HubSpot da monitorare |
| `TARGET_GENERIC_SOURCE` | `Marketing - Interactions & Inbound requests` | Filtro source |
| `SEMRUSH_API_KEY` | Hardcoded | API key SEMrush |
| `SIMILARWEB_API_KEY` | Hardcoded | API key SimilarWeb |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Endpoint Ollama locale |
| `OLLAMA_MODEL` | `gemma3:4b` | Modello LLM locale |

### HubSpot Custom Properties

Il sistema legge e scrive queste proprietà custom sui deal:
- `generic_source` (read) — filtro sorgente deal
- `vatnumber` (read) — partita IVA
- `domain` (read) — dominio sito web
- `macro_category` (read) — categoria merceologica
- `store_type` (read) — tipo negozio (online/fisico)
- `product_inbound_request` (read) — prodotto richiesto
- `online_annual_revenue` (read) — fatturato online dichiarato
- `offline_annual_revenue` (read) — fatturato offline dichiarato
- `sql_qualifier` (write) — qualificazione: `"automated"` | `"sales"`
- `sql_qualifier_status` (read/write) — stato lifecycle: `"to_start"` | `"in_progress"` | `"done"` | `"failed"`
- `sql_qualifier_json` (write) — JSON completo del messaggio Slack (blocks + triage data)

---

## Deployment

### Avvio manuale

```bash
# Terminal 1: Flask server
./start_webhook.sh
# oppure:
SLACK_BOT_TOKEN="xoxb-xxx" python3 webhook_server.py

# Terminal 2: ngrok tunnel
ngrok http 5001

# Configurare HubSpot webhook URL: https://<ngrok-id>.ngrok.io/webhook/hubspot
# Configurare Slack interactivity URL: https://<ngrok-id>.ngrok.io/slack/interactions
```

### Avvio automatico al boot (macOS LaunchAgent)

Il server Flask si avvia automaticamente al login e si riavvia in caso di crash.

```bash
# Installa LaunchAgent per webhook server (auto-start + KeepAlive)
cp com.scalapay.webhook-server.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.scalapay.webhook-server.plist

# Comandi gestione:
launchctl load ~/Library/LaunchAgents/com.scalapay.webhook-server.plist     # start
launchctl unload ~/Library/LaunchAgents/com.scalapay.webhook-server.plist   # stop
launchctl list | grep webhook-server                                         # status
```

Il plist configura:
- `RunAtLoad: true` — avvia al login macOS
- `KeepAlive: true` — riavvia automaticamente se il processo muore
- Variabili d'ambiente: `HUBSPOT_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL`
- Log: `webhook.log` (stdout), `webhook.error.log` (stderr)

All'avvio, il server automaticamente:
1. Controlla Ollama (health check)
2. Cerca deal pendenti (`sql_qualifier_status = to_start`) e li processa
3. Avvia background scheduler (check pendenti ogni 10 min)

### Polling automatico agente (macOS LaunchAgent)

```bash
# Installa LaunchAgent (esegue run_agent.sh ogni 5 min)
./setup_scheduler.sh

# Comandi gestione:
launchctl load ~/Library/LaunchAgents/com.scalapay.deal-qualifier.plist     # start
launchctl unload ~/Library/LaunchAgents/com.scalapay.deal-qualifier.plist   # stop
launchctl list | grep deal-qualifier                                        # status
```

### Health check

```bash
./check_status.sh

# oppure:
curl http://localhost:5001/health
# {"status":"healthy","ollama":{"available":true,"model_loaded":true,"error":null}}
```

---

## Comandi Utili

```bash
# Test payment detection su un dominio
python3 -c "
from webhook_server import enhanced_payment_detection
import json
result = enhanced_payment_detection('www.example.com')
print(json.dumps(result, indent=2))
"

# Test revenue detection
python3 -c "
from webhook_server import search_company_revenue
result = search_company_revenue('12345678901', 'Azienda SRL')
print(result)
"

# Test Slack
curl http://localhost:5001/webhook/test-slack

# Test webhook manuale
curl -X POST http://localhost:5001/webhook/test

# Process deal pendenti manualmente
curl http://localhost:5001/webhook/process-pending

# Logs
tail -f webhook.log     # server Flask
tail -f agent.log        # agente autonomo
tail -f agent.error.log  # errori agente

# Gestione LaunchAgents
launchctl list | grep scalapay                                               # status tutti
launchctl unload ~/Library/LaunchAgents/com.scalapay.webhook-server.plist    # stop server
launchctl load ~/Library/LaunchAgents/com.scalapay.webhook-server.plist      # start server
```

---

## Struttura File

```
sales-qualifier/
├── webhook_server.py          # Server Flask + orchestratore triage (3895 righe)
├── agent.py                   # Agente autonomo Opus (non attivo) (419 righe)
├── deal_monitor.py            # Monitor polling + Haiku API (non attivo) (579 righe)
├── checkout_simulator.py      # Simulatore checkout standalone (363 righe)
├── send_slack_report.py       # Formatter report + usage tracker (361 righe)
│
├── start_webhook.sh           # Avvia server Flask
├── run_agent.sh               # Lancia agente Claude CLI
├── check_status.sh            # Health check Flask + ngrok
├── setup_scheduler.sh         # Installa macOS LaunchAgent
├── test_claude.sh             # Test binario Claude
│
├── .env                       # Variabili d'ambiente (gitignored)
├── .env.example               # Template variabili d'ambiente
├── requirements.txt           # Dipendenze Python
├── .gitignore                 # Esclude .env
│
├── com.scalapay.webhook-server.plist  # macOS LaunchAgent: auto-start webhook server
├── com.scalapay.deal-qualifier.plist  # macOS LaunchAgent: polling agente ogni 5 min
├── HAIKU_SCORING_INSTRUCTIONS.md      # Documentazione scoring Haiku
│
├── credentials.json           # Google OAuth credentials
├── token.json                 # Google OAuth token
├── processed_deals.json       # Stato deal processati (agent.py)
│
├── webhook.log                # Log server Flask
├── agent.log                  # Log agente autonomo
├── agent.error.log            # Errori agente
├── server.log                 # Log generico
├── usage.log                  # Tracking token/costi
│
└── screenshots/               # Screenshot agent-browser
```

**Totale codebase: ~5617 righe** (5 moduli Python + 5 shell script + 2 LaunchAgent plist)

---

## Limiti Noti

### Bot protection
Siti protetti da Cloudflare, Akamai o bot protection avanzata non possono essere analizzati. Il sistema rileva la protezione e restituisce `blocked_by: "Cloudflare"` con `confidence: 20`. Non c'e' workaround con browser headless.

### agent-browser timeout
Il click di agent-browser ha un timeout hardcoded di 10 secondi (Playwright `waitForNavigation`). Per azioni AJAX che non generano navigazione (es. "Aggiungi al carrello"), il timeout scatta sempre. Risolto con JS eval, ma e' un workaround.

### Revenue detection Italia-centrica
La ricerca fatturato e' ottimizzata per aziende italiane (fatturatoitalia.it, ufficiocamerale.it, registroaziende.it, Atoka). Per aziende non italiane, il sistema salta automaticamente le fonti italiane (validazione geografica VAT) e dipende solo da VIES, quindi accuracy ridotta (spesso N/D).

### Payment detection falsi positivi
La detection BNPL cerca keyword (es. "scalapay", "afterpay") nel HTML e nell'accessibility tree. Questo puo' generare falsi positivi se:
- Shopify app installata ma non configurata (es. `window.scalapayConfig = []`)
- Keyword menzionata in Google Analytics exclusion list o cookie consent
- Articoli/FAQ che menzionano competitor BNPL

Il sistema riporta `confidence: 95 (high)` quando trova keyword al checkout, ma non garantisce che il metodo sia effettivamente attivo. Per validazione accurata, verificare manualmente il sito.

### Shopify stores
I siti Shopify rendono i metodi di pagamento solo al checkout con prodotti nel carrello. Il flow completo (prodotto → carrello → checkout) e' necessario. Siti con homepage che mostra solo `/collections/` (senza link diretti a prodotti) richiedono il drill-down tramite Haiku, aggiungendo latenza.

### Costi API esterne
SEMrush e SimilarWeb hanno limiti di crediti mensili. Le API key sono condivise (hardcoded). Monitorare l'utilizzo per evitare esaurimento crediti.

### Single-thread
Il server Flask gira senza worker WSGI (Gunicorn/uWSGI). Webhook concorrenti vengono processati sequenzialmente. Per volumi elevati, aggiungere un worker WSGI o una coda (Redis/Celery).
