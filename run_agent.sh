#!/bin/bash
# Run Claude Code as autonomous agent for deal monitoring

cd /Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier

# Ensure PATH includes common locations
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export HUBSPOT_TOKEN="${HUBSPOT_TOKEN:-pat-eu1-52da997e-cecb-4777-ae6c-69e6e87d208e}"

# Claude binary - try PATH first, then find latest Cursor extension version
CLAUDE_BIN=$(which claude 2>/dev/null)
if [ -z "$CLAUDE_BIN" ]; then
    # Fallback: find latest version in Cursor extensions (sorted by version, pick last)
    CLAUDE_BIN=$(ls -d /Users/stefano.conforti@scalapay.com/.cursor/extensions/anthropic.claude-code-*-darwin-arm64/resources/native-binary/claude 2>/dev/null | sort -V | tail -1)
fi
if [ -z "$CLAUDE_BIN" ]; then
    echo "ERROR: Claude binary not found in PATH or Cursor extensions"
    exit 1
fi

echo "=== Agent started at $(date) ==="
echo "CLAUDE_BIN: $CLAUDE_BIN"
echo "CLAUDE_BIN exists: $(test -x "$CLAUDE_BIN" && echo YES || echo NO)"
echo "Working dir: $(pwd)"
echo "Launching Claude..."

PROMPT="Sei un agente autonomo per qualificare i deal. Esegui questi step SENZA chiedere conferme:

1. Esegui questo comando bash per ottenere i nuovi deal di oggi:
   HUBSPOT_TOKEN=\"$HUBSPOT_TOKEN\" python3 deal_monitor.py --dry-run 2>&1 | grep -E 'Processing deal:|Website:|Domain:|Company:' | head -20

2. Per ogni deal/sito trovato:
   a) Fai WebFetch del sito per analizzare: tecnologie, cosa vende, catalogo, social, pagamenti
   b) Fai WebSearch per: \"[nome azienda] fatturato revenue notizie 2024 2025\"

3. Stampa un report completo per ogni deal con tutte le informazioni raccolte.

IMPORTANTE: Esegui TUTTO autonomamente senza chiedere conferme."

# Run Claude with dangerously-skip-permissions for full automation
echo "Starting Claude now..."
"$CLAUDE_BIN" --dangerously-skip-permissions --print -p "$PROMPT" 2>&1
EXIT_CODE=$?
echo "=== Claude finished with exit code: $EXIT_CODE at $(date) ==="
