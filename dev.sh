#!/usr/bin/env bash
# tinyclaw local dev: gateway + all procurement agents + runtime, seeded demo.
#
#   ./dev.sh                       # start everything (mock LLM, zero keys)
#   ./dev.sh --no-seed             # start without seeding demo requests
#   TINYCLAW_HOST=0.0.0.0 ./dev.sh # expose on the LAN — use from your phone
#                                   # at http://<your-mac-ip>:9100 (dashboard
#                                   # is fully responsive)
#
# Dashboard: http://127.0.0.1:9100   (Approvals tab has parked decisions waiting)
set -euo pipefail
cd "$(dirname "$0")"

SEED=1
[[ "${1:-}" == "--no-seed" ]] && SEED=0

pkill -f "tinyclaw" 2>/dev/null || true
sleep 1
mkdir -p data

uv run python -m tinyclaw.gateway &
uv run python -m tinyclaw.scenarios.procurement &
uv run python -m tinyclaw.runtime &

trap 'pkill -P $$ 2>/dev/null || true; pkill -f "tinyclaw" 2>/dev/null || true' EXIT

for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:9100/api/health >/dev/null 2>&1 && break
  sleep 0.5
done

if [[ $SEED == 1 ]]; then
  echo "seeding demo requests…"
  uv run python -m tinyclaw.scenarios.procurement.seed || true
fi

echo
echo "  🦞 tinyclaw is up — dashboard: http://127.0.0.1:9100"
echo "  pending human approvals are waiting in the Approvals tab"
echo "  ctrl-c to stop"
wait
