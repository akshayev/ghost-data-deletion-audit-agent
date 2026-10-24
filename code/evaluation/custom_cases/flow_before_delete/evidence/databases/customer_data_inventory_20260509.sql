PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_search_documents (resource_id TEXT PRIMARY KEY, customer_reference TEXT, display_name TEXT, email TEXT, phone TEXT, email_hash TEXT, index_name TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE connector_subscriptions (resource_id TEXT PRIMARY KEY, customer_reference TEXT, customer_email TEXT, connector_name TEXT NOT NULL, enabled INTEGER NOT NULL CHECK (enabled IN (0,1)), last_delivery_status TEXT NOT NULL, updated_at TEXT NOT NULL);
COMMIT;
-- snapshot_completed_at=2026-05-09T06:00:00Z
INSERT INTO snapshot_scope VALUES ('search_service', '2026-05-09T06:00:00Z');
INSERT INTO snapshot_scope VALUES ('integration_service', '2026-05-09T06:00:00Z');
INSERT INTO customer_search_documents VALUES ('search_3302', 'user_94', 'Noor Al-Rashid', 'noor.alrashid@example.test', '+965 5550 1944', '5051cc59dbd24992c47e96e9493c44fd01c05fc90c1bcec4dc6a3eaab4944566', 'customer_directory_v3', '2026-05-09T05:50:00Z');
INSERT INTO connector_subscriptions VALUES ('data_flow_4410', 'user_94', 'noor.alrashid@example.test', 'crm_sync', 1, 'DELIVERED', '2026-05-09T05:50:00Z');
