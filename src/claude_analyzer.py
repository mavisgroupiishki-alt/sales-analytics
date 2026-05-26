"""
Анализ одного звонка:
1. Whisper транскрибирует аудио → текст
2. Claude анализирует текст → структурированный разбор
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any

import whisper
from anthropic import Anthropic

logger = logging.getLogger(__name__)


# ============================================================
# КОНСТАНТЫ
# ============================================================
MODEL_CLAUDE = "claude-sonnet-4-5"
MODEL_WHISPER = "base"  # tiny / base / small / medium / large

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

ANALYSIS_PROMPT_TEMPLATE = """Ты — эксперт по качеству продаж в отделе строительных материалов компании Mavis Group в Беларуси.

Тебе нужно проанализировать транскрипт телефонного разговора менеджера с клиентом и дать структурированный разбор.

КОНТЕКСТ:
- Компания: Mavis Group, Беларусь, строительные материалы и услуги
- Клиенты: ООО, ИП, частные подрядчики
- Менеджеры работают по скриптам отдела продаж

ВАЖНО: Транскрипт получен через автоматическое распознавание речи (Whisper). 
В нём могут быть искажения слов, особенно технических терминов и имён. 
Старайся понять смысл, даже если есть мелкие ошибки в словах.

ИНФОРМАЦИЯ О ЗВОНКЕ:
{call_info}

ТРАНСКРИПТ (одним потоком, без разделения на спикеров):
---
{transcript}
---

ТВОЯ ЗАДАЧА:

1. Раздели транскрипт на реплики менеджера и клиента (по смыслу/тону).

2. Классифицируй:
   - Тип звонка (холодный/тёплый/дожим/входящий запрос/презентация КП/отработка возражений/закрытие)
   - Цель звонка
   - Этап воронки

3. Резюме разговора (2-3 предложения).

4. Ключевая цитата клиента (если есть).

5. Оценка по 17 критериям (0-10):
{criteria_list}

6. Итоговая взвешенная оценка (0-10).

7. Триггеры «Требует внимания» — конкретные ошибки:
   - Не отработано возражение «дорого»
   - Не зафиксирован следующий шаг
   - Упущена допродажа
   - Не выявлены потребности
   - Скидка дана без переговоров
   - Не предложена встреча
   - Прерывал клиента
   - Не использовал имя клиента
   - Не уточнил сроки
   - Не уточнил бюджет
   - Не задал уточняющие вопросы
   - Не предложил альтернативу
   - Грубость/непрофессионализм
   - Не закрыл звонок резюме

8. Рекомендация менеджеру (1-2 предложения).

ОТВЕТ СТРОГО В JSON, БЕЗ ОБЁРТКИ ```json:

{{
  "transcript_split": [
    {{"speaker": "manager|client", "text": "..."}}
  ],
  "classification": {{
    "type": "...",
    "goal": "...",
    "funnel_stage": "..."
  }},
  "summary": "...",
  "key_quote": {{"speaker": "client", "text": "..."}},
  "scores": {{
{scores_template}
  }},
  "overall_score": 7.0,
  "triggers": [
    {{"name": "...", "description": "..."}}
  ],
  "recommendation": "..."
}}
"""


# ============================================================
# ТРАНСКРИБАЦИЯ (Whisper)
# ============================================================
def transcribe_audio(audio_path: Path) -> Dict[str, Any]:
    """Транскрибирует аудио через локальную модель Whisper."""
    logger.info(f"Загружаем модель Whisper '{MODEL_WHISPER}'...")
    model = whisper.load_model(MODEL_WHISPER)

    logger.info(f"Транскрибируем {audio_path.name}...")
    result = model.transcribe(
        str(audio_path),
        language="ru",
        verbose=False,
        fp16=False,
    )

    full_text = result["text"].strip()
    duration_sec = 0
    if result.get("segments"):
        duration_sec = result["segments"][-1].get("end", 0)

    logger.info(f"Получено {len(full_text)} символов, {len(result.get('segments', []))} сегментов")

    return {
        "text": full_text,
        "segments": result.get("segments", []),
        "duration_sec": round(duration_sec, 1),
        "language": result.get("language", "ru"),
    }


# ============================================================
# АНАЛИЗ (Claude)
# ============================================================
def get_claude_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY не задан в окружении")
    return Anthropic(api_key=api_key)


def analyze_transcript(transcript: str, call_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Анализирует транскрипт через Claude."""
    client = get_claude_client()

    call_info = (
        f"- Менеджер: {call_meta.get('manager', {}).get('name', 'неизвестно')}\n"
        f"- Клиент: {call_meta.get('client', {}).get('name', 'неизвестно')}\n"
        f"- Компания: {call_meta.get('client', {}).get('company', 'неизвестно')}\n"
        f"- Направление: {'входящий' if call_meta.get('direction') == 'incoming' else 'исходящий'}\n"
        f"- Время: {call_meta.get('created', '')}\n"
    )

    criteria_list = "\n".join(f"   - {c}" for c in CRITERIA)
    scores_template = ",\n".join(f'    "{c}": 0.0' for c in CRITERIA)

    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        call_info=call_info,
        transcript=transcript,
        criteria_list=criteria_list,
        scores_template=scores_template,
    )

    logger.info("Отправляем в Claude API...")
    response = client.messages.create(
        model=MODEL_CLAUDE,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # Убираем обёртку ```json если есть
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Не удалось распарсить JSON Claude: {e}")
        logger.error(f"Ответ модели:\n{text[:1000]}...")
        raise

    result["_meta"] = {
        "model": MODEL_CLAUDE,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "approx_cost_usd": round(
            response.usage.input_tokens * 0.003 / 1000 +
            response.usage.output_tokens * 0.015 / 1000,
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
    print("Тест: Whisper + Claude на одном звонке")
    print("=" * 60)

    # Берём первый mp3 из audio_temp
    audio_dir = Path("audio_temp")
    if not audio_dir.exists() or not list(audio_dir.glob("*.mp3")):
        print("Ошибка: нет аудиофайлов в audio_temp/")
        return

    audio_files = sorted(audio_dir.glob("*.mp3"))
    audio_path = audio_files[0]
    print(f"\nФайл: {audio_path.name}")
    print(f"Размер: {audio_path.stat().st_size:,} байт")

    # Метаданные
    calls = json.loads(Path("calls_data.json").read_text(encoding="utf-8"))
    file_id_str = audio_path.name.split("_")[0]
    call_meta = next((c for c in calls if c.get("audio") and str(c["audio"].get("file_id")) == file_id_str), None)

    if not call_meta:
        print(f"Не нашли мета-данные для {audio_path.name}")
        return

    print(f"\nМенеджер: {call_meta['manager']['name']}")
    print(f"Клиент: {call_meta['client']['name']}")
    print(f"Компания: {call_meta['client']['company']}")
    print(f"Направление: {call_meta['direction']}")

    # ============================================================
    # ЭТАП 1: ТРАНСКРИБАЦИЯ
    # ============================================================
    print("\n" + "=" * 60)
    print(f"ЭТАП 1: ТРАНСКРИБАЦИЯ (Whisper-{MODEL_WHISPER})")
    print("=" * 60)
    print("(загрузка модели ~150MB + распознавание...)")

    transcription = transcribe_audio(audio_path)

    print(f"\nДлительность: {transcription['duration_sec']} сек")
    print(f"\nТранскрипт:")
    print("-" * 60)
    print(transcription["text"])
    print("-" * 60)

    if not transcription["text"] or len(transcription["text"]) < 20:
        print("\n⚠️ Слишком короткий или пустой транскрипт — анализ нецелесообразен")
        return

    # ============================================================
    # ЭТАП 2: АНАЛИЗ
    # ============================================================
    print("\n" + "=" * 60)
    print(f"ЭТАП 2: АНАЛИЗ ({MODEL_CLAUDE})")
    print("=" * 60)

    result = analyze_transcript(transcription["text"], call_meta)

    # ============================================================
    # ВЫВОД
    # ============================================================
    print("\n" + "=" * 60)
    print("РЕЗУЛЬТАТ АНАЛИЗА")
    print("=" * 60)

    cls = result.get("classification", {})
    print(f"\n📋 Классификация:")
    print(f"   Тип: {cls.get('type', '—')}")
    print(f"   Цель: {cls.get('goal', '—')}")
    print(f"   Этап воронки: {cls.get('funnel_stage', '—')}")

    print(f"\n📝 Резюме:")
    print(f"   {result.get('summary', '—')}")

    kq = result.get("key_quote")
    if kq:
        print(f"\n💬 Ключевая цитата клиента:")
        print(f"   «{kq.get('text', '')}»")

    print(f"\n📊 Оценки по критериям:")
    for criterion, score in (result.get("scores") or {}).items():
        try:
            score_int = int(round(float(score)))
        except (TypeError, ValueError):
            score_int = 0
        bar = "█" * score_int + "░" * (10 - score_int)
        print(f"   {criterion:35} {bar} {score}")

    print(f"\n⭐ ИТОГОВАЯ ОЦЕНКА: {result.get('overall_score', '—')}/10")

    triggers = result.get("triggers") or []
    if triggers:
        print(f"\n⚠️  Триггеры «Требует внимания» ({len(triggers)}):")
        for t in triggers:
            print(f"   • {t.get('name', '—')}")
            if t.get("description"):
                print(f"     {t['description']}")
    else:
        print("\n✅ Триггеры не сработали — звонок без критичных ошибок")

    print(f"\n💡 Рекомендация менеджеру:")
    print(f"   {result.get('recommendation', '—')}")

    print(f"\n💰 Стоимость анализа:")
    m = result.get("_meta", {})
    print(f"   Входные токены Claude: {m.get('input_tokens', 0):,}")
    print(f"   Выходные токены Claude: {m.get('output_tokens', 0):,}")
    print(f"   Whisper (локально): $0.0000")
    print(f"   ИТОГО: ${m.get('approx_cost_usd', 0):.4f}")

    # Сохраняем
    out_data = {
        "call_meta": call_meta,
        "transcription": transcription,
        "analysis": result,
    }
    Path("analysis_result.json").write_text(
        json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✅ Полный результат сохранён в analysis_result.json")


if __name__ == "__main__":
    main()
