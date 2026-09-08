-- Align persisted statuses with the deterministic triage values emitted by
-- src/claude_analyzer.py and seed the first immutable production rubric.
alter table jarvis.call_analyses
  drop constraint if exists call_analyses_status_check;
alter table jarvis.call_analyses
  add constraint call_analyses_status_check check (
    status in ('pending', 'completed', 'critical', 'needs_review',
               'requires_reanalysis', 'normal', 'excluded', 'failed')
  );

insert into jarvis.rubrics (code, version, title, status, effective_from)
select 'jarvis_rop', 1, 'Джарвис: применимая оценка звонков', 'active', now()
where not exists (
  select 1 from jarvis.rubrics where code = 'jarvis_rop' and version = 1
);

insert into jarvis.rubric_criteria
  (rubric_id, code, title, applies_to_call_types, weight, required_for_critical)
select rubric.id, criterion.code, criterion.title, criterion.call_types, criterion.weight, criterion.required
from jarvis.rubrics rubric
cross join (values
  ('opening', 'Понятное начало и цель разговора', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','successful_payment','upsell']::text[], 0.06::numeric, false),
  ('need', 'Выявление потребности или причины решения', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','upsell']::text[], 0.16::numeric, false),
  ('presentation', 'Презентация через пользу клиента', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','upsell']::text[], 0.16::numeric, false),
  ('expertise', 'Экспертность и точность ответа', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','successful_payment','upsell']::text[], 0.10::numeric, false),
  ('objection', 'Распознавание и отработка существенного возражения', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','upsell']::text[], 0.18::numeric, true),
  ('closing', 'Продвижение сделки к решению', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','counteroffer','objection_handling','payment_push','upsell']::text[], 0.14::numeric, true),
  ('next_step', 'Конкретный следующий шаг', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','successful_payment','upsell']::text[], 0.16::numeric, true),
  ('communication', 'Корректность и ясность общения', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','successful_payment','upsell']::text[], 0.04::numeric, true)
) as criterion(code, title, call_types, weight, required)
where rubric.code = 'jarvis_rop' and rubric.version = 1
  and not exists (
    select 1 from jarvis.rubric_criteria existing
    where existing.rubric_id = rubric.id and existing.code = criterion.code
  );
