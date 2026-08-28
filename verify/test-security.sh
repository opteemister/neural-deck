#!/usr/bin/env bash
# Mechanized security checks. Complements review-security, does not replace it.
set -uo pipefail
cd "$(dirname "$0")/.."
FAILED=0

SRC_RG=(rg --no-heading --line-number --glob '*.py' --glob '*.ts' --glob '*.tsx' --glob '!backend/.venv/**' --glob '!frontend/node_modules/**' --glob '!**/.worktrees/**' --glob '!**/dist/**')

echo "[security] insecure connections / TLS verification disabled"
if "${SRC_RG[@]}" 'verify\s*=\s*False|ssl\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False' .; then
  echo "  ^^ FAIL: TLS verification disabled"
  FAILED=1
fi

echo "[security] dangerous calls"
if "${SRC_RG[@]}" 'shell\s*=\s*True|pickle\.loads|os\.system\(' .; then
  echo "  ^^ FAIL: dangerous call (shell=True / pickle.loads / os.system)"
  FAILED=1
fi
if "${SRC_RG[@]}" 'yaml\.load\(' . | rg -vi 'SafeLoader'; then
  echo "  ^^ FAIL: yaml.load without SafeLoader"
  FAILED=1
fi

echo "[security] looks like a hardcoded secret"
SECRET_PATTERN="(api_key|secret|password|token)\\s*[:=]\\s*[\"'][A-Za-z0-9+/=_-]{20,}[\"']"
if "${SRC_RG[@]}" "$SECRET_PATTERN" . | rg -vi 'fake|test|example|placeholder|dummy'; then
  echo "  ^^ FAIL: looks like a real secret in code"
  FAILED=1
fi

echo "[security] required env with an empty default"
EMPTY_DEFAULT_PATTERN="environ\\.get\\([^)]+,\\s*[\"'][\"']\\)"
if "${SRC_RG[@]}" "$EMPTY_DEFAULT_PATTERN" .; then
  echo "  ^^ FAIL: os.environ.get(KEY, \"\") — required value with a silent empty default"
  FAILED=1
fi

echo "[security] pip-audit is handled by review-security"
exit "$FAILED"
