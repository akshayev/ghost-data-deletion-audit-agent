# Latest evaluation metrics (sample split)

Cases: 32 | gold steps: 193 | predicted steps: 0

| Metric | Value |
|---|---|
| Status accuracy | 0.0% |
| Step P / R / F1 (full tuple) | 0.0% / 0.0% / 0.0% |
| Step P / R / F1 (no evidence_id) | 0.0% / 0.0% / 0.0% |
| Alignment rate (phase, system, resource, operation) | 0.0% |
| Field accuracy: state | n/a |
| Field accuracy: evidence_id | n/a |
| Sequence exact match | 0.0% |
| Sequence mean normalized LCS | 0.0% |

## Status confusion matrix (rows = gold, columns = predicted)

| gold \ pred | COMPLETE | INCOMPLETE | NOT_APPLICABLE |
|---|---|---|---|
| COMPLETE | 0 | 0 | 10 |
| INCOMPLETE | 0 | 0 | 22 |
| NOT_APPLICABLE | 0 | 0 | 0 |

## Per-case diff

### case_01: gold COMPLETE, predicted NOT_APPLICABLE
- missing: `VERIFICATION | account_service | account_001 | VERIFY_RECORD_ABSENT | ABSENT | evidence_54`

### case_02: gold COMPLETE, predicted NOT_APPLICABLE
- missing: `VERIFICATION | account_service | account_002 | VERIFY_RECORD_ABSENT | ABSENT | evidence_54`

### case_03: gold COMPLETE, predicted NOT_APPLICABLE
- missing: `VERIFICATION | account_service | account_7566 | VERIFY_RECORD_ABSENT | ABSENT | evidence_94`
- missing: `VERIFICATION | security_service | audit_record_2016 | VERIFY_RECORD_PRESENT | PRESENT | evidence_94`
- missing: `VERIFICATION | analytics_platform | analytics_9406 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | evidence_94`

### case_04: gold COMPLETE, predicted NOT_APPLICABLE
- missing: `VERIFICATION | account_service | account_004 | VERIFY_RECORD_ABSENT | ABSENT | evidence_54`
- missing: `VERIFICATION | analytics_platform | analytics_901 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | evidence_56`

### case_05: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | account_service | account_005 | REPORT_REMAINING_RECORD | PRESENT | evidence_54`
- missing: `REMEDIATION | account_service | account_005 | DELETE_RECORD | ABSENT | `
- missing: `VERIFICATION | account_service | account_005 | VERIFY_RECORD_ABSENT | ABSENT | `

### case_06: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | search_service | search_001 | REPORT_REMAINING_RECORD | PRESENT | evidence_57`
- missing: `REMEDIATION | search_service | search_001 | DELETE_RECORD | ABSENT | `
- missing: `VERIFICATION | account_service | account_006 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_001 | VERIFY_RECORD_ABSENT | ABSENT | `

### case_07: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | support_platform | support_001 | REPORT_REMAINING_RECORD | PRESENT | evidence_58`
- missing: `REMEDIATION | support_platform | support_001 | DELETE_RECORD | ABSENT | `
- missing: `VERIFICATION | account_service | account_007 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | support_platform | support_001 | VERIFY_RECORD_ABSENT | ABSENT | `

### case_08: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | export_service | export_001 | REPORT_REMAINING_RECORD | PRESENT | evidence_59`
- missing: `REMEDIATION | export_service | export_001 | DELETE_RECORD | ABSENT | `
- missing: `VERIFICATION | account_service | account_008 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | export_service | export_001 | VERIFY_RECORD_ABSENT | ABSENT | `

### case_09: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | backup_service | backup_4978 | REPORT_RESTORE_RISK | ACTIVE | evidence_93`
- missing: `REMEDIATION | backup_service | backup_4978 | BLOCK_RESTORE | BLOCKED | `
- missing: `VERIFICATION | account_service | account_3960 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_4902 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | backup_service | backup_4978 | VERIFY_RESTORE_BLOCKED | BLOCKED | `

### case_10: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | support_platform | support_002 | REPORT_REMAINING_RECORD | PRESENT | evidence_61`
- missing: `REMEDIATION | support_platform | support_002 | DELETE_RECORD | ABSENT | `
- missing: `VERIFICATION | account_service | account_010 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | support_platform | support_002 | VERIFY_RECORD_ABSENT | ABSENT | `

### case_11: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | search_service | search_052 | REPORT_REMAINING_RECORD | PRESENT | evidence_03`
- missing: `FINDING | analytics_platform | analytics_2034 | REPORT_EXPOSED_PII | PRESENT | evidence_03`
- missing: `FINDING | support_platform | support_864 | REPORT_EXPOSED_PII | PRESENT | evidence_03`
- missing: `FINDING | integration_service | data_flow_9522 | REPORT_ACTIVE_DATA_FLOW | ACTIVE | evidence_03`
- missing: `REMEDIATION | integration_service | data_flow_9522 | DISABLE_DATA_FLOW | DISABLED | `
- missing: `REMEDIATION | search_service | search_052 | DELETE_RECORD | ABSENT | `
- missing: `REMEDIATION | support_platform | support_864 | REDACT_RECORD | REDACTED | `
- missing: `REMEDIATION | analytics_platform | analytics_2034 | ANONYMIZE_RECORD | ANONYMIZED | `
- missing: `VERIFICATION | account_service | account_4248 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_052 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_9394 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | support_platform | support_864 | VERIFY_RECORD_REDACTED | REDACTED | `
- missing: `VERIFICATION | analytics_platform | analytics_2034 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | `
- missing: `VERIFICATION | integration_service | data_flow_9522 | VERIFY_DATA_FLOW_DISABLED | DISABLED | `

### case_12: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | analytics_platform | analytics_9914 | REPORT_REMAINING_RECORD | PRESENT | evidence_01`
- missing: `REMEDIATION | analytics_platform | analytics_9914 | DELETE_RECORD | ABSENT | `
- missing: `VERIFICATION | account_service | account_9797 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | analytics_platform | analytics_9914 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_2945 | VERIFY_RECORD_PRESENT | PRESENT | `

### case_13: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | search_service | search_1308 | REPORT_REMAINING_RECORD | PRESENT | evidence_03`
- missing: `FINDING | support_platform | support_4218 | REPORT_REMAINING_RECORD | PRESENT | evidence_03`
- missing: `REMEDIATION | search_service | search_1308 | DELETE_RECORD | ABSENT | `
- missing: `REMEDIATION | support_platform | support_4218 | DELETE_RECORD | ABSENT | `
- missing: `VERIFICATION | account_service | account_9335 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_1308 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | support_platform | support_4218 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_4816 | VERIFY_RECORD_PRESENT | PRESENT | `

### case_14: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | backup_service | backup_3870 | REPORT_RESTORE_RISK | ACTIVE | evidence_03`
- missing: `REMEDIATION | backup_service | backup_3870 | BLOCK_RESTORE | BLOCKED | `
- missing: `VERIFICATION | account_service | account_3677 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_987 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | backup_service | backup_3870 | VERIFY_RESTORE_BLOCKED | BLOCKED | `

### case_15: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | integration_service | data_flow_9846 | REPORT_ACTIVE_DATA_FLOW | ACTIVE | evidence_01`
- missing: `REMEDIATION | integration_service | data_flow_9846 | DISABLE_DATA_FLOW | DISABLED | `
- missing: `VERIFICATION | account_service | account_4745 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_1596 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | integration_service | data_flow_9846 | VERIFY_DATA_FLOW_DISABLED | DISABLED | `

### case_16: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | account_service | account_994 | REPORT_REMAINING_RECORD | PRESENT | evidence_05`
- missing: `FINDING | search_service | search_1007 | REPORT_REMAINING_RECORD | PRESENT | evidence_05`
- missing: `REMEDIATION | account_service | account_994 | DELETE_RECORD | ABSENT | `
- missing: `REMEDIATION | search_service | search_1007 | DELETE_RECORD | ABSENT | `
- missing: `VERIFICATION | account_service | account_994 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_1007 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_9528 | VERIFY_RECORD_PRESENT | PRESENT | `

### case_17: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | backup_service | backup_3245 | REPORT_RESTORE_RISK | ACTIVE | evidence_01`
- missing: `REMEDIATION | backup_service | backup_3245 | BLOCK_RESTORE | BLOCKED | `
- missing: `VERIFICATION | account_service | account_4092 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_1797 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | backup_service | backup_3245 | VERIFY_RESTORE_BLOCKED | BLOCKED | `

### case_18: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | search_service | search_4013 | REPORT_REMAINING_RECORD | PRESENT | evidence_94`
- missing: `FINDING | analytics_platform | analytics_037 | REPORT_EXPOSED_PII | PRESENT | evidence_94`
- missing: `FINDING | support_platform | support_5569 | REPORT_EXPOSED_PII | PRESENT | evidence_94`
- missing: `FINDING | integration_service | data_flow_4890 | REPORT_ACTIVE_DATA_FLOW | ACTIVE | evidence_94`
- missing: `REMEDIATION | integration_service | data_flow_4890 | DISABLE_DATA_FLOW | DISABLED | `
- missing: `REMEDIATION | search_service | search_4013 | DELETE_RECORD | ABSENT | `
- missing: `REMEDIATION | support_platform | support_5569 | REDACT_RECORD | REDACTED | `
- missing: `REMEDIATION | analytics_platform | analytics_037 | ANONYMIZE_RECORD | ANONYMIZED | `
- missing: `VERIFICATION | account_service | account_5521 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_4013 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_6645 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | support_platform | support_5569 | VERIFY_RECORD_REDACTED | REDACTED | `
- missing: `VERIFICATION | analytics_platform | analytics_037 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | `
- missing: `VERIFICATION | integration_service | data_flow_4890 | VERIFY_DATA_FLOW_DISABLED | DISABLED | `

### case_19: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | search_service | search_6083 | REPORT_REMAINING_RECORD | PRESENT | evidence_01`
- missing: `FINDING | support_platform | support_9553 | REPORT_EXPOSED_PII | PRESENT | evidence_01`
- missing: `REMEDIATION | search_service | search_6083 | DELETE_RECORD | ABSENT | `
- missing: `REMEDIATION | support_platform | support_9553 | REDACT_RECORD | REDACTED | `
- missing: `VERIFICATION | account_service | account_9102 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_6083 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_6793 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | support_platform | support_9553 | VERIFY_RECORD_REDACTED | REDACTED | `

### case_20: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | search_service | search_4262 | REPORT_REMAINING_RECORD | PRESENT | evidence_94`
- missing: `FINDING | integration_service | data_flow_1770 | REPORT_ACTIVE_DATA_FLOW | ACTIVE | evidence_94`
- missing: `REMEDIATION | integration_service | data_flow_1770 | DISABLE_DATA_FLOW | DISABLED | `
- missing: `REMEDIATION | search_service | search_4262 | DELETE_RECORD | ABSENT | `
- missing: `VERIFICATION | account_service | account_5037 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_4262 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_5569 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | integration_service | data_flow_1770 | VERIFY_DATA_FLOW_DISABLED | DISABLED | `

### case_21: gold COMPLETE, predicted NOT_APPLICABLE
- missing: `VERIFICATION | account_service | account_6440 | VERIFY_RECORD_ABSENT | ABSENT | evidence_92`
- missing: `VERIFICATION | security_service | audit_record_3737 | VERIFY_RECORD_PRESENT | PRESENT | evidence_92`
- missing: `VERIFICATION | analytics_platform | analytics_5335 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | evidence_92`

### case_22: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | search_service | search_8911 | REPORT_REMAINING_RECORD | PRESENT | evidence_05`
- missing: `FINDING | analytics_platform | analytics_4440 | REPORT_EXPOSED_PII | PRESENT | evidence_05`
- missing: `REMEDIATION | search_service | search_8911 | DELETE_RECORD | ABSENT | `
- missing: `REMEDIATION | analytics_platform | analytics_4440 | ANONYMIZE_RECORD | ANONYMIZED | `
- missing: `VERIFICATION | account_service | account_4990 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_8911 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_3482 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | analytics_platform | analytics_4440 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | `

### case_23: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | export_service | export_7364 | REPORT_REMAINING_RECORD | PRESENT | evidence_92`
- missing: `FINDING | search_service | search_898 | REPORT_REMAINING_RECORD | PRESENT | evidence_92`
- missing: `REMEDIATION | export_service | export_7364 | DELETE_RECORD | ABSENT | `
- missing: `REMEDIATION | search_service | search_898 | DELETE_RECORD | ABSENT | `
- missing: `VERIFICATION | account_service | account_410 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | export_service | export_7364 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_898 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_4128 | VERIFY_RECORD_PRESENT | PRESENT | `

### case_24: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | account_service | account_5334 | REPORT_REMAINING_RECORD | PRESENT | evidence_92`
- missing: `FINDING | search_service | search_5577 | REPORT_REMAINING_RECORD | PRESENT | evidence_92`
- missing: `REMEDIATION | account_service | account_5334 | DELETE_RECORD | ABSENT | `
- missing: `REMEDIATION | search_service | search_5577 | DELETE_RECORD | ABSENT | `
- missing: `VERIFICATION | account_service | account_5334 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_5577 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_7095 | VERIFY_RECORD_PRESENT | PRESENT | `

### case_25: gold COMPLETE, predicted NOT_APPLICABLE
- missing: `VERIFICATION | account_service | account_3136 | VERIFY_RECORD_ABSENT | ABSENT | evidence_92`
- missing: `VERIFICATION | security_service | audit_record_451 | VERIFY_RECORD_PRESENT | PRESENT | evidence_92`
- missing: `VERIFICATION | analytics_platform | analytics_1895 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | evidence_92`

### case_26: gold COMPLETE, predicted NOT_APPLICABLE
- missing: `VERIFICATION | account_service | account_5684 | VERIFY_RECORD_ABSENT | ABSENT | evidence_92`
- missing: `VERIFICATION | security_service | audit_record_5416 | VERIFY_RECORD_PRESENT | PRESENT | evidence_92`
- missing: `VERIFICATION | support_platform | support_4715 | VERIFY_RECORD_REDACTED | REDACTED | evidence_92`
- missing: `VERIFICATION | analytics_platform | analytics_4566 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | evidence_92`
- missing: `VERIFICATION | integration_service | data_flow_6244 | VERIFY_DATA_FLOW_DISABLED | DISABLED | evidence_92`
- missing: `VERIFICATION | backup_service | backup_3120 | VERIFY_RESTORE_BLOCKED | BLOCKED | evidence_92`

### case_27: gold COMPLETE, predicted NOT_APPLICABLE
- missing: `VERIFICATION | account_service | account_3866 | VERIFY_RECORD_ABSENT | ABSENT | evidence_92`
- missing: `VERIFICATION | security_service | audit_record_3830 | VERIFY_RECORD_PRESENT | PRESENT | evidence_92`
- missing: `VERIFICATION | support_platform | support_4707 | VERIFY_RECORD_REDACTED | REDACTED | evidence_92`
- missing: `VERIFICATION | analytics_platform | analytics_4574 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | evidence_92`
- missing: `VERIFICATION | integration_service | data_flow_2295 | VERIFY_DATA_FLOW_DISABLED | DISABLED | evidence_92`
- missing: `VERIFICATION | backup_service | backup_5970 | VERIFY_RESTORE_BLOCKED | BLOCKED | evidence_92`

### case_28: gold COMPLETE, predicted NOT_APPLICABLE
- missing: `VERIFICATION | account_service | account_5482 | VERIFY_RECORD_ABSENT | ABSENT | evidence_92`
- missing: `VERIFICATION | security_service | audit_record_9265 | VERIFY_RECORD_PRESENT | PRESENT | evidence_92`
- missing: `VERIFICATION | support_platform | support_2351 | VERIFY_RECORD_REDACTED | REDACTED | evidence_92`
- missing: `VERIFICATION | analytics_platform | analytics_6077 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | evidence_92`
- missing: `VERIFICATION | integration_service | data_flow_2337 | VERIFY_DATA_FLOW_DISABLED | DISABLED | evidence_92`
- missing: `VERIFICATION | backup_service | backup_838 | VERIFY_RESTORE_BLOCKED | BLOCKED | evidence_92`

### case_29: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | search_service | search_1993 | REPORT_REMAINING_RECORD | PRESENT | evidence_92`
- missing: `FINDING | analytics_platform | analytics_5208 | REPORT_EXPOSED_PII | PRESENT | evidence_92`
- missing: `FINDING | support_platform | support_5281 | REPORT_EXPOSED_PII | PRESENT | evidence_92`
- missing: `FINDING | integration_service | data_flow_3827 | REPORT_ACTIVE_DATA_FLOW | ACTIVE | evidence_92`
- missing: `REMEDIATION | integration_service | data_flow_3827 | DISABLE_DATA_FLOW | DISABLED | `
- missing: `REMEDIATION | search_service | search_1993 | DELETE_RECORD | ABSENT | `
- missing: `REMEDIATION | support_platform | support_5281 | REDACT_RECORD | REDACTED | `
- missing: `REMEDIATION | analytics_platform | analytics_5208 | ANONYMIZE_RECORD | ANONYMIZED | `
- missing: `VERIFICATION | account_service | account_7012 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_1993 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_3954 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | support_platform | support_5281 | VERIFY_RECORD_REDACTED | REDACTED | `
- missing: `VERIFICATION | analytics_platform | analytics_5208 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | `
- missing: `VERIFICATION | integration_service | data_flow_3827 | VERIFY_DATA_FLOW_DISABLED | DISABLED | `

### case_30: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | search_service | search_9359 | REPORT_REMAINING_RECORD | PRESENT | evidence_92`
- missing: `FINDING | analytics_platform | analytics_3697 | REPORT_EXPOSED_PII | PRESENT | evidence_92`
- missing: `FINDING | support_platform | support_6477 | REPORT_EXPOSED_PII | PRESENT | evidence_92`
- missing: `FINDING | integration_service | data_flow_8524 | REPORT_ACTIVE_DATA_FLOW | ACTIVE | evidence_92`
- missing: `REMEDIATION | integration_service | data_flow_8524 | DISABLE_DATA_FLOW | DISABLED | `
- missing: `REMEDIATION | search_service | search_9359 | DELETE_RECORD | ABSENT | `
- missing: `REMEDIATION | support_platform | support_6477 | REDACT_RECORD | REDACTED | `
- missing: `REMEDIATION | analytics_platform | analytics_3697 | ANONYMIZE_RECORD | ANONYMIZED | `
- missing: `VERIFICATION | account_service | account_4163 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | search_service | search_9359 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_1707 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | support_platform | support_6477 | VERIFY_RECORD_REDACTED | REDACTED | `
- missing: `VERIFICATION | analytics_platform | analytics_3697 | VERIFY_RECORD_ANONYMIZED | ANONYMIZED | `
- missing: `VERIFICATION | integration_service | data_flow_8524 | VERIFY_DATA_FLOW_DISABLED | DISABLED | `

### case_31: gold COMPLETE, predicted NOT_APPLICABLE
- missing: `VERIFICATION | account_service | account_4857 | VERIFY_RECORD_ABSENT | ABSENT | evidence_51`
- missing: `VERIFICATION | security_service | audit_record_6909 | VERIFY_RECORD_PRESENT | PRESENT | evidence_51`
- missing: `VERIFICATION | identity_service | tombstone_440 | VERIFY_RECORD_REDACTED | REDACTED | evidence_51`

### case_32: gold INCOMPLETE, predicted NOT_APPLICABLE
- missing: `FINDING | identity_service | tombstone_9178 | REPORT_EXPOSED_PII | PRESENT | evidence_51`
- missing: `REMEDIATION | identity_service | tombstone_9178 | REDACT_RECORD | REDACTED | `
- missing: `VERIFICATION | account_service | account_8858 | VERIFY_RECORD_ABSENT | ABSENT | `
- missing: `VERIFICATION | security_service | audit_record_7068 | VERIFY_RECORD_PRESENT | PRESENT | `
- missing: `VERIFICATION | identity_service | tombstone_9178 | VERIFY_RECORD_REDACTED | REDACTED | `
