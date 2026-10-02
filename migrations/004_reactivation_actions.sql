-- Audit trail for explicit dashboard actions only. Queue calculation remains
-- read-only and does not write to this table.
begin;

create table if not exists jarvis.reactivation_actions (
  id bigint generated always as identity primary key,
  deal_id text not null,
  status text not null check (status in ('succeeded', 'rejected', 'failed')),
  actor text not null,
  from_category_id integer,
  from_stage_id text,
  to_category_id integer,
  to_stage_id text,
  error text,
  happened_at timestamptz not null,
  created_at timestamptz not null default now()
);

create index if not exists reactivation_actions_deal_happened_idx
  on jarvis.reactivation_actions (deal_id, happened_at desc);

revoke all on table jarvis.reactivation_actions from public;
grant select, insert on table jarvis.reactivation_actions to service_role;
grant usage, select on sequence jarvis.reactivation_actions_id_seq to service_role;
alter table jarvis.reactivation_actions enable row level security;
drop policy if exists reactivation_actions_service_role on jarvis.reactivation_actions;
create policy reactivation_actions_service_role on jarvis.reactivation_actions
  for all to service_role using (true) with check (true);

commit;
