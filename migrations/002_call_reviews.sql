-- Persistent human validation of an AI-selected call type.
create table if not exists jarvis.call_reviews (
  id bigserial primary key,
  call_id bigint not null references jarvis.calls(id) on delete cascade,
  call_type_key text not null,
  reason text not null,
  reviewer_name text not null,
  reviewed_at timestamptz not null default now(),
  reanalysis_requested boolean not null default false,
  unique (call_id)
);
