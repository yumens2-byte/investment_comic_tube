alter table public.episodes
  add column if not exists video_path text,
  add column if not exists video_hash text check (video_hash is null or length(video_hash) = 64),
  add column if not exists video_metadata jsonb;
