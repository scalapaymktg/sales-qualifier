#!/bin/bash
# Setup LaunchAgent for automatic deal qualification every 5 minutes

PLIST_NAME="com.scalapay.deal-qualifier.plist"
PLIST_SOURCE="/Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier/$PLIST_NAME"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "Setting up Deal Qualifier Agent..."

# Copy plist to LaunchAgents
cp "$PLIST_SOURCE" "$PLIST_DEST"

# Unload if already loaded
launchctl unload "$PLIST_DEST" 2>/dev/null

# Load the agent
launchctl load "$PLIST_DEST"

echo "Agent installed and started!"
echo ""
echo "Commands:"
echo "  Start:   launchctl load ~/Library/LaunchAgents/$PLIST_NAME"
echo "  Stop:    launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
echo "  Status:  launchctl list | grep deal-qualifier"
echo "  Logs:    tail -f /Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier/agent.log"
echo ""
echo "The agent will run every 5 minutes automatically."
