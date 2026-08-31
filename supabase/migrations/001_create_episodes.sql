create table if not exists public.episodes (
  episode_id text primary key check (episode_id ~ '^EP-[0-9]{8}-[0-9]{2}$'),
  market_date date not null,
  status text not null check (status in (
    'DRAFT', 'DATA_READY', 'SCRIPT_READY', 'ASSETS_READY', 'RENDERED',
    'QA_PASSED', 'REVIEW_PENDING', 'APPROVED', 'UPLOADED_PRIVATE',
    'PUBLISHED', 'REJECTED', 'FAILED'
  )),
  content_hash text not null check (length(content_hash) = 64),
  payload jsonb not null,
  total_duration_seconds numeric(5, 2) not null
    check (total_duration_seconds between 15 and 60),
  youtube_video_id text unique,
  approved_by text,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.episodes enable row level security;

-- No public policy is intentionally created. The ingestion worker must use a
-- server-side secret, while reviewer access should get a narrow authenticated
-- policy in a later migration. Never expose a service-role key in browser code.
