"""
Whisper + Claude: транскрибируем и анализируем ВСЕ аудиофайлы в audio_temp/.
Результаты сохраняются в analyses.json — словарь activity_id → анализ.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List

import whisper
from anthropic import Anthropic

logger = logging.getLogger(__name__)

MODEL_CLAUDE = "claude-sonnet-4-5"
MODEL_WHISPER = "base"

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

Тебе нужно проанализировать транскрипт телефонного разговора менеджера с клиентом.

КОНТЕКСТ:
- Компания: Mavis Group, Беларусь, строительные материалы и услуги
- Клиенты: ООО, ИП, частные подрядчики
- Менеджеры работают по скриптам отдела продаж

ВАЖНО: Транскрипт получен через автоматическое распознавание речи (Whisper).
В нём могут быть искажения слов. Старайся понять смысл, даже если есть ошибки.

ИНФОРМАЦИЯ О ЗВОНКЕ:
{call_info}

ТРАНСКРИПТ:
---
{transcript}
---

ТВОЯ ЗАДАЧА:

1. Раздели транскрипт на реплики менеджера и клиента (по смыслу/тону).
2. Классифицируй: тип звонка / цель / этап воронки.
3. Резюме разговора (2-3 предложения).
4. Ключевая цитата клиента (если есть).
5. Оценка по 17 критериям (0-10):
{criteria_list}
6. Итоговая взвешенная оценка (0-10).
7. Триггеры «Требует внимания» — конкретные ошибки.
8. Рекомендация менеджеру (1-2 предложения).

ОТВЕТ СТРОГО В JSON, БЕЗ ОБЁРТКИ ```json:

{{
  "transcript_split": [
    {{"speaker": "manager|client", "text": "..."}}
  ],
  "classification": {{"type": "...", "goal": "...", "funnel_stage": "..."}},
  "summary": "...",
  "key_quote": {{"speaker": "client", "text": "..."}},
  "scores": {{
{scores_template}
  }},
  "overall_score": 7.0,
  "triggers": [{{"name": "...", "description": "..."}}],
  "recommendation": "..."
}}
"""


def transcribe_audio(audio_path: Path, model) -> Dict[str, Any]:
    logger.info(f"Транскрибируем {audio_path.name}...")
    result = model.transcribe(str(audio_path), language="ru", verbose=False, fp16=False)
    full_text = result["text"].strip()
    duration_sec = 0
    if result.get("segments"):
        duration_sec = result["segments"][-1].get("end", 0)
    return {
        "text": full_text,
        "duration_sec": round(duration_sec, 1),
    }


def get_claude_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY не задан")
    return Anthropic(api_key=api_key)


def analyze_transcript(transcript: str, call_meta: Dict[str, Any], claude_client: Anthropic) -> Dict[str, Any]:
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
        call_info=call_info, transcript=transcript,
        criteria_list=criteria_list, scores_template=scores_template,
    )

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

    result = json.loads(text)
    result["_meta"] = {
        "model": MODEL_CLAUDE,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "approx_cost_usd": round(
            response.usage.input_tokens * 0.003 / 1000 +
            response.usage.output_tokens * 0.015 / 1000, 4
        ),
    }
    return result


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("=" * 60)
    print("Анализ всех аудио в audio_temp/")
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
    for f in audio_files:
        print(f"   • {f.name} ({f.stat().st_size:,} байт)")

    # Метаданные
    calls = json.loads(Path("calls_data.json").read_text(encoding="utf-8"))

    # Загружаем модели один раз
    print(f"\nЗагружаем модель Whisper '{MODEL_WHISPER}'...")
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
        print(f"   Менеджер: {call_meta['manager']['name']}")
        print(f"   Клиент: {call_meta['client']['name']}")

        try:
            # Транскрибация
            transcription = transcribe_audio(audio_path, whisper_model)
            print(f"   Транскрипт: {len(transcription['text'])} символов, {transcription['duration_sec']} сек")

            if len(transcription["text"]) < 50:
                print(f"   ⚠ Слишком короткий транскрипт, пропускаем")
                failed += 1
                continue

            # Анализ
            print(f"   Анализ через Claude...")
            analysis = analyze_transcript(transcription["text"], call_meta, claude_client)
            cost = analysis["_meta"]["approx_cost_usd"]
            total_cost += cost
            score = analysis.get("overall_score", 0)
            print(f"   ✅ Оценка: {score}/10, стоимость: ${cost:.4f}")

            analyses[activity_id] = {
                "call_meta": call_meta,
                "transcription": transcription,
                "analysis": analysis,
                "analyzed_at": __import__("datetime").datetime.now().isoformat(),
            }
            success += 1

        except Exception as e:
            print(f"   ❌ Ошибка: {type(e).__name__}: {e}")
            failed += 1

    # Сохраняем
    analyses_path.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"ИТОГИ")
    print(f"{'='*60}")
    print(f"   ✅ Успешно: {success}")
    print(f"   ❌ Ошибок: {failed}")
    print(f"   💰 Общая стоимость: ${total_cost:.4f}")
    print(f"   📊 Анализов в базе: {len(analyses)}")
    print(f"   💾 Сохранено в: {analyses_path}")


if __name__ == "__main__":
    main()
