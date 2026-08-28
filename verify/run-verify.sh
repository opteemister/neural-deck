#!/usr/bin/env bash
# Single verification entry point. Run by verifier / spec-checker / CI — NOT by the developer.
set -uo pipefail

cd "$(dirname "$0")/.."

PHASES=(test-lint test-backend test-frontend test-security)
declare -a RESULTS
FAILED=0

echo "=== run-verify: $(date '+%Y-%m-%d %H:%M:%S') ==="

for phase in "${PHASES[@]}"; do
  script="verify/${phase}.sh"
  if [[ ! -f "$script" ]]; then
    RESULTS+=("SKIP  ${phase} (no script)")
    continue
  fi
  echo ""
  echo "--- ${phase} ---"
  if bash "$script"; then
    RESULTS+=("PASS  ${phase}")
  else
    RESULTS+=("FAIL  ${phase}")
    FAILED=1
  fi
done

echo ""
echo "=== SUMMARY ==="
for result in "${RESULTS[@]}"; do
  echo "  $result"
done

if [[ $FAILED -ne 0 ]]; then
  echo "VERIFY: FAILED"
  exit 1
fi
echo "VERIFY: PASSED"
