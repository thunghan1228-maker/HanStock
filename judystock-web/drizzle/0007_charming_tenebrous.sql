CREATE TABLE `watchlist_sync_state` (
	`user_email` text PRIMARY KEY NOT NULL,
	`payload` text NOT NULL,
	`revision` integer DEFAULT 1 NOT NULL,
	`primary_device_id` text NOT NULL,
	`updated_by_device_id` text NOT NULL,
	`updated_by_device_kind` text NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
