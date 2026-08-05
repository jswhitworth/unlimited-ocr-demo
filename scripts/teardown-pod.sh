#!/usr/bin/env bash
# Terminate the tracked Unlimited-OCR demo pod so billing stops.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load-env.sh"

STATE_DIR="$SCRIPT_DIR/../.runpod"
POD_ID_FILE="$STATE_DIR/pod-id"

if [ ! -s "$POD_ID_FILE" ]; then
  echo "No tracked pod found at $POD_ID_FILE. Nothing to tear down." >&2
  exit 1
fi

POD_ID=$(cat "$POD_ID_FILE")

echo "Terminating pod $POD_ID..."
HTTP_CODE=$(curl -sS -o /dev/null -w "%{http_code}" --request DELETE \
  "https://rest.runpod.io/v1/pods/$POD_ID" \
  --header "Authorization: Bearer $RUNPOD_API_KEY")

if [ "$HTTP_CODE" != "200" ] && [ "$HTTP_CODE" != "204" ]; then
  echo "Unexpected response code $HTTP_CODE — check the RunPod console to confirm the pod is gone." >&2
  exit 1
fi

rm -f "$POD_ID_FILE"
echo "Pod terminated. Container disk storage for this pod stops billing immediately."
