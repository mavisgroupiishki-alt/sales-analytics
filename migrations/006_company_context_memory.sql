-- Durable, fact-only CRM memory for fast call analysis.
-- This schema is private: neither transcripts nor credentials are exposed
-- through the Supabase Data API.

create table if not exists jarvis.context_facts (
  id bigint generated always as identity primary key,
  call_id bigint not null references jarvis.calls(id) on delete cascade,
  analysis_id bigint not null references jarvis.call_analyses(id) on delete cascade,
  scope_type text not null check (scope_type in ('deal', 'contact', 'company')),
  scope_external_id text not null,
  funnel_id text not null default '',
  occurred_at timestamptz not null,
  fact jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (call_id, scope_type, scope_external_id, funnel_id)
);

create index if not exists context_facts_scope_recent_idx
  on jarvis.context_facts (scope_type, scope_external_id, funnel_id, occurred_at desc, call_id desc);
create index if not exists context_facts_analysis_idx
  on jarvis.context_facts (analysis_id);

create table if not exists jarvis.context_profiles (
  scope_type text not null check (scope_type in ('deal', 'contact', 'company')),
  scope_external_id text not null,
  funnel_id text not null default '',
  facts_count integer not null default 0 check (facts_count >= 0),
  latest_call_at timestamptz,
  current_state jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (scope_type, scope_external_id, funnel_id)
);

-- Populate the new memory from the newest persisted analysis of every call.
-- It is intentionally a projection of compact analysis fields, never of a
-- transcript. Re-running this migration is safe because of its upsert key.
with latest_analyses as (
  select distinct on (ca.call_id)
    ca.id as analysis_id,
    ca.call_id,
    ca.status,
    ca.result
  from jarvis.call_analyses ca
  order by ca.call_id, ca.analyzed_at desc nulls last, ca.id desc
), source_calls as (
  select
    c.id as call_id,
    c.source_call_id,
    c.occurred_at,
    la.analysis_id,
    la.result,
    re.payload,
    coalesce(cr.call_type_key, la.result #>> '{call_type,key}', 'unknown') as call_type_key,
    case cr.call_type_key
      when 'primary_incoming_new' then 'Первичный входящий (новый клиент)'
      when 'primary_incoming_existing' then 'Первичный входящий (действующий клиент)'
      when 'cold_new' then 'Первичный холодный (новый клиент)'
      when 'cold_periodika' then 'Первичный холодный (периодика, действующий клиент)'
      when 'cold_reactivation' then 'Первичный холодный (давно не было контакта, через пользу)'
      when 'kp_defense' then 'Защита КП'
      when 'kp_feedback' then 'Обратная связь по КП'
      when 'counteroffer' then 'Контроффер (особое предложение для клиента)'
      when 'objection_handling' then 'Отработка возражений'
      when 'payment_push' then 'Дожим клиента на оплату (через пользу или по итогам договорённостей)'
      when 'successful_payment' then 'Успешная оплата'
      when 'upsell' then 'Доп продажа (отдельный звонок)'
      when 'unknown' then 'Тип звонка требует проверки'
      else coalesce(la.result #>> '{call_type,label}', 'Тип не указан')
    end as call_type_label,
    coalesce(re.payload #>> '{crm,category_id}', '') as funnel_id
  from jarvis.calls c
  join latest_analyses la on la.call_id = c.id
  join jarvis.raw_events re on re.id = c.raw_event_id
  left join jarvis.call_reviews cr on cr.call_id = c.id
  where la.status not in ('excluded', 'failed')
), scopes as (
  select
    source_calls.*, 'deal'::text as scope_type,
    source_calls.payload #>> '{crm,owner_id}' as scope_external_id
  from source_calls
  where source_calls.payload #>> '{crm,owner_type}' = 'deal'

  union all

  select
    source_calls.*, 'company'::text as scope_type,
    coalesce(
      nullif(source_calls.payload #>> '{crm,company_id}', ''),
      case when source_calls.payload #>> '{crm,owner_type}' = 'company'
        then source_calls.payload #>> '{crm,owner_id}' end
    ) as scope_external_id
  from source_calls
  where coalesce(
    nullif(source_calls.payload #>> '{crm,company_id}', ''),
    case when source_calls.payload #>> '{crm,owner_type}' = 'company'
      then source_calls.payload #>> '{crm,owner_id}' end
  ) is not null

  union all

  select
    source_calls.*, 'contact'::text as scope_type,
    contact_id.value as scope_external_id
  from source_calls
  cross join lateral jsonb_array_elements_text(
    coalesce(source_calls.payload #> '{crm,contact_ids}', '[]'::jsonb)
  ) as contact_id(value)

  union all

  select
    source_calls.*, 'contact'::text as scope_type,
    source_calls.payload #>> '{crm,owner_id}' as scope_external_id
  from source_calls
  where source_calls.payload #>> '{crm,owner_type}' = 'contact'
), facts as (
  select distinct on (call_id, scope_type, scope_external_id, funnel_id)
    call_id, analysis_id, scope_type, scope_external_id, funnel_id, occurred_at,
    jsonb_build_object(
      'activity_id', source_call_id,
      'date', occurred_at,
      'call_type_key', left(call_type_key, 80),
      'call_type', left(call_type_label, 160),
      'summary', left(coalesce(result ->> 'summary', ''), 600),
      'outcome', left(coalesce(result ->> 'outcome', ''), 400),
      'next_step', left(coalesce(result ->> 'recommended_action', result ->> 'recommendation', ''), 400),
      'objections', coalesce(
        (
          select jsonb_agg(objection.text)
          from (
            select left(coalesce(moment.value ->> 'text', moment.value ->> 'detail'), 240) as text
            from jsonb_array_elements(coalesce(result -> 'key_moments', '[]'::jsonb)) with ordinality as moment(value, ordinal)
            where lower(coalesce(moment.value ->> 'type', '')) = 'negative'
              and coalesce(moment.value ->> 'text', moment.value ->> 'detail', '') <> ''
            order by moment.ordinal
            limit 3
          ) objection
        ),
        '[]'::jsonb
      )
    ) as fact
  from scopes
  where nullif(scope_external_id, '') is not null
    and funnel_id <> ''
  order by call_id, scope_type, scope_external_id, funnel_id, analysis_id desc
)
insert into jarvis.context_facts
  (call_id, analysis_id, scope_type, scope_external_id, funnel_id, occurred_at, fact)
select call_id, analysis_id, scope_type, scope_external_id, funnel_id, occurred_at, fact
from facts
on conflict (call_id, scope_type, scope_external_id, funnel_id) do update set
  analysis_id = excluded.analysis_id,
  occurred_at = excluded.occurred_at,
  fact = excluded.fact,
  updated_at = now();

insert into jarvis.context_profiles
  (scope_type, scope_external_id, funnel_id, facts_count, latest_call_at, current_state)
select
  scope_type,
  scope_external_id,
  funnel_id,
  count(*)::integer,
  max(occurred_at),
  jsonb_build_object(
    'latest_call_id', (array_agg(fact ->> 'activity_id' order by occurred_at desc, call_id desc))[1],
    'last_summary', (array_agg(fact ->> 'summary' order by occurred_at desc, call_id desc))[1],
    'last_outcome', (array_agg(fact ->> 'outcome' order by occurred_at desc, call_id desc))[1],
    'next_step', (array_agg(fact ->> 'next_step' order by occurred_at desc, call_id desc))[1]
  )
from jarvis.context_facts
group by scope_type, scope_external_id, funnel_id
on conflict (scope_type, scope_external_id, funnel_id) do update set
  facts_count = excluded.facts_count,
  latest_call_at = excluded.latest_call_at,
  current_state = excluded.current_state,
  updated_at = now();

revoke all on table jarvis.context_facts, jarvis.context_profiles from public;
grant select on table jarvis.context_facts, jarvis.context_profiles to jarvis_web;
grant select, insert, update, delete on table jarvis.context_facts, jarvis.context_profiles to jarvis_worker, service_role;
grant usage, select on sequence jarvis.context_facts_id_seq to jarvis_worker, service_role;

alter table jarvis.context_facts enable row level security;
alter table jarvis.context_profiles enable row level security;

drop policy if exists context_facts_jarvis_web on jarvis.context_facts;
create policy context_facts_jarvis_web on jarvis.context_facts
  for select to jarvis_web using (true);
drop policy if exists context_profiles_jarvis_web on jarvis.context_profiles;
create policy context_profiles_jarvis_web on jarvis.context_profiles
  for select to jarvis_web using (true);

drop policy if exists context_facts_jarvis_worker on jarvis.context_facts;
create policy context_facts_jarvis_worker on jarvis.context_facts
  for all to jarvis_worker using (true) with check (true);
drop policy if exists context_profiles_jarvis_worker on jarvis.context_profiles;
create policy context_profiles_jarvis_worker on jarvis.context_profiles
  for all to jarvis_worker using (true) with check (true);

drop policy if exists context_facts_service_role on jarvis.context_facts;
create policy context_facts_service_role on jarvis.context_facts
  for all to service_role using (true) with check (true);
drop policy if exists context_profiles_service_role on jarvis.context_profiles;
create policy context_profiles_service_role on jarvis.context_profiles
  for all to service_role using (true) with check (true);
