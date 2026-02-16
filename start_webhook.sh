#!/bin/bash
# Start webhook server for HubSpot integration

cd /Users/stefano.conforti@scalapay.com/Cursor/sales-qualifier

export HUBSPOT_TOKEN="${HUBSPOT_TOKEN:-pat-eu1-52da997e-cecb-4777-ae6c-69e6e87d208e}"

echo "Starting webhook server on port 5000..."
echo ""
echo "To expose publicly, in another terminal run:"
echo "  ngrok http 5000"
echo ""
echo "Then configure HubSpot webhook URL:"
echo "  https://YOUR-NGROK-URL/webhook/hubspot"
echo ""

python3 webhook_server.py
