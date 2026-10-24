# FINDINGS — Verified facts about the dataset

Every fact below was checked by running code against `dataset/`. Facts marked
**[VERIFIED]** hold on all 32 samples. **[HYPOTHESIS]** items must be tested
by the evaluation harness before being relied on. Do not add facts here
without evidence.

## 1. Request classification

- [VERIFIED] The 518 requests are built from 27 opening sentences. 17 are
  deletion requests in 6 languages: English, Spanish, Portuguese, French,
  Korean and Japanese.
- [VERIFIED] Exactly 10 requests are non-deletion. They cover: changing the
  phone, stopping promotional emails, a data download, a login reset, a
  surname fix, "remove me from marketing lists only", "what info do you
  hold", invoices, changing the email, and "send me a copy, don't delete".
  Each one also contains an explicit keep-the-account sentence (for example
  "Do not close the account."). The expected output for these is
  `NOT_APPLICABLE` with `[]`.
- [VERIFIED] The 32 samples contain zero `NOT_APPLICABLE` cases. This class
  cannot be learned from the samples.
- [VERIFIED] `system_activity.csv` has 10 `CLASSIFY_REQUEST` events reading
  "classified ... as non-deletion". These are claims only and must not be
  used as the decision.

## 2. Identity

- [VERIFIED] 418 requests match a current email in `user_identities.csv`,
  and 100 match only an old email in `user_audit_log.csv`. Every request
  matches some user.
- [VERIFIED] The audit log changes these fields: `email`, `phone`,
  `email_hash` and name. Old hashes appear in evidence. In case_12, a log
  line has `correlation_key=<old email_hash>`.
- [VERIFIED] Evidence emails can be upper-case
  (`CHOI.JIHOON94@GMAIL.COM`), so lower-case them before comparing.
- [VERIFIED] Phone formats vary. Compare digits only, and require at least
  8 digits.
- [VERIFIED] Resource IDs must be matched exactly on word boundaries:
  `support_864` and `support_8642` are different records.
- [VERIFIED] email_hash = sha256(lowercase email) for all current and historical emails

## 3. Evidence formats (all under `dataset/evidence/`)

| Family | Files | Notes |
|---|---|---|
| SQL inventory (`customer_data_inventory_*`) | 12 | Typed tables such as `customer_profiles` and `connector_subscriptions` (with `enabled`). It also has `backup_restore_entries` (with `restore_allowed`) and `security_audit_records` (with `retention_until`). `snapshot_scope` lists which systems the snapshot covers. A covered system with no row means the record is ABSENT. |
| SQL replicas (`*_read_replica_*`, `*_regional_replicas_*`, `*_reporting_replica_*`, `*_failover_replica_*`) | 11 | There are two patterns. In the first, separate `customer_profiles_primary` and `customer_profiles_failover_replica` tables. In the second, `customer_data_primary` and replica tables with `record_json`. Replication drift means present in a replica but missing from the primary. |
| Service export JSON (`service_resource_export_*`) | 11 | `service_exports.<system>.<collection>[]`. Account lookups give `status_code` 404 or 200 and a `profile`. Each record has `observed_at`. |
| Snapshot bundle JSON (`customer_data_snapshot_bundle_*`) | 12 | `snapshots[]` with `data_store`, `collection` and `records[]`. Includes `identity_service.deleted_customer_references` (tombstones). |
| Readback logs (`resource_lookup_trace_*`) | 11 | `key=value` lines. The `lookup_key=<email or user_id>` line and the result line share a `trace_id`, so lines must be grouped by `trace_id`. |
| Observability logs (`service_events_*`, `structured_*`, `syslog_*`, `operations_*`, `cloud_runtime_*`, `application_trace_*`) | 12 | Mixed `key=value` and JSON-per-line. They contain noise lines. Identity (`lookup_key`, `subject_key`, `match_key`, `correlation_key`) sits on a separate line with the same `trace_id`. |
| Bespoke records | several | `customer_account_handover_*.txt` (pipe table: system, resource, CATEGORY, STATE, identifier), `connector_configurations_*.json`, `customer_export_jobs_*.json`, `customer_analytics_report_*.json`, `security_audit_ledger_*.json`, `support_replica.log`, `notification_delivery_*.log`, `search_index_diagnostic_*.log`. |
| PDFs | 4 | Text is extractable (pdftotext or pypdf), so no OCR or LLM is needed. |
| PNG screenshots | 11 | Clean admin UIs. These need a vision model, run once and cached. They show the resource ID, `user_XX` and email. |

- [VERIFIED] The handover TXT already states a category and state per row,
  for example `ANALYTICS_PROFILE | IDENTIFIABLE` or `BACKUP_ARCHIVE | ACTIVE`.
- [VERIFIED] Some evidence is captured after `audit_at`. For example,
  evidence_02 (09-17) shows account_5684 ACTIVE, but case_26 has
  `audit_at` 09-15 and the answer is COMPLETE. Later evidence must never
  decide state.
- [VERIFIED] All 2,211 non-empty log lines match exactly one of six formats:
  key=value, bracket, pipe, JSON-per-line, syslog and cloud (escaped
  `message="k=\"v\""`). No other line format exists. The ledger-style logs
  (`notification_delivery_*`, `search_index_diagnostic_*`) use the key=value
  format with `event=record_lookup`.
- [VERIFIED] The same resource can appear in several traces of one log with
  different results. For example, account_1956 in evidence_21 is a 404 on
  07-03 and a 200 on 07-05. Each trace therefore yields its own observation
  with its own `observed_at`.
- [VERIFIED] `lookup_key` is sometimes a resource id rather than an
  identifier (`analytics_profile_scan_started lookup_key="analytics_257"`),
  and is sometimes the literal `null`.
- [VERIFIED] `search_documents_primary.source_resource_id` (evidence_57)
  ties a search document to its account. account_006 appears in non-PNG
  evidence only through this reference, so it is emitted as a mention
  (`present=None`).
- [VERIFIED] Snapshot bundles hold per-snapshot `captured_at` values up to 8
  days earlier than the manifest time (91 of 116 snapshots are earlier, none
  are later). Bundles also embed ledger collections
  (`operational_record_history` under `support_storage_service`,
  `privacy_treatment_service` and `backup_restore_service`) keyed by
  `lookup_value`.
- [VERIFIED] Ledger results are FOUND, NOT_FOUND, IDENTIFIABLE, ANONYMIZED,
  ACTIVE, BLOCKED, DISABLED and REDACTED. Only NOT_FOUND means absent.
- [VERIFIED] `snapshot_scope.captured_at` equals the manifest `captured_at`
  in all 12 inventory files.
- [VERIFIED] evidence_53 (cloud log) is a bulk dump of 300 `account_*`
  lookups with 5-digit ids, logged under `function=profile_service` rather
  than `account_service`.
- [VERIFIED] Security audit records carry
  `integrity_seal="sha256:<64 hex>"`. The hex part looks like an email hash
  but matches no identity.
- [VERIFIED] Two PDFs contain user ids but no resource ids: the legal
  retention register (order ref, user, retain-until per row) and the
  account history report (user_18, one order). They are stored as file
  facts for later legal-hold linking.
- [VERIFIED] Screenshots (11 PNGs) are one admin-console layout: a table, a
  detail/side panel for the selected row, and an activity feed that shows other
  people's records. The vision model (gemini-3.8-flash, ~95s per image) transcribes
  them; the same resource can appear as both a table row and a panel, and the panel
  often carries fields the row lacks (backup_9287's account email). Link keys must
  come from the record's own row/panel only. The top search box holds a query email
  (stored in FileFacts.emails), not a record.
- [VERIFIED] Vision output needs guards: it can emit non-resource ids as
  `resource_id` (order refs `ORD-...`, a `user_17` page-header key, a bucket
  name). The resource-id regex drops them (4 of 29 records/mentions); the content
  survives in `references`. No suspect emails (misreads) in the 11 images.
- [VERIFIED] Image-only resources: `support_9538` (a mention in evidence_04) and
  `backup_9281` (feed-only mention in evidence_32). A panel attribute can also name
  a resource (`Support: CS-8538 · support_9538`); the link is not in `fields`.
- [VERIFIED] evidence_42 (deleted_account_reference) and evidence_46
  (object_storage_browser) carry no resource ids; they yield FileFacts only
  (user_17 + ORD-20263245; eight file names).

## 4. Activity stream (`system_activity.csv`, 4,011 rows)

- [VERIFIED] The `deletion_worker` events for a case fall within about
  -1.5h to +2.5h of the case's `reported_at`. Account `DELETE_RECORD` comes
  first, then "Policy action completed" events, then "received the final
  result" events.
- [VERIFIED] `reported_result` (SUCCESS, FAILED, PARTIAL) does not predict
  the true state. For example, case_05 shows account_005 deleted with
  SUCCESS, but the failover replica still has it.
- [VERIFIED] The worker's action type (DELETE, ANONYMIZE, REDACT) matches
  the treatment the answer expects. For example, analytics_9914 had
  `DELETE_RECORD`, and the answer is DELETE, not ANONYMIZE. Use it as a
  category signal, not as a state signal.
- [VERIFIED] `CHECK_DATA_FLOW` reading "remains enabled" and
  `CHECK_RESTORE_ACCESS` reading "can still be restored" line up with
  active-risk findings.
- [VERIFIED] Account resources are linked to identity in different ways:
  - by `lookup_key` (logs);
  - by `customer_reference` or `user_id` (JSON and SQL);
  - by `LINK_RESOURCE_IDENTITY` activities (12 events, from `data_catalog`);
  - not at all. Service-export 404s have no identifier.
- [VERIFIED] After identifier and `trace_id` linking, 20 of 32 sample cases
  still have no linked account. In the 518, 340 cases have no
  identity-linked account before `trace_id` grouping. Linking accounts is
  the hardest sub-problem.
- [VERIFIED] Several cases share the same `reported_at`, so a time window
  alone is ambiguous. Across the 518, 121 timestamps are shared by 3 cases.
- [HYPOTHESIS] Ambiguity can be reduced by intersecting two sets: worker
  resources in the case's window, and resources observed without
  identifiers in the cited evidence. Then assign globally by elimination
  across all 550 cases. In case_21 this gives only `account_6440`.
- [VERIFIED] Some answer resources appear nowhere in the inputs, for
  example audit_record_6645 (case_18) and audit_record_5569 (case_20). Do
  not chase 100%. Some answer items cannot be recovered.

## 5. Output patterns (from `sample_output.csv`)

- [VERIFIED] Status distribution: 22 INCOMPLETE and 10 COMPLETE.
  `reported_status` is uncorrelated with the true status.
- [VERIFIED] Every cited `evidence_id` has `captured_at <= audit_at` (equal
  is allowed). It is usually the latest relevant capture, within about 1h
  before `audit_at`.
- [VERIFIED] Most cases cite a single evidence file for all their steps.
- [VERIFIED] COMPLETE rows contain only VERIFICATION steps, and every step
  has an evidence ID.
- [VERIFIED] INCOMPLETE rows contain FINDING steps (with evidence), then
  REMEDIATION (`""`), then VERIFICATION (`""`).
- [VERIFIED] INCOMPLETE verification lists all relevant records, not only
  remediated ones. It always includes `VERIFY_RECORD_ABSENT` for the
  account and `VERIFY_RECORD_PRESENT` for retained security audit records.
- [VERIFIED] Backups produce `VERIFY_RESTORE_BLOCKED`, never
  `VERIFY_RECORD_PRESENT`.
- [VERIFIED] Ordering: phase first, then operation in the problem
  statement's table order, then `system_id` ascending, then `resource_id`
  ascending. case_11 shows this order for:
  - Findings: REMAINING, then EXPOSED_PII, then ACTIVE_DATA_FLOW.
  - Remediation: DISABLE, then DELETE, then REDACT, then ANONYMIZE.
  - Verification: ABSENT, then PRESENT, then REDACTED, then ANONYMIZED,
    then FLOW_DISABLED.
- [VERIFIED] Finding operation by situation:

  | Situation | Finding |
  |---|---|
  | DELETE-category record still present | `REPORT_REMAINING_RECORD` |
  | REDACT or ANONYMIZE category with identifiers present | `REPORT_EXPOSED_PII` |
  | Connector enabled | `REPORT_ACTIVE_DATA_FLOW` |
  | Backup with `restore_allowed` | `REPORT_RESTORE_RISK` |

- [VERIFIED] Support records are handled two ways:
  - DELETE when it is a subject-only attachment or a copy (support_001 is a
    PDF attachment, support_002 comes from the replica log).
  - REDACT when the record is a message containing shared order context
    (support_864, support_5569, support_9553).
- [VERIFIED] Tombstones (`identity_service`, `tombstone_*`) are handled two
  ways:
  - If name, email, phone or hash is still set: `REPORT_EXPOSED_PII`, then
    REDACT, then `VERIFY_RECORD_REDACTED`.
  - If all are null: `VERIFY_RECORD_REDACTED` with evidence.
- [VERIFIED] Rows in `deletion_requests.csv` are in numeric `case_id` order
  (case_33 to case_550). The output keeps numeric order, so the IDs have
  gaps.

## 6. Known open questions (resolve with the eval harness)

1. The exact evidence-citation rule for absent records that no file
   mentions. case_01, 02 and 04 cite evidence_54, not the later empty
   inventory snapshot evidence_90.
2. How to tell DELETE from REDACT for support records in every evidence
   format.
3. How well the window-intersection and elimination approach links
   accounts.
4. `resource_id` sort: string versus numeric. No sample has two resources
   with the same operation and system.
