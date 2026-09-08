# Security Policy

## Reporting a Vulnerability

Please **do not** open a public issue for security vulnerabilities.

Report them privately through
[GitHub Security Advisories](https://github.com/augura-os/augura/security/advisories/new)
on this repository. We aim to acknowledge reports within 3 working days.

## Scope Notes

Augura is a **local-first, single-user** tool: all ports bind to 127.0.0.1 by
default and there is intentionally no built-in authentication. Deploying it
behind a reverse proxy or on a public network without adding your own auth
layer is out of scope (see README's deployment boundary).

In scope: anything that leaks local data outward without explicit consent —
telemetry escaping its documented allowlist (see `PRIVACY.md`), unintended
network exposure, credential handling in the install scripts, supply-chain
issues in the published images.
