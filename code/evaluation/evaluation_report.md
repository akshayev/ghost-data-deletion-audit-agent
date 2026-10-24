# Evaluation report

## Metrics history

| date (UTC) | mode | cases | status acc | step F1 full | step F1 no-evidence | state acc | evidence acc | seq exact | mean LCS |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-24 21:14 | oracle-links | 32 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| 2026-10-24 21:14 | real | 32 | 100.0% | 96.9% | 96.9% | 100.0% | 100.0% | 71.9% | 94.6% |
| 2026-10-24 21:30 | real | 32 | 100.0% | 96.9% | 96.9% | 100.0% | 100.0% | 71.9% | 94.6% |
| 2026-10-24 21:43 | real | 32 | 100.0% | 96.9% | 96.9% | 100.0% | 100.0% | 71.9% | 94.6% |

## Failures found & fixes

- P6, oracle run, case_06: `account_006` was reported as remaining in evidence_57. It
  appears there only as a search document's `source_resource_id`; the reference mention
  carried the referencing record's fields, so it was upgraded to "present". Fix: a
  reference mention never decides state. Oracle-links went from 99.5% to 100% step F1.
- P6, real run: every remaining wrong step (9 cases) is a linker error, not state or
  policy: link misses account_004 (no worker event), audit_record_6645 and
  audit_record_5569 (absent from all inputs), account_410 (window overlap), and the
  analytics_1895/analytics_4566 same-minute tie; link extras analytics_5718,
  analytics_3264, analytics_2265 and account_7015.

## Custom cases

| case | purpose | result |
|---|---|---|
| flow_before_delete | An enabled connector and a live search document: DISABLE_DATA_FLOW must precede DELETE_RECORD. | PASS |
| historical_email_old_hash | Request uses a former email; the leftover search hit carries only sha256(old email). | PASS |
| late_evidence_ignored | File A (before audit_at) shows the account missing; file B (after audit_at) shows it ACTIVE. | PASS |
| non_deletion_keep_account | A request to update an address while keeping the account must be NOT_APPLICABLE with []. | PASS |
| replica_drift | Primary no longer has the account but the failover replica still does: replication drift. | PASS |
