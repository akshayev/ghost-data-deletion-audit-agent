# Latest evaluation metrics (sample split)

Cases: 32 | gold steps: 193 | predicted steps: 193

| Metric | Value |
|---|---|
| Status accuracy | 100.0% |
| Step P / R / F1 (full tuple) | 96.9% / 96.9% / 96.9% |
| Step P / R / F1 (no evidence_id) | 96.9% / 96.9% / 96.9% |
| Alignment rate (phase, system, resource, operation) | 96.9% |
| Field accuracy: state | 100.0% |
| Field accuracy: evidence_id | 100.0% |
| Sequence exact match | 71.9% |
| Sequence mean normalized LCS | 94.6% |

## Status confusion matrix (rows = gold, columns = predicted)

| gold \ pred | COMPLETE | INCOMPLETE | NOT_APPLICABLE |
|---|---|---|---|
| COMPLETE | 10 | 0 | 0 |
| INCOMPLETE | 0 | 22 | 0 |
| NOT_APPLICABLE | 0 | 0 | 0 |

## Per-case diff

### case_04: gold COMPLETE, predicted COMPLETE
- missing (link miss): `VERIFICATION | account_service | account_004 | VERIFY_RECORD_ABSENT | ABSENT | evidence_54`

### case_09: gold INCOMPLETE, predicted INCOMPLETE
- extra (link extra): `VERIFICATION | analytics_platform | analytics_5718 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | `

### case_18: gold INCOMPLETE, predicted INCOMPLETE
- missing (link miss): `VERIFICATION | security_service | audit_record_6645 | VERIFY_RECORD_PRESENT | PRESENT | `

### case_19: gold INCOMPLETE, predicted INCOMPLETE
- extra (link extra): `VERIFICATION | analytics_platform | analytics_3264 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | `

### case_20: gold INCOMPLETE, predicted INCOMPLETE
- missing (link miss): `VERIFICATION | security_service | audit_record_5569 | VERIFY_RECORD_PRESENT | PRESENT | `
- extra (link extra): `VERIFICATION | analytics_platform | analytics_2265 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | `

### case_23: gold INCOMPLETE, predicted INCOMPLETE
- missing (link miss): `VERIFICATION | account_service | account_410 | VERIFY_RECORD_ABSENT | ABSENT | `
- extra (link extra): `VERIFICATION | account_service | account_7015 | VERIFY_RECORD_ABSENT | ABSENT | `

### case_24: gold INCOMPLETE, predicted INCOMPLETE
- extra (link extra): `VERIFICATION | analytics_platform | analytics_1895 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | `

### case_25: gold COMPLETE, predicted COMPLETE
- missing (link miss): `VERIFICATION | analytics_platform | analytics_1895 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | evidence_92`
- extra (link extra): `VERIFICATION | analytics_platform | analytics_4566 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | evidence_92`

### case_26: gold COMPLETE, predicted COMPLETE
- missing (link miss): `VERIFICATION | analytics_platform | analytics_4566 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | evidence_92`
