# Security Policy

## Supported Version

- `Factory OS 1.0 FINAL` (latest `master`)

## Reporting a Vulnerability

Please do not open public issues for security vulnerabilities.

Report privately using GitHub security advisories for this repository:

- Repository -> Security -> Advisories -> Report a vulnerability

Include:

- Affected files and commands
- Reproduction steps
- Impact assessment
- Suggested mitigation (if available)

## Response Targets

- Initial acknowledgement: within 72 hours
- Triage update: within 7 days
- Fix/mitigation timeline: based on severity and exploitability

## Luna Swarm Safety Boundary

- Swarm artifacts are append-only and reject common secret, credential,
  mnemonic, and private-key patterns.
- External evidence is retained as sourced data and must not be interpreted as
  a system instruction.
- Outcome observations outside the prediction-maturity window are quarantined
  as temporal-contamination anomalies and are not scored.
- The repository contains no wallet/private-key integration and the default
  x402 payment verifier rejects settlement.
- Paper accounting is deterministic and gated. Real execution is hard-disabled
  and has no broker or order-submission adapter.
