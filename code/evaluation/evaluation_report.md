# Evaluation report

## Metrics history

| date (UTC) | mode | cases | status acc | step F1 full | step F1 no-evidence | state acc | evidence acc | seq exact | mean LCS |
|---|---|---|---|---|---|---|---|---|---|
| 2026-10-24 21:14 | oracle-links | 32 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| 2026-10-24 21:14 | real | 32 | 100.0% | 96.9% | 96.9% | 100.0% | 100.0% | 71.9% | 94.6% |
| 2026-10-24 21:30 | real | 32 | 100.0% | 96.9% | 96.9% | 100.0% | 100.0% | 71.9% | 94.6% |
| 2026-10-24 21:43 | real | 32 | 100.0% | 96.9% | 96.9% | 100.0% | 100.0% | 71.9% | 94.6% |
| 2026-10-24 21:52 | real | 32 | 100.0% | 97.4% | 97.4% | 100.0% | 100.0% | 71.9% | 94.0% |
| 2026-10-24 22:09 | real | 32 | 100.0% | 97.4% | 97.4% | 100.0% | 100.0% | 71.9% | 94.0% |

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

- P8b, propagation link channel: 32 events say "repopulated downstream resource <T>" (31
  SYNC_RECORD, 1 RESTORE_BACKUP). Events with a linked source: 0 on the sample split, 29 on the
  full split; the target was already linked to the same case in all 29, so new = 0 and blocked
  by another owner = 0. Implemented anyway as insurance for hidden cases (one hop, event <=
  audit_at, skipped when the target belongs to another known user; state is still decided by
  evidence only). Sample and full outputs are byte-identical to P7. To make an unobserved
  target linkable, the stale filter now only drops resources that have at least one
  observation (an empty set was vacuously "all before the request"); no existing output changed.
- P8b, tie-break strategy (GHOST_TIEBREAK; measured on the sample split, full-split columns
  against ORDER):

| strategy | link tp/fp/fn | step F1 | status acc | ties (sample) | sample ties correct | ties (full) | steps changed vs ORDER (full) | cases changed |
|---|---|---|---|---|---|---|---|---|
| ORDER | 105/6/6 | 96.9% | 100.0% | 12 | 6 | 199 | 0 | 0 |
| SKIP_ALL | 99/0/12 | 96.8% | 100.0% | 0 | 0 | 0 | 199 | 172 |
| SKIP_MULTI | 104/3/7 | 97.4% | 100.0% | 8 | 5 | 109 | 90 | 84 |
| REQUESTED | 100/8/11 | 95.0% | 100.0% | 9 | 1 | 202 | 353 | 204 |

Chosen default: **SKIP_MULTI**. Rule: highest sample step F1; strategies within 0.5 pp of the best (ORDER, SKIP_MULTI) are ranked by fewer guessed (tie-broken) assignments on the full split.

  Reasoning: SKIP_MULTI has the best sample step F1 (97.4%, +0.5 pp over ORDER) and guesses
  110 fewer assignments on the full split (109 vs 199), so both the F1 rule and the
  "within 0.5 pp, prefer fewer guesses" rule pick it. SKIP_ALL is the most conservative
  (precision 100% on links, fp=0) but loses recall; REQUESTED is worse on every measure, which
  says request order carries no information about which case a worker event belongs to. Cost:
  with SKIP_MULTI 26 more full-split cases end with no account_* (3-way account ties are left
  unassigned), so the full split is expected to lose about as many account steps as ORDER would
  have gotten right by luck (ORDER was right on 6 of 12 sample ties). The sample has only 32 cases, so
  the 0.5 pp margin is one or two steps, not strong evidence; the decision rests mainly on
  the guess count.

## Custom cases

| case | purpose | result |
|---|---|---|
| flow_before_delete | An enabled connector and a live search document: DISABLE_DATA_FLOW must precede DELETE_RECORD. | PASS |
| historical_email_old_hash | Request uses a former email; the leftover search hit carries only sha256(old email). | PASS |
| late_evidence_ignored | File A (before audit_at) shows the account missing; file B (after audit_at) shows it ACTIVE. | PASS |
| non_deletion_keep_account | A request to update an address while keeping the account must be NOT_APPLICABLE with []. | PASS |
| replica_drift | Primary no longer has the account but the failover replica still does: replication drift. | PASS |
