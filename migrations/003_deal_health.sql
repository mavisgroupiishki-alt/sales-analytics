-- CRM Health v0: private, read-only analytical projection.
-- Apply only after the preflight schema-diff described in spec 006. This
-- migration neither changes Bitrix24 nor alters existing Jarvis tables.

begin;

create table if not exists jarvis.health_rule_versions (
  id bigint generated always as identity primary key,
  version text not null unique,
  status text not null check (status in ('draft', 'shadow', 'active', 'retired')),
  rules jsonb not null,
  created_at timestamptz not null default now(),
  activated_at timestamptz
);

create table if not exists jarvis.health_stage_rules (
  rule_version_id bigint not null references jarvis.health_rule_versions(id) on delete restrict,
  category_id integer not null,
  stage_id text not null,
  max_stage_hours integer,
  max_stage_business_days integer,
  max_contact_business_days integer,
  requires_product boolean not null default false,
  deferred_demand boolean not null default false,
  primary key (rule_version_id, category_id, stage_id),
  check (max_stage_hours is null or max_stage_hours >= 0),
  check (max_stage_business_days is null or max_stage_business_days >= 0),
  check (max_contact_business_days is null or max_contact_business_days >= 0)
);

create table if not exists jarvis.health_sync_runs (
  id uuid primary key,
  source text not null default 'bitrix24',
  input_sha256 char(64) not null,
  status text not null check (status in ('running', 'succeeded', 'failed')),
  received_count integer not null default 0 check (received_count >= 0),
  persisted_count integer not null default 0 check (persisted_count >= 0),
  error_type text,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  unique (source, input_sha256)
);

create table if not exists jarvis.deal_health_current (
  deal_id text primary key,
  category_id integer not null,
  stage_id text not null,
  responsible_id text,
  responsible_active boolean not null,
  score smallint not null check (score between 0 and 100),
  zone text not null check (zone in ('green', 'yellow', 'red')),
  primary_issue_code text,
  issue_codes jsonb not null,
  data_quality text not null check (data_quality in ('complete', 'incomplete')),
  facts jsonb not null,
  rule_version text not null,
  snapshot_sha256 char(64) not null,
  calculated_at timestamptz not null,
  last_sync_run_id uuid not null references jarvis.health_sync_runs(id) on delete restrict,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists jarvis.deal_health_snapshots (
  id bigint generated always as identity primary key,
  deal_id text not null,
  observed_on date not null,
  score smallint not null check (score between 0 and 100),
  zone text not null check (zone in ('green', 'yellow', 'red')),
  primary_issue_code text,
  issue_codes jsonb not null,
  data_quality text not null check (data_quality in ('complete', 'incomplete')),
  facts jsonb not null,
  rule_version text not null,
  snapshot_sha256 char(64) not null,
  calculated_at timestamptz not null,
  sync_run_id uuid not null references jarvis.health_sync_runs(id) on delete restrict,
  created_at timestamptz not null default now(),
  unique (deal_id, observed_on)
);

create table if not exists jarvis.deal_health_zone_events (
  id bigint generated always as identity primary key,
  deal_id text not null,
  from_zone text check (from_zone in ('green', 'yellow', 'red')),
  to_zone text not null check (to_zone in ('green', 'yellow', 'red')),
  score smallint not null check (score between 0 and 100),
  primary_issue_code text,
  rule_version text not null,
  changed_at timestamptz not null,
  sync_run_id uuid not null references jarvis.health_sync_runs(id) on delete restrict,
  unique (sync_run_id, deal_id)
);

create index if not exists deal_health_current_zone_idx on jarvis.deal_health_current (zone, calculated_at desc);
create index if not exists deal_health_current_owner_idx on jarvis.deal_health_current (responsible_id, zone);
create index if not exists deal_health_snapshots_deal_date_idx on jarvis.deal_health_snapshots (deal_id, observed_on desc);
create index if not exists deal_health_zone_events_deal_changed_idx on jarvis.deal_health_zone_events (deal_id, changed_at desc);

-- No browser-facing role receives access to Health facts. The private worker
-- uses the server-side Supabase service role; the explicit policies make this
-- boundary auditable instead of relying on an application convention.
do $$
begin
  if to_regrole('service_role') is null then
    raise exception 'CRM Health requires the Supabase service_role before enabling RLS';
  end if;
end $$;

revoke all on schema jarvis from public;
revoke all on table jarvis.health_rule_versions, jarvis.health_stage_rules,
  jarvis.health_sync_runs, jarvis.deal_health_current,
  jarvis.deal_health_snapshots, jarvis.deal_health_zone_events from public;
grant usage on schema jarvis to service_role;
grant select, insert, update, delete on table jarvis.health_rule_versions,
  jarvis.health_stage_rules, jarvis.health_sync_runs, jarvis.deal_health_current,
  jarvis.deal_health_snapshots, jarvis.deal_health_zone_events to service_role;
grant usage, select on all sequences in schema jarvis to service_role;
alter table jarvis.health_rule_versions enable row level security;
alter table jarvis.health_stage_rules enable row level security;
alter table jarvis.health_sync_runs enable row level security;
alter table jarvis.deal_health_current enable row level security;
alter table jarvis.deal_health_snapshots enable row level security;
alter table jarvis.deal_health_zone_events enable row level security;

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'health_rule_versions', 'health_stage_rules', 'health_sync_runs',
    'deal_health_current', 'deal_health_snapshots', 'deal_health_zone_events'
  ] loop
    execute format('drop policy if exists %I on jarvis.%I', table_name || '_service_role', table_name);
    execute format(
      'create policy %I on jarvis.%I for all to service_role using (true) with check (true)',
      table_name || '_service_role', table_name
    );
  end loop;
end $$;

insert into jarvis.health_rule_versions (version, status, rules)
values ('crm_health_v0', 'shadow', '{"source":"health_rules.py","checks":12}'::jsonb)
on conflict (version) do nothing;

commit;
