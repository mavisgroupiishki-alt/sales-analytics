"""
Анализ звонков через Whisper + Claude Haiku — версия по ТЗ.

Возможности:
- 17 критериев по ТЗ (матрица из Таблицы 2)
- Веса критериев по Таблице 3 → взвешенная оценка
- Двухосевая классификация (CRM-контекст + цель)
- Сильные/слабые стороны звонка отдельно
- Основная проблема разговора
- Сопоставление со скриптами по этапам (соблюдено/частично/пропущено)
- При оценке <6 — цитата+таймкод+рекомендация
- Дата следующего контакта
- Поддержка ручной правки через manual_corrections.json
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple

import whisper
from anthropic import Anthropic

logger = logging.getLogger(__name__)

MODEL_CLAUDE = "claude-haiku-4-5-20251001"
MODEL_WHISPER = "base"

# ============================================================
# 17 КРИТЕРИЕВ ПО ТЗ (Таблица 2)
# ============================================================
CRITERIA_TZ = [
    "Представление и корректное начало",
    "Подготовленность к звонку",
    "Четкость цели и рамки разговора",
    "Управление диалогом",
    "Полнота выявления потребности",
    "Глубина уточняющих вопросов",
    "Резюмирование потребности",
    "Презентация решения через пользу",
    "Экспертность и уверенность",
    "Допродажа / расширение решения",
    "Распознавание возражения",
    "Качество отработки возражений",
    "Попытка закрытия / продвижение сделки",
    "Фиксация следующего шага и даты связи",
    "Выполнение обещаний и связь с CRM",
    "Речь и эмоциональный фон",
    "Корректное завершение разговора",
]

# ============================================================
# ВЕСА КРИТЕРИЕВ ПО ТЗ (Таблица 3)
# ============================================================
CRITERIA_WEIGHTS = {
    # Выявление потребности и боли — 20%
    "Полнота выявления потребности": 0.10,
    "Глубина уточняющих вопросов": 0.05,
    "Резюмирование потребности": 0.05,
    # Аргументация и презентация ценности — 20%
    "Презентация решения через пользу": 0.12,
    "Экспертность и уверенность": 0.08,
    # Работа с возражениями — 20%
    "Распознавание возражения": 0.08,
    "Качество отработки возражений": 0.12,
    # Попытка закрытия и следующий шаг — 20%
    "Попытка закрытия / продвижение сделки": 0.10,
    "Фиксация следующего шага и даты связи": 0.07,
    "Выполнение обещаний и связь с CRM": 0.03,
    # Коммуникация — 10%
    "Представление и корректное начало": 0.02,
    "Подготовленность к звонку": 0.02,
    "Четкость цели и рамки разговора": 0.01,
    "Управление диалогом": 0.02,
    "Речь и эмоциональный фон": 0.02,
    "Корректное завершение разговора": 0.01,
    # Допродажа — 10%
    "Допродажа / расширение решения": 0.10,
}
# Проверка суммы (должно быть 1.0)
assert abs(sum(CRITERIA_WEIGHTS.values()) - 1.0) < 0.001, f"Сумма весов: {sum(CRITERIA_WEIGHTS.values())}"

# ============================================================
# ТРИГГЕРЫ (раздел 14.2 ТЗ + общие)
# ============================================================
TRIGGERS = [
    "Не отработано возражение «дорого»",
    "Не зафиксирован следующий шаг",
    "Не зафиксирована дата следующего контакта",
    "Упущена допродажа",
    "Не выявлены потребности клиента",
    "Скидка дана без переговоров",
    "Не предложена встреча",
    "Прерывал клиента",
    "Не использовал имя клиента",
    "Не уточнил сроки",
    "Не уточнил бюджет",
    "Не задал уточняющие вопросы",
    "Не предложил альтернативу при отказе",
    "Грубость или непрофессионализм",
    "Не закрыл звонок резюме договорённостей",
    "Клиент готов был купить, менеджер не закрыл",
    "Звонок завершился без понятного результата",
    "Менеджер не управляет структурой диалога",
    "Менеджер дал спорное обещание клиенту",
    "Эмоциональный фон негативный",
]

# Критичность
CRITICAL_TRIGGER_RUDENESS = "Грубость или непрофессионализм"
CRITICAL_SCORE_THRESHOLD = 5.0
CRITICAL_TRIGGERS_COUNT = 2


def is_critical(analysis: Dict[str, Any]) -> Tuple[bool, str]:
    score = analysis.get("overall_score", 10)
    triggers = analysis.get("triggers", []) or []
    trigger_names = [t.get("name", "") for t in triggers]

    if any(CRITICAL_TRIGGER_RUDENESS.lower() in n.lower() for n in trigger_names):
        return True, "Грубость в разговоре"
    try:
        if float(score) < CRITICAL_SCORE_THRESHOLD:
            return True, f"Низкая оценка ({score}/10)"
    except (TypeError, ValueError):
        pass
    if len(triggers) >= CRITICAL_TRIGGERS_COUNT:
        return True, f"{len(triggers)} триггеров"
    return False, ""


def compute_weighted_score(scores: Dict[str, float]) -> float:
    """Считает взвешенную оценку по весам из ТЗ."""
    if not scores:
        return 0.0
    total = 0.0
    for crit, weight in CRITERIA_WEIGHTS.items():
        val = scores.get(crit)
        try:
            total += float(val) * weight
        except (TypeError, ValueError):
            pass
    return round(total, 1)


# ============================================================
# СКРИПТЫ
# ============================================================
def load_scripts() -> Dict[str, Any]:
    p = Path("scripts.json")
    if not p.exists():
        logger.warning("scripts.json не найден")
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def select_relevant_scripts(transcript: str, scripts: Dict[str, Any]) -> List[Tuple[str, str]]:
    if not scripts:
        return []
    transcript_lower = transcript.lower()
    selected = []
    selected_names = set()

    # Всегда подгружаем базовые
    for name in scripts.get("_always_load", []):
        if name in scripts and isinstance(scripts[name], dict):
            selected.append((name, scripts[name]["text"]))
            selected_names.add(name)

    for name, data in scripts.items():
        if name.startswith("_") or name in selected_names:
            continue
        if not isinstance(data, dict):
            continue
        for kw in data.get("keywords", []):
            if kw.lower() in transcript_lower:
                selected.append((name, data["text"]))
                selected_names.add(name)
                break
    return selected


# ============================================================
# РУЧНЫЕ ПРАВКИ ОЦЕНОК (manual_corrections.json)
# ============================================================
def load_manual_corrections() -> Dict[str, Any]:
    p = Path("manual_corrections.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def apply_manual_corrections(activity_id: str, analysis: Dict[str, Any], corrections: Dict) -> Dict[str, Any]:
    """Применяет ручную правку оценки, если она есть."""
    if activity_id not in corrections:
        return analysis
    corr = corrections[activity_id]
    if "overall_score" in corr:
        analysis["overall_score"] = corr["overall_score"]
        analysis["manually_corrected"] = True
        analysis["manual_comment"] = corr.get("comment", "")
    if "scores" in corr:
        for crit, val in corr["scores"].items():
            analysis.setdefault("scores", {})[crit] = val
        analysis["manually_corrected"] = True
    return analysis


# ============================================================
# WHISPER
# ============================================================
def transcribe_audio(audio_path: Path, model) -> Dict[str, Any]:
    logger.info(f"Транскрибируем {audio_path.name}...")
    result = model.transcribe(str(audio_path), language="ru", verbose=False, fp16=False)
    full_text = result["text"].strip()
    duration_sec = 0
    segments = []
    if result.get("segments"):
        for seg in result["segments"]:
            segments.append({
                "start": round(seg.get("start", 0), 1),
                "end": round(seg.get("end", 0), 1),
                "text": seg.get("text", "").strip(),
            })
        duration_sec = result["segments"][-1].get("end", 0)
    text_with_timecodes = "\n".join(
        f"[{format_timecode(s['start'])}] {s['text']}" for s in segments
    )
    return {
        "text": full_text,
        "text_with_timecodes": text_with_timecodes,
        "segments": segments,
        "duration_sec": round(duration_sec, 1),
    }


def format_timecode(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


# ============================================================
# CLAUDE — НОВЫЙ ПРОМПТ С ВСЕМИ ТРЕБОВАНИЯМИ ТЗ
# ============================================================
def get_claude_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY не задан")
    return Anthropic(api_key=api_key)


def build_analysis_prompt(transcript_with_timecodes: str, call_meta: Dict, scripts: List[Tuple[str, str]]) -> str:
    call_info = (
        f"- Менеджер: {call_meta.get('manager', {}).get('name', 'неизвестно')}\n"
        f"- Клиент: {call_meta.get('client', {}).get('name', 'неизвестно')}\n"
        f"- Компания клиента: {call_meta.get('client', {}).get('company', 'неизвестно')}\n"
        f"- Направление: {'входящий' if call_meta.get('direction') == 'incoming' else 'исходящий'}\n"
        f"- Время: {call_meta.get('created', '')}\n"
        f"- В CRM связано с: {call_meta.get('crm', {}).get('owner_type', 'неизвестно')}\n"
    )

    criteria_list = "\n".join(f"   - {c}" for c in CRITERIA_TZ)
    scores_template = ",\n".join(f'    "{c}": 0.0' for c in CRITERIA_TZ)
    triggers_list = "\n".join(f"   - {t}" for t in TRIGGERS)

    scripts_block = ""
    scripts_stages_template = ""
    if scripts:
        scripts_block = "\n\nСКРИПТЫ MAVIS GROUP (используй для сопоставления звонка):\n"
        for name, text in scripts:
            scripts_block += f"\n=== {name} ===\n{text}\n"
        scripts_block += "\n"
        scripts_stages_template = """
  "scripts_alignment": [
    {"stage": "название стадии из скрипта", "status": "соблюдено|частично|пропущено", "evidence": "цитата или объяснение", "time": "MM:SS"}
  ],"""

    prompt = f"""Ты — эксперт по качеству продаж в компании Mavis Group (Беларусь, услуги по СРО, ISO, ГОСТ, СПК, аттестация специалистов в строительстве).

Проанализируй транскрипт звонка и дай структурированный разбор по техническому заданию.

ВАЖНО: транскрипт получен через Whisper, в нём могут быть искажения слов. Понимай смысл.
В транскрипте таймкоды [MM:SS] — используй их в цитатах и оценках.

ИНФОРМАЦИЯ О ЗВОНКЕ:
{call_info}
{scripts_block}
ТРАНСКРИПТ:
---
{transcript_with_timecodes}
---

ТВОЯ ЗАДАЧА:

1. РАЗДЕЛИ транскрипт на реплики менеджера и клиента (с таймкодами).

2. ДВУХОСЕВАЯ КЛАССИФИКАЦИЯ (ОБЕ ОСИ обязательно):
   
   А. Категория по CRM-контексту:
      - входящий новый лид
      - исходящий по новой сделке
      - исходящий по сделке в работе
      - исходящий по действующему клиенту
      - иное
   
   Б. Цель разговора (можно 1-2):
      - первичная консультация
      - допродажа
      - защита коммерческого предложения
      - отработка возражений
      - дожим на оплату
      - согласование следующего шага
      - уточнение информации
      - сервисный/организационный звонок
      - иное

3. КРАТКОЕ РЕЗЮМЕ разговора (2-3 предложения).

4. ИТОГ РАЗГОВОРА — конкретный результат: договорились о N, отказ, перенос, оплата.

5. КЛЮЧЕВЫЕ ЦИТАТЫ КЛИЕНТА (1-2) — то, что раскрывает потребность/возражение.

6. ОЦЕНКА ПО 17 КРИТЕРИЯМ (0-10) ПО МАТРИЦЕ ТЗ:
{criteria_list}

7. ПО КАЖДОМУ КРИТЕРИЮ С ОЦЕНКОЙ <6 — обязательно укажи:
   - проблему
   - цитату из разговора
   - таймкод
   - рекомендацию

8. ТРИГГЕРЫ (выбери из списка ВСЕ, что сработали):
{triggers_list}

9. СИЛЬНЫЕ СТОРОНЫ ЗВОНКА (1-3 пункта) — что менеджер сделал хорошо.

10. СЛАБЫЕ СТОРОНЫ ЗВОНКА (1-3 пункта) — что нужно улучшить.

11. ОСНОВНАЯ ПРОБЛЕМА разговора — главное, что помешало успеху, ОДНОЙ фразой.

12. РЕКОМЕНДАЦИЯ МЕНЕДЖЕРУ (1-2 предложения).

13. ДАТА СЛЕДУЮЩЕГО КОНТАКТА — если в разговоре звучала договорённость о следующей связи, извлеки:
    - дату или срок (например "до 15 июня", "через 2 недели", "после праздников")
    - время если указано
    - кто должен инициировать
    - контекст договорённости
    Если не звучало — поставь null.

14. СОПОСТАВЛЕНИЕ СО СКРИПТАМИ (если скрипты приложены выше):
    Для каждой стадии скрипта (Установление контакта, Квалификация, Презентация и т.д.) укажи:
    - status: "соблюдено" / "частично" / "пропущено"
    - evidence: цитата из транскрипта или объяснение
    - time: таймкод

ОТВЕТ СТРОГО В JSON, БЕЗ ОБЁРТКИ ```json, БЕЗ КОММЕНТАРИЕВ ДО ИЛИ ПОСЛЕ:

{{
  "transcript_split": [
    {{"speaker": "manager|client", "time": "MM:SS", "text": "..."}}
  ],
  "classification": {{
    "crm_context": "входящий новый лид|исходящий по новой сделке|...",
    "goals": ["первичная консультация", "допродажа"],
    "funnel_stage": "квалификация|презентация|возражения|закрытие|..."
  }},
  "summary": "...",
  "outcome": "...",
  "key_quotes": [
    {{"speaker": "client", "time": "MM:SS", "text": "..."}}
  ],
  "scores": {{
{scores_template}
  }},
  "low_score_details": [
    {{"criterion": "...", "score": 4.0, "problem": "...", "quote": "...", "time": "MM:SS", "recommendation": "..."}}
  ],
  "triggers": [
    {{"name": "название из списка", "description": "...", "time": "MM:SS"}}
  ],
  "strengths": ["...", "..."],
  "weaknesses": ["...", "..."],
  "main_problem": "одна фраза",
  "recommendation": "...",
  "next_contact": {{
    "date_or_period": "до 15 июня",
    "time": null,
    "initiator": "менеджер|клиент",
    "context": "..."
  }},{scripts_stages_template}
  "scripts_used": []
}}
"""
    prompt = prompt.replace('"scripts_used": []', f'"scripts_used": {json.dumps([n for n, _ in scripts], ensure_ascii=False)}')
    return prompt


def analyze_transcript(transcription: Dict, call_meta: Dict, scripts_db: Dict, claude_client: Anthropic) -> Dict[str, Any]:
    transcript_text = transcription["text"]
    transcript_tc = transcription.get("text_with_timecodes") or transcript_text
    relevant_scripts = select_relevant_scripts(transcript_text, scripts_db)
    if relevant_scripts:
        logger.info(f"   Скрипты: {', '.join(n for n, _ in relevant_scripts)}")

    prompt = build_analysis_prompt(transcript_tc, call_meta, relevant_scripts)

    response = claude_client.messages.create(
        model=MODEL_CLAUDE,
        max_tokens=10000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    decoder = json.JSONDecoder()
    result, _ = decoder.raw_decode(text)

    # Считаем взвешенную оценку
    weighted = compute_weighted_score(result.get("scores", {}))
    result["overall_score"] = weighted
    result["overall_score_method"] = "weighted_by_tz"

    # Критичность
    is_crit, crit_reason = is_critical(result)
    result["is_critical"] = is_crit
    result["critical_reason"] = crit_reason

    result["_meta"] = {
        "model": MODEL_CLAUDE,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "approx_cost_usd": round(
            response.usage.input_tokens * 1.0 / 1_000_000 +
            response.usage.output_tokens * 5.0 / 1_000_000, 4
        ),
    }
    return result


# ============================================================
# CLI
# ============================================================
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("=" * 60)
    print(f"Анализ (Whisper-{MODEL_WHISPER} + {MODEL_CLAUDE}) — версия ТЗ")
    print("=" * 60)

    audio_dir = Path("audio_temp")
    if not audio_dir.exists():
        print("audio_temp/ не существует")
        return
    audio_files = sorted(audio_dir.glob("*.mp3"))
    if not audio_files:
        print("Нет аудиофайлов")
        return
    print(f"\nНайдено файлов: {len(audio_files)}")

    calls = json.loads(Path("calls_data.json").read_text(encoding="utf-8"))
    scripts_db = load_scripts()
    corrections = load_manual_corrections()
    print(f"Скриптов в базе: {len([k for k in scripts_db if not k.startswith('_')])}")
    print(f"Ручных правок: {len(corrections)}")

    print(f"\nЗагружаем Whisper '{MODEL_WHISPER}'...")
    whisper_model = whisper.load_model(MODEL_WHISPER)
    claude_client = get_claude_client()

    analyses_path = Path("analyses.json")
    if analyses_path.exists():
        analyses = json.loads(analyses_path.read_text(encoding="utf-8"))
    else:
        analyses = {}

    total_cost = 0.0
    success = 0
    failed = 0
    critical_count = 0

    for i, audio_path in enumerate(audio_files, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(audio_files)}] {audio_path.name}")
        print(f"{'='*60}")

        file_id_str = audio_path.name.split("_")[0]
        call_meta = next(
            (c for c in calls if c.get("audio") and str(c["audio"].get("file_id")) == file_id_str),
            None
        )
        if not call_meta:
            print(f"   ⚠ Метаданные не найдены")
            failed += 1
            continue
        activity_id = call_meta["activity_id"]

        if activity_id in analyses:
            print(f"   ⏭ Уже проанализирован, пропускаем")
            continue

        print(f"   Менеджер: {call_meta['manager']['name']}")
        print(f"   Клиент: {call_meta['client']['name']}")

        try:
            transcription = transcribe_audio(audio_path, whisper_model)
            print(f"   Транскрипт: {len(transcription['text'])} симв, {transcription['duration_sec']} сек")
            if len(transcription["text"]) < 50:
                print(f"   ⚠ Слишком короткий, пропускаем")
                failed += 1
                continue

            analysis = analyze_transcript(transcription, call_meta, scripts_db, claude_client)
            
            # Применяем ручную правку если есть
            analysis = apply_manual_corrections(activity_id, analysis, corrections)
            
            cost = analysis["_meta"]["approx_cost_usd"]
            total_cost += cost
            score = analysis.get("overall_score", 0)
            crit_mark = ""
            if analysis.get("is_critical"):
                critical_count += 1
                crit_mark = f" 🔴 КРИТИЧНО ({analysis['critical_reason']})"
            print(f"   ✅ Оценка: {score}/10, стоимость: ${cost:.4f}{crit_mark}")

            analyses[activity_id] = {
                "call_meta": call_meta,
                "transcription": transcription,
                "analysis": analysis,
                "analyzed_at": datetime.now().isoformat(),
            }
            success += 1

            if success % 10 == 0:
                analyses_path.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"   💾 Промежуточное сохранение ({success})")
        except Exception as e:
            print(f"   ❌ {type(e).__name__}: {e}")
            failed += 1

    analyses_path.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}\nИТОГИ\n{'='*60}")
    print(f"   ✅ Успешно: {success}")
    print(f"   ❌ Ошибок: {failed}")
    print(f"   🔴 Критичных: {critical_count}")
    print(f"   💰 Общая стоимость: ${total_cost:.4f}")
    print(f"   📊 Анализов в базе: {len(analyses)}")


if __name__ == "__main__":
    main()
