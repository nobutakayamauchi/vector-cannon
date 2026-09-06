# Security Policy

Vector Cannon sends selected repository text to the configured AI provider. Review provider privacy, retention, and data-use terms before aiming it at sensitive code or data.

## Built-in protections

- read-only repository tools only
- path traversal blocked
- out-of-root symlinks rejected
- common secret files and build/dependency trees skipped
- common API-key/private-key patterns redacted before upstream model calls
- provider base URLs and secret environment-variable names are fixed in code
- remote FIRE commands are restricted to the repository owner and same-owner target repositories

These controls are defensive safeguards, not a formal DLP or sandbox guarantee.

## Reporting a vulnerability

Please do not publish API keys, credentials, private repository contents, or a working exploit in a public issue.

If GitHub private vulnerability reporting / Security Advisories are enabled for this repository, use that channel. Otherwise open a minimal public issue that states a security report is available without including exploit details or secrets, so a private contact path can be arranged.

## Secrets

Never commit provider API keys. For GitHub Actions, store them only as repository Actions secrets using the documented environment-variable names.
