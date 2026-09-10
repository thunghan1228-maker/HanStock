-- Issue a fresh one-time setup token after the original crypto attempt failed
-- before credentials were persisted.
INSERT OR IGNORE INTO `admin_setup_tokens` (`token_hash`, `created_at`, `expires_at`)
VALUES ('EVrRT6vtHhtPWOraFZwp6c5EI4sHF2JlIu4-kVpVE6s', 1787212525697, 1787385313696);
