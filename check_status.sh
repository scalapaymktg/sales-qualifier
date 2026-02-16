#!/bin/bash
# Quick status check for the deal qualifier system

echo "=== Deal Qualifier Status Check ==="
echo ""

# Check Flask server
echo -n "Flask Server (port 5001): "
if curl -s http://localhost:5001/health | grep -q "healthy"; then
    echo "✅ RUNNING"
else
    echo "❌ NOT RUNNING"
    echo "   Start with: SLACK_BOT_TOKEN=\"...\" python3 webhook_server.py"
fi

# Check ngrok
echo -n "ngrok tunnel: "
NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | grep -o '"public_url":"https://[^"]*"' | head -1 | cut -d'"' -f4)
if [ -n "$NGROK_URL" ]; then
    echo "✅ RUNNING"
    echo "   URL: $NGROK_URL"
else
    echo "❌ NOT RUNNING"
    echo "   Start with: ngrok http 5001"
fi

echo ""
echo "=== Quick Commands ==="
echo "Test Slack:  curl http://localhost:5001/webhook/test-slack"
echo "View logs:   tail -f webhook.log"
echo "Agent logs:  tail -f agent.log"
