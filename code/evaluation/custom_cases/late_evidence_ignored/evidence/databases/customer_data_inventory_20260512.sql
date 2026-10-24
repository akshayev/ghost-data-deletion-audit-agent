PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT, full_name TEXT, email TEXT, phone TEXT, email_hash TEXT, account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
COMMIT;
-- snapshot_completed_at=2026-05-12T08:00:00Z
INSERT INTO snapshot_scope VALUES ('account_service', '2026-05-12T08:00:00Z');
INSERT INTO customer_profiles VALUES ('account_6602', 'user_93', 'Haakon Lindqvist', 'haakon.lindqvist@example.test', '+47 912 55 019', 'b6e4fbb2c013ce74cf6ce41123e7c297a694c5e03779f6dec2c88fc37e86c41d', 'ACTIVE', '2026-05-12T07:50:00Z');
