CREATE TABLE `weakest_group_events` (
	`id` text PRIMARY KEY NOT NULL,
	`day` text NOT NULL,
	`stamp` integer NOT NULL,
	`name` text NOT NULL,
	`change` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `weakest_group_events_day_stamp` ON `weakest_group_events` (`day`,`stamp`);--> statement-breakpoint
CREATE TABLE `weakest_group_state` (
	`day` text PRIMARY KEY NOT NULL,
	`stamp` integer NOT NULL,
	`names` text NOT NULL
);
