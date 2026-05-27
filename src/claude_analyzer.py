"""
Анализ звонков через Whisper + Claude Haiku.

Что делает:
1. Транскрибирует аудио через Whisper-base (бесплатно)
2. Выбирает релевантные скрипты из scripts.json по ключевым словам
3. Анализирует через Claude Haiku с учётом скриптов
4. Сохраняет в analyses.json с цитатами и таймкодами
5. Помечает критичные звонки

Триггеры — список ниже, можно редактировать вручную через GitHub.
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

# ============================================================
# МОДЕЛИ
# ============================================================
MODEL_CLAUDE = "claude-haiku-4-5-20251001"  # Haiku — в 5 раз дешевле Sonnet
MODEL_WHISPER = "base"

# ============================================================
# КРИТЕРИИ ОЦЕНКИ (17 шт)
# ============================================================
CRITERIA = [
    "Приветствие и представление",
    "Выявление потребностей клиента",
    "Презентация ценности продукта",
    "Работа с возражениями",
    "Управление разговором",
    "Активное слушание",
    "Эмоциональный тон",
    "Структурированность разговора",
    "Использование скрипта компании",
    "Уточняющие вопросы",
    "Допродажа / cross-sell",
    "Фиксация следующего шага",
    "Прояснение бюджета",
    "Проработка сроков",
    "Резюмирование договорённостей",
    "Завершение разговора",
    "Профессиональная речь",
]

# ============================================================
# ТРИГГЕРЫ — РЕДАКТИРУЙТЕ ЗДЕСЬ (добавьте/уберите строки)
# ============================================================
TRIGGERS = [
    "Не отработано возражение «дорого»",
    "Не зафиксирован следующий шаг",
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
]

# ============================================================
# КРИТЕРИИ КРИТИЧНОГО ЗВОНКА
# ============================================================
CRITICAL_TRIGGER_RUDENESS = "Грубость или непрофессионализм"
CRITICAL_SCORE_THRESHOLD = 5.0
CRITICAL_TRIGGERS_COUNT = 2


def is_critical(analysis: Dict[str, Any]) -> Tuple[bool, str]:
    """Определяет, критичный ли звонок. Возвращает (флаг, причина)."""
    score = analysis.get("overall_score", 10)
    triggers = analysis.get("triggers", []) or []
    trigger_names = [t.get("name", "") for t in triggers]

    # Грубость — всегда критично
    if any(CRITICAL_TRIGGER_RUDENESS.lower() in n.lower() for n in trigger_names):
        return True, "Грубость в разговоре"

    # Оценка < 5 — критично
    try:
        if float(score) < CRITICAL_SCORE_THRESHOLD:
            return True, f"Низкая оценка ({score}/10)"
    except (TypeError, ValueError):
        pass

    # 2+ триггера — критично
    if len(triggers) >= CRITICAL_TRIGGERS_COUNT:
        return True, f"{len(triggers)} триггеров"

    return False, ""


# ============================================================
# ЗАГРУЗКА СКРИПТОВ И УМНАЯ ПОДГРУЗКА
# ============================================================
def load_scripts() -> Dict[str, Any]:
    """Читает scripts.json. Если файла нет — возвращает пустой dict."""
    p = Path("scripts.json")
    if not p.exists():
        logger.warning("scripts.json не найден, работаем без скриптов компании")
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def select_relevant_scripts(transcript: str, scripts: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Выбирает релевантные скрипты по ключевым словам в транскрипте.
    Всегда подгружает базовые ("Чек-лист" + "Возражения").
    Возвращает список (название, текст).
    """
    if not scripts:
        return []

    transcript_lower = transcript.lower()
    selected = []
    selected_names = set()

    # Всегда подгружаем базовые
    always = scripts.get("_always_load", [])
    for name in always:
        if name in scripts and isinstance(scripts[name], dict):
            selected.append((name, scripts[name]["text"]))
            selected_names.add(name)

    # Подгружаем по ключевым словам
    for name, data in scripts.items():
        if name.startswith("_") or name in selected_names:
            continue
        if not isinstance(data, dict):
            continue
        keywords = data.get("keywords", [])
        for kw in keywords:
            if kw.lower() in transcript_lower:
                selected.append((name, data["text"]))
                selected_names.add(name)
                break

    return selected


# ============================================================
# ТРАНСКРИБАЦИЯ С ТАЙМКОДАМИ
# ============================================================
def transcribe_audio(audio_path: Path, model) -> Dict[str, Any]:
    """Транскрибирует с таймкодами в сегментах."""
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

    # Также делаем текст с таймкодами (для Claude)
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
    """Форматирует секунды в MM:SS."""
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


# ============================================================
# КЛАУД АНАЛИЗ
# ============================================================
def get_claude_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY не задан")
    return Anthropic(api_key=api_key)


def build_analysis_prompt(transcript_with_timecodes: str, call_meta: Dict, scripts: List[Tuple[str, str]]) -> str:
    """Собирает промпт с контекстом, скриптами и инструкциями."""

    call_info = (
        f"- Менеджер: {call_meta.get('manager', {}).get('name', 'неизвестно')}\n"
        f"- Клиент: {call_meta.get('client', {}).get('name', 'неизвестно')}\n"
        f"- Компания клиента: {call_meta.get('client', {}).get('company', 'неизвестно')}\n"
        f"- Направление: {'входящий' if call_meta.get('direction') == 'incoming' else 'исходящий'}\n"
        f"- Время: {call_meta.get('created', '')}\n"
    )

    criteria_list = "\n".join(f"   - {c}" for c in CRITERIA)
    scores_template = ",\n".join(f'    "{c}": 0.0' for c in CRITERIA)
    triggers_list = "\n".join(f"   - {t}" for t in TRIGGERS)

    scripts_block = ""
    if scripts:
        scripts_block = "\n\nСКРИПТЫ КОМПАНИИ MAVIS GROUP (релевантные данному звонку):\n"
        for name, text in scripts:
            scripts_block += f"\n=== {name} ===\n{text}\n"
        scripts_block += "\n"

    prompt = f"""Ты — эксперт по качеству продаж в отделе строительных материалов компании Mavis Group в Беларуси.

Тебе нужно проанализировать транскрипт телефонного разговора менеджера с клиентом, сравнить с корпоративными скриптами и дать структурированный разбор.

КОНТЕКСТ:
- Компания: Mavis Group, Беларусь, СРО, ISO, ГОСТ, СПК, аттестация специалистов в строительстве
- Клиенты: ООО, ИП, частные подрядчики

ВАЖНО: Транскрипт получен через распознавание речи (Whisper). 
В нём могут быть искажения слов. Понимай смысл, не цепляйся за точные слова.
В транскрипте указаны таймкоды [MM:SS] — используй их в цитатах.

ИНФОРМАЦИЯ О ЗВОНКЕ:
{call_info}
{scripts_block}
ТРАНСКРИПТ С ТАЙМКОДАМИ:
---
{transcript_with_timecodes}
---

ТВОЯ ЗАДАЧА:

1. Раздели транскрипт на реплики менеджера и клиента (по смыслу).

2. Классифицируй:
   - Тип звонка (холодный/тёплый/дожим/входящий/презентация КП/закрытие/отработка возражений)
   - Цель звонка (что хотел менеджер достичь)
   - Этап воронки (квалификация/презентация/возражения/закрытие)

3. Резюме разговора (2-3 предложения).

4. 1-2 ключевые цитаты клиента с таймкодами (то, что раскрывает его потребность/возражение).

5. Оценка по 17 критериям (0-10):
{criteria_list}

   ВАЖНО для критерия "Использование скрипта компании": если скрипты приложены выше, 
   оцени именно соответствие речи менеджера этим скриптам. Если скриптов нет — поставь 5.0.

6. Итоговая взвешенная оценка (0-10).

7. Триггеры из списка ниже — отметь ВСЕ, что сработали в этом звонке:
{triggers_list}

   Для каждого триггера укажи name (точное название из списка) и description (1 предложение что именно произошло, с цитатой если есть).

8. Рекомендация менеджеру (1-2 предложения). Если использовались скрипты — упомяни, что соответствовало/не соответствовало им.

ОТВЕТ СТРОГО В JSON, БЕЗ ОБЁРТКИ ```json:

{{
  "transcript_split": [
    {{"speaker": "manager|client", "time": "MM:SS", "text": "..."}}
  ],
  "classification": {{"type": "...", "goal": "...", "funnel_stage": "..."}},
  "summary": "...",
  "key_quotes": [
    {{"speaker": "client", "time": "MM:SS", "text": "..."}}
  ],
  "scores": {{
{scores_template}
  }},
  "overall_score": 7.0,
  "triggers": [
    {{"name": "название из списка", "description": "...", "time": "MM:SS"}}
  ],
  "recommendation": "...",
  "scripts_used": []
}}
"""
    # Дополняем список использованных скриптов
    prompt = prompt.replace('"scripts_used": []', f'"scripts_used": {json.dumps([n for n, _ in scripts], ensure_ascii=False)}')
    return prompt


def analyze_transcript(transcription: Dict, call_meta: Dict, scripts_db: Dict, claude_client: Anthropic) -> Dict[str, Any]:
    """Один полный цикл анализа звонка."""
    transcript_text = transcription["text"]
    transcript_tc = transcription.get("text_with_timecodes") or transcript_text

    # Выбираем релевантные скрипты
    relevant_scripts = select_relevant_scripts(transcript_text, scripts_db)
    if relevant_scripts:
        logger.info(f"   Скрипты: {', '.join(n for n, _ in relevant_scripts)}")

    # Строим промпт
    prompt = build_analysis_prompt(transcript_tc, call_meta, relevant_scripts)

    # Запрос к Claude
    response = claude_client.messages.create(
        model=MODEL_CLAUDE,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    # Парсим только первый JSON-объект (Haiku иногда добавляет текст после)
    decoder = json.JSONDecoder()
    result, _ = decoder.raw_decode(text)

    # Определяем критичность
    is_crit, crit_reason = is_critical(result)
    result["is_critical"] = is_crit
    result["critical_reason"] = crit_reason

    # Цена Haiku: input $1/1M токенов, output $5/1M токенов
    result["_meta"] = {
        "model": MODEL_CLAUDE,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "approx_cost_usd": round(
            response.usage.input_tokens * 1.0 / 1_000_000 +
            response.usage.output_tokens * 5.0 / 1_000_000,
            4
        ),
    }
    return result


# ============================================================
# CLI
# ============================================================
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("=" * 60)
    print(f"Анализ звонков (Whisper-{MODEL_WHISPER} + {MODEL_CLAUDE})")
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

    # Загружаем данные
    calls = json.loads(Path("calls_data.json").read_text(encoding="utf-8"))
    scripts_db = load_scripts()
    print(f"Скриптов в базе: {len([k for k in scripts_db if not k.startswith('_')])}")

    # Whisper и Claude
    print(f"\nЗагружаем Whisper '{MODEL_WHISPER}'...")
    whisper_model = whisper.load_model(MODEL_WHISPER)
    claude_client = get_claude_client()

    # Существующие анализы
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
            print(f"   ⚠ Метаданные не найдены, пропускаем")
            failed += 1
            continue

        activity_id = call_meta["activity_id"]

        # Если уже анализировали — пропускаем (можно поменять, если нужно перезаписать)
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
            cost = analysis["_meta"]["approx_cost_usd"]
            total_cost += cost
            score = analysis.get("overall_score", 0)

            critical_mark = ""
            if analysis.get("is_critical"):
                critical_count += 1
                critical_mark = f" 🔴 КРИТИЧНО ({analysis['critical_reason']})"

            print(f"   ✅ Оценка: {score}/10, стоимость: ${cost:.4f}{critical_mark}")

            analyses[activity_id] = {
                "call_meta": call_meta,
                "transcription": transcription,
                "analysis": analysis,
                "analyzed_at": datetime.now().isoformat(),
            }
            success += 1

            # Сохраняем каждые 10 анализов (на случай сбоя)
            if success % 10 == 0:
                analyses_path.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"   💾 Промежуточное сохранение ({success} анализов)")

        except Exception as e:
            print(f"   ❌ {type(e).__name__}: {e}")
            failed += 1

    # Финальное сохранение
    analyses_path.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"ИТОГИ")
    print(f"{'='*60}")
    print(f"   ✅ Успешно: {success}")
    print(f"   ❌ Ошибок: {failed}")
    print(f"   🔴 Критичных: {critical_count}")
    print(f"   💰 Общая стоимость: ${total_cost:.4f}")
    print(f"   📊 Анализов в базе: {len(analyses)}")
    print(f"   💾 Сохранено: {analyses_path}")


if __name__ == "__main__":
    main()
