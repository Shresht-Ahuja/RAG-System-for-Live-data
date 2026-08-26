-- Small, deliberately simple SQLite schema for the resume-project deployment.
-- User identity is saved after Google sign-in. Provider credentials are encrypted
-- before they are stored in connections.encrypted_credentials.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    name TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS connections (
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    encrypted_credentials BLOB NOT NULL,
    expires_at INTEGER,
    metadata TEXT NOT NULL DEFAULT '{}',
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, provider),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_connections_user_id ON connections(user_id);
