begin;

create table if not exists jarvis.call_manual_views (
  call_id bigint primary key references jarvis.calls(id) on delete cascade,
  reviewed boolean not null default false,
  reviewer_name text not null default '',
  reviewed_at timestamptz,
  updated_at timestamptz not null default now()
);

commit;
