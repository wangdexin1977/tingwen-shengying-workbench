CREATE TABLE `site_visits` (
	`key` text PRIMARY KEY NOT NULL,
	`total` integer DEFAULT 0 NOT NULL,
	`started_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
