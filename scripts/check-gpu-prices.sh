#!/usr/bin/env bash
# Print current RunPod Community Cloud on-demand prices for GPUs with
# <=32GB VRAM, cheapest first — use this to sanity-check the fallback list
# in deploy-pod.sh before spending credits.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load-env.sh"

CLOUD="${1:-COMMUNITY}"

curl -sS "https://api.runpod.io/v2/catalog/gpus?include=AVAILABILITY&product=POD&cloud=$CLOUD" \
  --header "Authorization: Bearer $RUNPOD_API_KEY" \
| jq -r --arg cloud "$CLOUD" '
  .gpus
  | map(select(.memory != null and .memory <= 32))
  | sort_by(if $cloud == "COMMUNITY" then .price.community else .price.secure end)
  | .[]
  | [
      (.memory | tostring) + "GB",
      "$" + ((if $cloud == "COMMUNITY" then .price.community else .price.secure end) | tostring) + "/hr",
      .availability,
      .name
    ]
  | @tsv
' | column -t -s $'\t'
