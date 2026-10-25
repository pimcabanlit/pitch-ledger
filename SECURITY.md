# Security policy

Pitch Ledger is a hackathon project. Its live demo runs on Azure until judging ends
on 2026-11-20 and uses synthetic data only: every club, player and match is fictional,
and no personal data is stored beyond a signed-in producer's account ID.

## Reporting a vulnerability

Please report security issues privately through GitHub:
**Security → Report a vulnerability** on this repository
([private vulnerability reporting](https://github.com/pimcabanlit/pitch-ledger/security/advisories/new)).

Do not open a public issue or pull request for a vulnerability. Include what you found,
how to reproduce it, and what it could affect. You should hear back within a few days.

## Supported versions

Only the current `main` branch, which is what the live demo runs, is supported.

## In scope

- The code in this repository.
- The deployed demo (Studio, API and jobs on Azure Container Apps).

Please don't run load or denial-of-service tests against the live demo, and don't
approve insights in bulk: each approval calls a paid model.
