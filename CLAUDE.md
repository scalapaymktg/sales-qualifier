# Project Rules - Sales Qualifier

## Server Restart (OBBLIGATORIO)

Dopo ogni modifica a `webhook_server.py`, il server Flask DEVE essere riavviato per caricare il codice aggiornato.

Procedura:
1. Trova il processo: `lsof -ti:5001`
2. Killalo: `kill -9 <PID>`
3. Riavvia: `cd /Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier && /usr/bin/python3 webhook_server.py &`
4. Verifica: `curl -s http://localhost:5001/health`

Il server gira sulla porta 5001. Se il processo vecchio non viene killato, il nuovo non puo' partire (Address already in use).

## Struttura

- Entry point principale: `webhook_server.py` (server Flask, porta 5001)
- Il sistema usa solo Claude Haiku (no Opus)
- LLM locale: Ollama gemma3:4b
- LaunchAgent: `com.scalapay.webhook-server.plist` (auto-start + KeepAlive)

## HubSpot Properties

- `sql_qualifier`: valori `automated` o `sales` (lowercase)
- `sql_qualifier_status`: lifecycle `to_start` -> `in_progress` -> `done` / `failed`
- Deal test (id che inizia con "test") non aggiornano HubSpot

## README (OBBLIGATORIO)

**REGOLA IMPERATIVA**: Ogni aggiornamento al codice DEVE prevedere aggiornamento del `README.md`. Nessuna eccezione.

Dopo ogni modifica a `webhook_server.py` o ad altri moduli, il `README.md` DEVE essere aggiornato per riflettere le modifiche. Aggiornare:
- **Changelog**: aggiungere entry con data, problema, causa, soluzione, modifiche codice, impatto
- Tabella funzioni principali (nomi, righe, descrizioni)
- Diagramma architettura (se cambia il flusso)
- Sezione flusso end-to-end
- Tabella integrazioni esterne (se aggiunte nuove API/servizi)
- Sezione configurazione (se aggiunte nuove variabili .env)

## Git Commit & Push (OBBLIGATORIO)

**REGOLA IMPERATIVA**: Dopo ogni aggiornamento al codice, DEVI fare `git commit` e `git push`. Nessuna eccezione.

Procedura:
1. `git add` dei file modificati (mai `git add -A`, solo file specifici)
2. `git commit` con messaggio descrittivo + `Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>`
3. `git push origin main`
4. Se il push fallisce (non-fast-forward), fare `git pull --rebase origin main` e riprovare

**ATTENZIONE SECRETS**: Non committare mai segreti hardcoded (API keys, tokens). Usare `os.environ.get()` nel codice e `.env` localmente. I file `.plist` locali possono avere i valori reali ma su GitHub devono avere placeholder.

## Convenzioni

- Lingua log/commenti: italiano
- Slack messages: italiano
- README: italiano
