-- Ghost Data Still Exists: point-in-time database snapshot
-- Dialect: SQLite-compatible SQL
-- Captured at: 2026-05-09T23:00:00Z
-- Database topology: failover_replica
-- Compare the primary and any replica tables for replication drift.

BEGIN TRANSACTION;
CREATE TABLE customer_profiles_failover_replica (
                resource_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
                account_status TEXT NOT NULL, updated_at TEXT NOT NULL
            );
INSERT INTO "customer_profiles_failover_replica" VALUES('account_7701','user_92','Mirela Costa','mirela.costa@example.test','+351 912 555 019','ACTIVE','2026-05-09T22:45:00Z');
INSERT INTO "customer_profiles_failover_replica" VALUES('account_7702','user_7702','Other Person','other.person@example.test','+351 912 555 020','ACTIVE','2026-05-09T22:45:00Z');
CREATE TABLE customer_profiles_primary (
                resource_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
                account_status TEXT NOT NULL, updated_at TEXT NOT NULL
            );
INSERT INTO "customer_profiles_primary" VALUES('account_7702','user_7702','Other Person','other.person@example.test','+351 912 555 020','ACTIVE','2026-05-09T22:45:00Z');
COMMIT;
