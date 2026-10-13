-- Server-owned page cache for operator-authorized public notice publication.
create table if not exists public.notice_public_pages (
  soop_id text not null,
  page integer not null check (page between 1 and 1000),
  payload jsonb not null,
  fetched_at timestamptz not null default now(),
  primary key (soop_id, page)
);
alter table public.notice_public_pages enable row level security;
revoke all on public.notice_public_pages from public, anon, authenticated;
grant select, insert, update, delete on public.notice_public_pages to service_role;
