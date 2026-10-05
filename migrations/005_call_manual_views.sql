begin;

create table if not exists jarvis.call_manual_views (
  call_id bigint primary key references jarvis.calls(id) on delete cascade,
  reviewed boolean not null default false,
  reviewer_name text not null default '',
  reviewed_at timestamptz,
  updated_at timestamptz not null default now()
);

-- The table stays in the private Jarvis schema.  The web process may read and
-- set its own server-side marker; the browser never receives DB credentials.
revoke all on table jarvis.call_manual_views from public;
grant select, insert, update on table jarvis.call_manual_views to jarvis_web;
grant select, insert, update, delete on table jarvis.call_manual_views to jarvis_worker, service_role;

alter table jarvis.call_manual_views enable row level security;

drop policy if exists call_manual_views_jarvis_web on jarvis.call_manual_views;
create policy call_manual_views_jarvis_web
  on jarvis.call_manual_views for all to jarvis_web
  using (true) with check (true);

drop policy if exists call_manual_views_jarvis_worker on jarvis.call_manual_views;
create policy call_manual_views_jarvis_worker
  on jarvis.call_manual_views for all to jarvis_worker
  using (true) with check (true);

drop policy if exists call_manual_views_service_role on jarvis.call_manual_views;
create policy call_manual_views_service_role
  on jarvis.call_manual_views for all to service_role
  using (true) with check (true);

commit;
