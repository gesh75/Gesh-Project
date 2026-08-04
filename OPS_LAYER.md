# GESH Operations Layer

This document intentionally contains only public-safe design guidance. Deployment
topology, scheduler identifiers, filesystem locations, log destinations, live
position data, control state, and incident procedures belong in a private runbook.

## Reliability Principles

- Run monitoring independently from the customer-facing service so that failures
  can still be detected and reported.
- Make monitoring position-agnostic and configuration-driven; never encode an
  account holding or live trade state in scripts or documentation.
- Use durable state and idempotent notifications to prevent duplicate alerts.
- Use the repository-declared Python version and avoid hardcoded interpreter paths.
- Apply least privilege to schedulers, automation tools, credentials, databases,
  logs, and host integrations.
- Rotate and retain logs according to the deployment's security policy.

## Operational Documentation Policy

Public documentation may describe architecture and configuration interfaces, but
must not record current portfolio state, account progress, control status, local
paths, host-access methods, or deploy-specific process names. Keep those details in
an access-controlled runbook and review that runbook after deployment changes.

## Product Recommendations

- Provide an authenticated health summary with no portfolio or control details.
- Add error trapping, bounded retries, log rotation, and stale-monitor detection.
- Keep one clearly owned orchestration path to reduce scheduler fragmentation.
- Version reusable, environment-neutral operational scripts under `scripts/ops/`.

See `GAPS_AND_IMPROVEMENTS_2026-07-09.md` for the repository-level roadmap.
