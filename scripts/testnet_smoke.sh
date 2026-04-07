#!/usr/bin/env bash
# testnet_smoke.sh — Smoke test for QBit multi-node testnet
#
# Prerequisites: docker compose up -d (3+ nodes running)
# Usage: ./scripts/testnet_smoke.sh [rpc_url] [target_blocks]
#
# Defaults: rpc_url=http://localhost:8545, target_blocks=1000

set -euo pipefail

RPC_URL="${1:-http://localhost:8545}"
TARGET_BLOCKS="${2:-1000}"
POLL_INTERVAL=5  # seconds

echo "=== QBit Testnet Smoke Test ==="
echo "RPC: $RPC_URL"
echo "Target: $TARGET_BLOCKS blocks"
echo ""

# Helper: JSON-RPC call
rpc() {
  local method="$1"
  shift
  curl -sf -H "Content-Type: application/json" \
    "${RPC_URL}/api/v1/${method}" "$@" 2>/dev/null
}

# 1. Health check
echo "[1/5] Health check..."
if rpc "health" > /dev/null; then
  echo "  ✓ Node healthy"
else
  echo "  ✗ Node not reachable at $RPC_URL"
  exit 1
fi

# 2. Check initial chain height
echo "[2/5] Chain height..."
INITIAL_HEIGHT=$(rpc "chain/info" | python3 -c "import json,sys; print(json.load(sys.stdin).get('height', 0))" 2>/dev/null || echo "0")
echo "  Current height: $INITIAL_HEIGHT"

# 3. Check peer connectivity
echo "[3/5] Peer connectivity..."
PEER_COUNT=$(rpc "peers" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d) if isinstance(d, list) else d.get('count', 0))" 2>/dev/null || echo "0")
echo "  Connected peers: $PEER_COUNT"
if [ "$PEER_COUNT" -lt 1 ]; then
  echo "  ⚠ Warning: no peers connected"
fi

# 4. Wait for blocks
echo "[4/5] Waiting for $TARGET_BLOCKS blocks (from height $INITIAL_HEIGHT)..."
LAST_HEIGHT="$INITIAL_HEIGHT"
STALL_COUNT=0
MAX_STALL=30  # exit if no new block for 30 polls (150s)

while true; do
  sleep "$POLL_INTERVAL"
  CURRENT=$(rpc "chain/info" | python3 -c "import json,sys; print(json.load(sys.stdin).get('height', 0))" 2>/dev/null || echo "$LAST_HEIGHT")
  PRODUCED=$((CURRENT - INITIAL_HEIGHT))

  if [ "$CURRENT" -eq "$LAST_HEIGHT" ]; then
    STALL_COUNT=$((STALL_COUNT + 1))
    if [ "$STALL_COUNT" -ge "$MAX_STALL" ]; then
      echo "  ✗ Chain stalled at height $CURRENT for ${MAX_STALL} polls"
      exit 1
    fi
  else
    STALL_COUNT=0
    echo "  Height: $CURRENT (+$PRODUCED blocks)"
  fi

  LAST_HEIGHT="$CURRENT"

  if [ "$PRODUCED" -ge "$TARGET_BLOCKS" ]; then
    echo "  ✓ Reached $PRODUCED blocks"
    break
  fi
done

# 5. Final summary
echo "[5/5] Final validation..."
FINAL_HEIGHT=$(rpc "chain/info" | python3 -c "import json,sys; print(json.load(sys.stdin).get('height', 0))" 2>/dev/null || echo "?")
echo "  Final chain height: $FINAL_HEIGHT"
echo "  Blocks produced: $((FINAL_HEIGHT - INITIAL_HEIGHT))"
echo ""
echo "=== Smoke Test PASSED ==="
