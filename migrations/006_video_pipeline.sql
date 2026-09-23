-- ststat Part 6: video collection ownership/support
-- Existing StarUniv video tables are reused. This migration is idempotent.

create table if not exists public.video_channels (
  channel_url text primary key,
  channel_id text,
  title text,
  display_name text,
  thumb text,
  uploads text,
  source_order integer not null unique,
  active boolean not null default true,
  updated_at timestamptz not null default now()
);

create table if not exists public.videos (
  id text primary key,
  channel_url text,
  title text not null,
  published timestamptz,
  thumb text,
  views bigint not null default 0,
  short boolean not null default false,
  hidden boolean not null default false,
  updated_at timestamptz not null default now()
);

create index if not exists videos_published_idx on public.videos (published desc);
create index if not exists videos_channel_idx on public.videos (channel_url);
create index if not exists videos_channel_published_idx on public.videos (channel_url, published desc);

comment on table public.videos is
  'Automatic video collection is owned by ststat; videos.hidden remains admin-owned.';

comment on table public.video_channels is
  'Admin owns channel_url/display_name/source_order/active. ststat owns channel_id/title/thumb/uploads metadata.';
