#!/bin/bash
cd /Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier
CLAUDE_BIN="/Users/stefano.conforti@scalapay.com/.cursor/extensions/anthropic.claude-code-2.1.11-darwin-arm64/resources/native-binary/claude"
echo "Starting test..."
"$CLAUDE_BIN" --dangerously-skip-permissions --print -p "Rispondi: TEST OK" 2>&1
echo "Done!"
