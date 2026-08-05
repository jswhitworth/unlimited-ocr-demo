#!/usr/bin/env bash
# Check status of the tracked Unlimited-OCR demo pod and print its proxy URL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load-env.sh"

STATE_DIR="$SCRIPT_DIR/../.runpod"
POD_ID_FILE="$STATE_DIR/pod-id"

if [ ! -s "$POD_ID_FILE" ]; then
  echo "No tracked pod found at $POD_ID_FILE. Run scripts/deploy-pod.sh first." >&2
  exit 1
fi

POD_ID=$(cat "$POD_ID_FILE")

RESPONSE=$(curl -sS "https://rest.runpod.io/v1/pods/$POD_ID" \
  --header "Authorization: Bearer $RUNPOD_API_KEY")

echo "$RESPONSE" | jq .

STATUS=$(echo "$RESPONSE" | jq -r '.desiredStatus // "UNKNOWN"')
COST=$(echo "$RESPONSE" | jq -r '.costPerHr // "unknown"')

echo ""
echo "Pod ID:      $POD_ID"
echo "Status:      $STATUS"
echo "Cost/hr:     \$$COST"
echo "Proxy URL:   https://$POD_ID-8000.proxy.runpod.net"
echo ""
echo "The proxy URL 404s/502s until the vLLM server has finished loading the model — check the console Logs tab if it's slow."
