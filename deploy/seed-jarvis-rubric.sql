-- Run once in the private `jarvis` schema, then use the returned `id` as
-- JARVIS_RUBRIC_ID in the DigitalOcean worker and Render web-service settings.
-- This is intentionally not run automatically by application startup.
do $$
declare rubric_id bigint;
begin
  if exists (select 1 from jarvis.rubrics where code = 'jarvis_rop' and version = 1) then
    raise exception 'Rubric jarvis_rop v1 already exists; do not overwrite its history';
  end if;

  insert into jarvis.rubrics (code, version, title, status, effective_from)
  values ('jarvis_rop', 1, 'Джарвис: применимая оценка звонков', 'active', now())
  returning id into rubric_id;

  insert into jarvis.rubric_criteria
    (rubric_id, code, title, applies_to_call_types, weight, required_for_critical)
  values
    (rubric_id, 'opening', 'Понятное начало и цель разговора', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','successful_payment','upsell'], 0.06, false),
    (rubric_id, 'need', 'Выявление потребности или причины решения', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','upsell'], 0.16, false),
    (rubric_id, 'presentation', 'Презентация через пользу клиента', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','upsell'], 0.16, false),
    (rubric_id, 'expertise', 'Экспертность и точность ответа', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','successful_payment','upsell'], 0.10, false),
    (rubric_id, 'objection', 'Распознавание и отработка существенного возражения', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','upsell'], 0.18, true),
    (rubric_id, 'closing', 'Продвижение сделки к решению', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','counteroffer','objection_handling','payment_push','upsell'], 0.14, true),
    (rubric_id, 'next_step', 'Конкретный следующий шаг', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','successful_payment','upsell'], 0.16, true),
    (rubric_id, 'communication', 'Корректность и ясность общения', array['primary_incoming_new','primary_incoming_existing','cold_new','cold_periodika','cold_reactivation','kp_defense','kp_feedback','counteroffer','objection_handling','payment_push','successful_payment','upsell'], 0.04, true);
end $$;

select id, code, version, status from jarvis.rubrics where code = 'jarvis_rop' and version = 1;
