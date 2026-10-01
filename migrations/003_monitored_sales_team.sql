-- The sales-call ingest worker and the Jarvis settings page share this list.
-- The four initial records include the two newly added sales managers.
create table if not exists jarvis.monitored_sales_team (
  bitrix_user_id integer primary key,
  display_name text not null,
  active boolean not null default true,
  added_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into jarvis.monitored_sales_team (bitrix_user_id, display_name, active)
values
  (1286, 'Роман Авсеенко', true),
  (2100, 'Ирина Богомольцева', true),
  (2272, 'Алена Хурсик', true),
  (2274, 'Ирина Базылева', true)
on conflict (bitrix_user_id) do nothing;
