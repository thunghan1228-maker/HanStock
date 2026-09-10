CREATE TABLE `admin_sessions` (
	`token_hash` text PRIMARY KEY NOT NULL,
	`admin_user_id` integer NOT NULL,
	`created_at` integer NOT NULL,
	`expires_at` integer NOT NULL,
	`last_seen_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `admin_sessions_user_expiry_idx` ON `admin_sessions` (`admin_user_id`,`expires_at`);--> statement-breakpoint
CREATE TABLE `admin_setup_tokens` (
	`token_hash` text PRIMARY KEY NOT NULL,
	`created_at` integer NOT NULL,
	`expires_at` integer NOT NULL,
	`used_at` integer
);
--> statement-breakpoint
ALTER TABLE `admin_users` ADD `username` text;--> statement-breakpoint
ALTER TABLE `admin_users` ADD `password_hash` text;--> statement-breakpoint
ALTER TABLE `admin_users` ADD `password_salt` text;--> statement-breakpoint
ALTER TABLE `admin_users` ADD `password_iterations` integer;--> statement-breakpoint
ALTER TABLE `admin_users` ADD `failed_login_count` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE `admin_users` ADD `locked_until` integer;--> statement-breakpoint
ALTER TABLE `admin_users` ADD `password_updated_at` integer;--> statement-breakpoint
CREATE UNIQUE INDEX `admin_users_username_idx` ON `admin_users` (`username`);--> statement-breakpoint
INSERT OR IGNORE INTO `admin_setup_tokens` (`token_hash`, `created_at`, `expires_at`)
VALUES ('c0nnxKHvhQmdzeT7XWoh8Q61wnON85flNBsGu75nfiA', 1787212006630, 1787384300552);
