# Security Policy

## Supported Versions

We support security updates for the latest minor release on the `main` branch.
Older releases are not actively maintained — please upgrade to the latest version.

| Version | Supported |
|---------|-----------|
| Latest (main) | ✅ |
| Older releases | ❌ |

## Reporting a Vulnerability

If you discover a security vulnerability in AI Reader V2, please report it
**privately** rather than opening a public issue.

### Preferred channel

**GitHub Security Advisory** (preferred — encrypted, tracked):

1. Go to [Security Advisories](https://github.com/mouseart2025/AI-Reader-V2/security/advisories)
2. Click "Report a vulnerability"
3. Fill out the form with details

### Alternative channel

If the above isn't an option, contact the maintainer via the email address in
the GitHub profile of [@mouseart2025](https://github.com/mouseart2025).

### What to include

- Affected component (backend / frontend / desktop / website)
- Reproduction steps or proof-of-concept
- Suspected impact (data leak / RCE / DoS / supply chain / etc.)
- Your assessment of severity (Critical / High / Medium / Low)

### Response timeline

- **Acknowledgement**: within 7 days
- **Initial assessment**: within 14 days
- **Fix or mitigation plan**: depends on severity

We'll credit reporters in the release notes (unless anonymity is requested).

## Security Hygiene in This Repo

For maintainers and contributors:

- **Pre-commit secret scan**: `scripts/scan-secrets.sh` runs as a Git pre-commit
  hook (install via `ln -sf ../../scripts/scan-secrets.sh .git/hooks/pre-commit`).
- **CI secret scan**: every push and PR runs the full-history secret scan
  (see `.github/workflows/test.yml`).
- **GitHub Secret Scanning**: enabled with push protection — commits containing
  detected secrets are blocked at the GitHub side.
- **Dependabot**: enabled for npm, pip, and cargo — security PRs land
  automatically.
- **`.gitignore`**: covers `.env*`, `*.secret*`, `credentials.*`, `*.pem`, `*.key`,
  and other sensitive file patterns by default.

## Out of Scope

- Vulnerabilities in third-party LLM providers (OpenAI / Anthropic / DeepSeek / etc.)
  — report those upstream.
- Issues that require an attacker already having local file system access on the
  user's machine (the desktop app stores data in `~/.ai-reader-v2/` — see CLAUDE.md
  for details).
