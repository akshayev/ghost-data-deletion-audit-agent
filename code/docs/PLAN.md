# PLAN — Ghost Data Still Exists: deletion-audit agent

Read `code/docs/FINDINGS.md` before changing logic. The spec lives in
`problem_statement.md` and `AGENTS.md` §6. When this plan and the spec
disagree, the spec wins.

## 0. Non-negotiable rules for any coding agent

1. **Deterministic core, narrow LLM.** All matching, state reconstruction,
   policy, planning, ordering and serialization are plain Python. The LLM is
   used only for:
   - (a) classifying `request_text`;
   - (b) one-time extraction from PNG screenshots;
   - (c) an optional fallback for evidence text no parser understands.
2. **No hardcoding.** Never put a `case_id`, `resource_id`, `user_id` or
   `evidence_id` literal in logic. Never read `sample_output.csv` outside
   `code/evaluation/`. No case-family shortcuts.
3. **Time contract.** Only evidence with `captured_at <= audit_at` may
   decide state or be cited. Later evidence may be used only to link a
   resource to an identity, and never to decide its state.
4. **Claims are not facts.** `reported_status` and `reported_result` never
   decide state.
5. **Every change is measured.** After any logic change, run
   `python code/evaluation/main.py` and report the metric deltas. No numbers
   means not done.
6. **Paths are relative to the repo root.** Secrets come only from env
   vars (`.env` is loaded by python-dotenv). Never print keys.
7. **Tests.** Every module gets pytest unit tests built from real dataset
   rows.

## 1. Layout

```text
code/
  main.py                 # CLI entry: python code/main.py [--split full|sample] [--out PATH] [--no-llm]
  ghost/
    paths.py              # repo-root-relative paths
    loaders.py            # CSV loaders -> typed dataclasses
    identity.py           # IdentityResolver
    observation.py        # Observation dataclass (the common evidence record)
    parsers/
      sql.py              # inventory + replica SQL -> Observations (sqlite3 in-memory)
      json_records.py     # service exports, snapshot bundles, bespoke JSON
      logs.py             # key=value and JSON-line logs, grouped by trace_id
      text.py             # handover TXT pipe tables
      pdf.py              # pypdf text -> Observations
      images.py           # Gemini vision -> cached JSON -> Observations
      registry.py         # picks a parser per file; reports unparsed files
    linker.py             # which resources/observations/activities belong to the subject
    state.py              # resource state at audit_at + data-flow/restore risks
    policy.py             # data_category inference + deletion_rules.csv -> required treatment
    planner.py            # steps_json builder, ordering, evidence citation
    classifier.py         # request intent: rules + LLM, cached
    llm.py                # OpenAI-SDK wrapper (Gemini primary, Groq fallback), disk cache, usage ledger
    serialize.py          # deterministic CSV/JSON writing + schema validator
  evaluation/
    main.py               # compares predictions with sample_output.csv; writes metrics
    custom_cases/         # >=3 hand-built cases (inputs + expected), see §5
    evaluation_report.md  # required deliverable
    usage_report.md       # required deliverable (generated from the usage ledger)
  tests/                  # pytest
  docs/PLAN.md, docs/FINDINGS.md
  cache/                  # llm + vision caches (json), safe to commit
README.md                 # repo root: setup + run (goes into code.zip)
```

## 2. Core data model

```python
@dataclass(frozen=True)
class Observation:
    file_id: str            # evidence_XX
    captured_at: str        # ISO, from evidence_files.csv (or row observed_at if finer)
    system_id: str          # account_service, search_service, ...
    resource_id: str
    present: bool           # record exists in this capture
    copy: str               # "primary" | "replica:<name>" | "export" | "log" | "image" | ...
    fields: dict            # identifier fields as seen: email, phone, name, email_hash, user_ref
    flags: dict             # enabled, restore_allowed, retention_until, status_code, account_status, category_hint, state_hint
    link_keys: frozenset    # every identifier/user_id/hash this observation mentions (normalised)
    raw_ref: str            # file path + row/line/trace for debugging
```

Absence is first-class. If a capture's scope covers a system (from
`snapshot_scope`, a lookup returning 404, or `rows=0`), the parser emits
`present=False` for every resource it explicitly looked up.

## 3. Pipeline per case

1. **Classify** `request_text` as DELETION or NOT_DELETION. For
   NOT_DELETION, emit `NOT_APPLICABLE, []` and stop.
2. **Identity.** Resolve `email` (current or historical) to one or more
   `user_id`s plus the full key set: all emails, phones (digits), names and
   hashes, current and historical.
3. **Link.** A resource belongs to the subject if any of these hold:
   - (a) any observation's `link_keys` intersect the key set;
   - (b) it shares a log `trace_id` with such a line (done in the parser);
   - (c) a `LINK_RESOURCE_IDENTITY` activity names a subject key;
   - (d) a worker-window rule applies (deletion_worker events in
     [reported_at - 1.5h, reported_at + 2.5h]) that are unlinked, after
     global elimination across all cases.

   Tune (d) only with eval numbers.
4. **State at audit_at.** For each linked resource, keep observations with
   `captured_at <= audit_at`. The latest capture per copy wins. The resource
   is PRESENT if any copy (primary or replica) is present.
   - Identifiers count as exposed if any prohibited field is non-null.
   - A data flow is ACTIVE if the latest `enabled` is true.
   - Restore is a RISK if the latest `restore_allowed` is true.
   - A sync or restore after the delete that re-creates the record counts
     as PRESENT, provided evidence shows it.
5. **Policy.** Pick the category in this order:
   - handover/category hints;
   - the worker action type;
   - system and content defaults (§4).

   Then compare the required treatment with the actual state to get one of:
   compliant, violation(op), or retained.
6. **Plan.**
   - COMPLETE (no violations or risks): one VERIFICATION per relevant
     record, each citing the evidence that proves it.
   - INCOMPLETE: FINDINGS (with evidence), then REMEDIATION (`""`), then
     VERIFICATION (`""`) covering every relevant record.

   Sort by phase, then operation table order, then `system_id`, then
   `resource_id`. Number steps from 1.
7. **Serialize.** Rows in numeric `case_id` order. JSON uses a fixed key
   order and no NaN. The validator runs before writing.

## 4. Policy table (from deletion_rules.csv + samples)

| Category | Typical system / prefix | Required | Finding when violated | Verify op |
|---|---|---|---|---|
| USER_PROFILE | account_service `account_*` | DELETE | REPORT_REMAINING_RECORD | VERIFY_RECORD_ABSENT |
| SECONDARY_IDENTIFIER | identity copies | DELETE | REPORT_REMAINING_RECORD | VERIFY_RECORD_ABSENT |
| SEARCH_INDEX_ENTRY | search_service `search_*` | DELETE | REPORT_REMAINING_RECORD | VERIFY_RECORD_ABSENT |
| CACHE_ENTRY | cache_service `cache_*` | DELETE | REPORT_REMAINING_RECORD | VERIFY_RECORD_ABSENT |
| SUPPORT_ATTACHMENT | support_platform (attachment or subject-only copy) | DELETE | REPORT_REMAINING_RECORD | VERIFY_RECORD_ABSENT |
| SUPPORT_MESSAGE | support_platform (shared order/agent context) | REDACT | REPORT_EXPOSED_PII | VERIFY_RECORD_REDACTED |
| ANALYTICS_PROFILE / PRODUCT_TELEMETRY | analytics_platform | ANONYMIZE | REPORT_EXPOSED_PII | VERIFY_RECORD_ANONYMIZED |
| USER_EXPORT | export_service `export_*` | DELETE | REPORT_REMAINING_RECORD | VERIFY_RECORD_ABSENT |
| MARKETING_PROFILE | marketing_platform, or analytics contact/opt-in records | DELETE | REPORT_REMAINING_RECORD | VERIFY_RECORD_ABSENT |
| EMAIL_DELIVERY_LOG | email_delivery_* with email | REDACT | REPORT_EXPOSED_PII | VERIFY_RECORD_REDACTED |
| SECURITY_AUDIT_LOG | security_service `audit_record_*`, within retention | RETAIN | (none) | VERIFY_RECORD_PRESENT |
| LEGAL_HOLD_RECORD | active legal hold | RETAIN | (none) | VERIFY_RECORD_PRESENT |
| BACKUP_ARCHIVE | backup_service `backup_*` | RETAIN + block | REPORT_RESTORE_RISK if restore allowed | VERIFY_RESTORE_BLOCKED |
| ACCOUNT_TOMBSTONE | identity_service `tombstone_*` | REDACT (keep user_id) | REPORT_EXPOSED_PII | VERIFY_RECORD_REDACTED |
| DATA_FLOW | integration_service `data_flow_*` | disabled | REPORT_ACTIVE_DATA_FLOW | VERIFY_DATA_FLOW_DISABLED |

Remediation op mapping:

| Violation | Remediation |
|---|---|
| DELETE category | DELETE_RECORD |
| REDACT category | REDACT_RECORD |
| ANONYMIZE category | ANONYMIZE_RECORD |
| Active data flow | DISABLE_DATA_FLOW |
| Restore risk | BLOCK_RESTORE |

## 5. Evaluation (build first, extend always)

`code/evaluation/main.py` runs the pipeline on the sample split and reports:

- case-level status accuracy (plus a confusion matrix);
- step-level precision, recall and F1 on the tuple
  (phase, system_id, resource_id, operation, state, evidence_id), plus the
  same without evidence_id;
- field-level accuracy per field on aligned steps;
- sequence accuracy: the exact-order match rate, plus a normalized LCS;
- a per-case diff (missing and extra steps);
- `--custom`, which also runs `custom_cases/`.

Results are written to `code/evaluation/latest_metrics.md` and appended as a
history table to `evaluation_report.md`.

Custom cases (at least 3, each a small synthetic dataset folder with its own
expected output):

1. A non-deletion request, which must be NOT_APPLICABLE.
2. A request using a historical email, where the leftover record is found
   only via the old hash.
3. Replication drift: the primary is deleted but the replica still has the
   record.
4. (bonus) Evidence captured after audit_at that must be ignored.

## 6. LLM usage

- Use the OpenAI Python SDK with `base_url` switching.
- Primary: Gemini Flash via its OpenAI-compatible endpoint. Fallback: Groq
  (text only).
- Cache key: sha256(model + prompt + image bytes), stored in
  `code/cache/llm/*.json`.
- Retry on 429 with backoff, then fall back to the next provider.
- Ledger: every call appends provider, model, input and output tokens,
  latency and cache_hit to `code/cache/usage_ledger.jsonl`.
  `usage_report.md` is generated from this ledger.
- Classifier: dedupe the texts (about 375 unique), batch them, and require a
  JSON-only reply. A deterministic rule backstop catches explicit "do not
  delete/close" sentences. If the rules and the LLM disagree, log it and
  prefer NOT_DELETION only when an explicit keep-account sentence exists.
- `--no-llm` must still produce a full valid output, using rules and cached
  vision.

## 7. Build order (one Claude Code prompt per step)

| # | Step | Done when |
|---|---|---|
| P1 | Scaffold, serializer and validator, eval harness, baseline run | Eval prints metrics for the baseline; pytest is green |
| P2 | Loaders and IdentityResolver | Tests: all 518+32 emails resolve; historical keys present |
| P3 | Text-format parsers (SQL, JSON, logs, TXT, PDF) and a coverage report | Every non-PNG evidence file parses; prints observations per file; 0 unparsed |
| P4 | Vision extraction for the 11 PNGs (Gemini, cached) | 11 cached JSONs; observations emitted |
| P5 | Linker (keys + trace + catalog) | Sample gold-resource recall reported; target ≥ 90% of recoverable items |
| P6 | State, policy and planner, end to end on samples | First real metrics |
| P7 | Classifier (rules + LLM) | 10/10 NOT_APPLICABLE on the full split; no false positives |
| P8 | Failure-driven iteration (worker-window linking, evidence citation, support DELETE vs REDACT) | Metrics improve each loop |
| P9 | Full run, usage report, evaluation report, README, packaging check | 518 valid rows; reports written |

## 8. Submission checklist

- Root `output.csv`: exact header, 518 rows, numeric `case_id` order, valid
  JSON.
- `code.zip` contains: README.md, code/, `code/evaluation/*.md`. It does not
  contain dataset/, .venv, caches of secrets, or nested archives.
- `log.txt` is written by Claude Code per AGENTS.md §5.
- Agent trace: Claude Code JSONL files plus `/export`, and the claude.ai
  chat export used for planning.
