#!/usr/bin/env bash
# scan-secrets.sh — fail-fast secret scanner for staged files.
#
# Usage:
#   ./scripts/scan-secrets.sh              # scan staged files
#   ./scripts/scan-secrets.sh --all        # scan ALL tracked files
#   ./scripts/scan-secrets.sh --history    # scan full git history (slow)
#
# Install as pre-commit hook:
#   ln -sf ../../scripts/scan-secrets.sh .git/hooks/pre-commit
#
# Exits non-zero if any pattern matches.

set -e

MODE="${1:-staged}"

# High-confidence secret patterns.
# Each line: description|regex
PATTERNS=(
  'OpenAI/Claude key|sk-(ant-|proj-|cp-)?[A-Za-z0-9_-]{30,}'
  'GitHub PAT|(ghp_|gho_|ghu_|ghs_|github_pat_)[A-Za-z0-9_]{30,}'
  'AWS Access Key|AKIA[0-9A-Z]{16}'
  'Google API key|AIza[0-9A-Za-z_-]{35}'
  'Stripe live key|(sk|pk)_live_[A-Za-z0-9]{24,}'
  'Slack token|xox[bapros]-[A-Za-z0-9-]{10,}'
  'Generic bearer|Bearer [A-Za-z0-9_.-]{40,}'
  'Private key|-----BEGIN (RSA |OPENSSH |DSA |EC |ENCRYPTED )?PRIVATE KEY-----'
  'JWT token|\beyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{10,}'
  'Literal api_key=long|(api_key|apikey|access_token|secret_key)[[:space:]]*[:=][[:space:]]*["'\''][a-zA-Z0-9_-]{30,}["'\'']'
  'URL with creds|https?://[^/\\ ]{1,32}:[^@/\\ ]{6,}@[a-zA-Z0-9.-]+'
  'Chinese session cookie|passport_csrf_token=[A-Za-z0-9]{20,}|sessionid=[A-Za-z0-9]{20,}'
)

scan_file() {
  local file="$1"
  local hit=0
  for entry in "${PATTERNS[@]}"; do
    local desc="${entry%%|*}"
    local regex="${entry#*|}"
    local matches
    matches=$(grep -nE "$regex" "$file" 2>/dev/null | head -3) || true
    if [ -n "$matches" ]; then
      echo "  ❌ [$desc] $file"
      echo "$matches" | sed 's/^/      /'
      hit=1
    fi
  done
  return $hit
}

FILES_LIST=""
case "$MODE" in
  staged)
    # Only staged files (diff --cached)
    FILES_LIST=$(git diff --cached --name-only --diff-filter=ACM)
    ;;
  --all)
    FILES_LIST=$(git ls-files)
    ;;
  --history)
    echo "Scanning full history (slow)..."
    HITS=$(git log --all -p 2>/dev/null | grep -cE 'sk-[A-Za-z0-9_-]{30,}|ghp_[A-Za-z0-9]{36}|AKIA[0-9A-Z]{16}|-----BEGIN.*PRIVATE KEY' || true)
    if [ "$HITS" -gt 0 ]; then
      echo "❌ $HITS potential secrets in git history — run: git log --all -p | grep -E 'sk-|ghp_|AKIA|BEGIN.*PRIVATE KEY'"
      exit 1
    fi
    echo "✓ git history clean"
    exit 0
    ;;
  *)
    echo "Usage: $0 [staged|--all|--history]"
    exit 2
    ;;
esac

if [ -z "$FILES_LIST" ]; then
  echo "No files to scan."
  exit 0
fi

FOUND=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  [ -f "$f" ] || continue
  # Skip binary files
  file --mime "$f" 2>/dev/null | grep -q 'charset=binary' && continue
  if ! scan_file "$f"; then
    FOUND=1
  fi
done <<< "$FILES_LIST"

if [ "$FOUND" -eq 1 ]; then
  echo ""
  echo "❌ Secrets detected in $MODE files."
  echo "   Remove them and/or add patterns to .gitignore, then retry."
  exit 1
fi

echo "✓ No secrets detected in $MODE files"
exit 0
