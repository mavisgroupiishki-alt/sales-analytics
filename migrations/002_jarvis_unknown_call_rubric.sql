-- Calls with an unconfirmed business scenario still receive a factual score
-- on universal criteria, without guessing whether the client is new or active.
update jarvis.rubric_criteria
set applies_to_call_types = array_append(applies_to_call_types, 'unknown')
where code in ('expertise', 'next_step', 'communication')
  and not ('unknown' = any(applies_to_call_types));
