"""
Тестовый модуль: анализ одного звонка через Claude API.
Транскрибирует, оценивает, формирует рекомендации.
"""

import os
import json
import logging
import base64
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

from anthropic import Anthropic

logger = logging.getLogger(__name__)

# ============================================================
# МОДЕЛЬ И ПРОМПТЫ
# ============================================================
# Используем Sonnet — лучший баланс качества и цены
MODEL = "claude-sonnet-4-5"

# 17 критериев оценки качества звонка
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

ANALYSIS_PROMPT = f"""Ты — эксперт по качеству продаж в отделе строительных материалов компании Mavis Group в Беларуси.

Тебе нужно проанализировать аудиозапись звонка менеджера с клиентом и дать структурированный разбор.

КОНТЕКСТ:
- Компания: Mavis Group, Беларусь, строительные материалы и услуги
- Клиенты: ООО, ИП, частные подрядчики в строительной сфере
- Менеджеры работают по скриптам отдела продаж

ТЕБЕ НУЖНО:

1. **Транскрипт** — разделить разговор на реплики менеджера и клиента, с примерными таймкодами (MM:SS).

2. **Классификация:**
   - Тип звонка (холодный/тёплый/дожим/входящий запрос/презентация КП/отработка возражений/закрытие)
   - Цель звонка (выявить потребность / презентовать / отработать возражение / закрыть сделку / напомнить)
   - Этап воронки

3. **Краткое резюме** (2-3 предложения): что произошло, чем закончилось.

4. **Ключевая цитата клиента** (если есть): фраза, которая раскрывает его боль/возражение/готовность.

5. **Оценка по 17 критериям** — каждый от 0 до 10. Критерии:
{chr(10).join(f"   - {c}" for c in CRITERIA)}

6. **Итоговая оценка** — взвешенная по критериям (0-10).

7. **Триггеры «Требует внимания»** — список конкретных ошибок:
   - Не отработано возражение «дорого»
   - Не зафиксирован следующий шаг
   - Упущена допродажа
   - Не выявлены реальные потребности
   - Скидка дана без переговоров
   - Не предложена встреча/просмотр
   - Прерывал клиента
   - Не использовал имя клиента
   - Не уточнил сроки
   - Не уточнил бюджет
   - Не задал уточняющие вопросы
   - Не предложил альтернативу при отказе
   - Грубость/непрофессионализм
   - Не закрыл звонок резюме договорённостей

8. **Рекомендация менеджеру** (1-2 предложения): что улучшить в следующий раз.

ВЕРНИ ОТВЕТ СТРОГО В ФОРМАТЕ JSON:

{{
  "transcript": [
    {{"speaker": "manager|client", "time": "MM:SS", "text": "..."}},
    ...
  ],
  "classification": {{
    "type": "...",
    "goal": "...",
    "funnel_stage": "..."
  }},
  "summary": "...",
  "key_quote": {{"speaker": "client", "time": "MM:SS", "text": "..."}},
  "scores": {{
    "Приветствие и представление": 8.5,
    "Выявление потребностей клиента": 7.0,
    ...
  }},
  "overall_score": 7.4,
  "triggers": [
    {{"name": "...", "description": "..."}}
  ],
  "recommendation": "..."
}}

ВАЖНО: верни ТОЛЬКО JSON, без какого-либо текста до или после. Без обёрток ```json. Готовый к json.loads().
"""


# ============================================================
# КЛИЕНТ
# ============================================================
def get_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY не задан. "
            "Добавьте в GitHub Secrets."
        )
    return Anthropic(api_key=api_key)


def analyze_call(audio_path: Path, call_meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Анализирует один звонок через Claude API.
    Возвращает структурированный результат.
    """
    client = get_client()

    logger.info(f"Анализируем звонок {call_meta.get('activity_id')}")
    logger.info(f"  Менеджер: {call_meta.get('manager', {}).get('name')}")
    logger.info(f"  Файл: {audio_path.name} ({audio_path.stat().st_size:,} байт)")

    # Читаем аудио в base64
    with open(audio_path, "rb") as f:
        audio_data = base64.standard_b64encode(f.read()).decode("utf-8")

    # Контекст звонка для промпта
    context = (
        f"Информация о звонке:\n"
        f"- Менеджер: {call_meta.get('manager', {}).get('name', 'неизвестно')}\n"
        f"- Клиент: {call_meta.get('client', {}).get('name', 'неизвестно')}\n"
        f"- Компания клиента: {call_meta.get('client', {}).get('company', 'неизвестно')}\n"
        f"- Направление: {'входящий' if call_meta.get('direction') == 'incoming' else 'исходящий'}\n"
        f"- Время: {call_meta.get('created', '')}\n"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "audio/mpeg",
                            "data": audio_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": context + "\n\n" + ANALYSIS_PROMPT,
                    },
                ],
            }
        ],
    )

    # Извлекаем JSON из ответа
    text = response.content[0].text.strip()
    # Иногда модель оборачивает в ```json ... ``` несмотря на просьбу — снимаем
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Не удалось распарсить JSON: {e}")
        logger.error(f"Ответ модели: {text[:500]}...")
        raise

    # Добавляем метаданные о вызове
    result["_meta"] = {
        "model": MODEL,
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
# CLI: тестовый запуск на 1 звонке
# ============================================================
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("=" * 60)
    print("Тест анализа одного звонка через Claude API")
    print("=" * 60)

    # Проверяем что есть скачанные аудио
    audio_dir = Path("audio_temp")
    if not audio_dir.exists() or not list(audio_dir.glob("*.mp3")):
        print("\nОшибка: нет аудиофайлов в audio_temp/")
        print("Сначала запустите src/bitrix.py с DOWNLOAD_AUDIO_COUNT=1")
        return

    # Берём первый файл
    audio_files = sorted(audio_dir.glob("*.mp3"))
    audio_path = audio_files[0]
    print(f"\nВыбран файл: {audio_path.name}")
    print(f"Размер: {audio_path.stat().st_size:,} байт")

    # Читаем JSON с метаданными
    calls_json = Path("calls_data.json")
    if not calls_json.exists():
        print("Ошибка: нет calls_data.json")
        return

    calls = json.loads(calls_json.read_text(encoding="utf-8"))

    # Находим запись для этого файла
    file_id_str = audio_path.name.split("_")[0]
    call_meta = None
    for c in calls:
        if c.get("audio") and str(c["audio"].get("file_id")) == file_id_str:
            call_meta = c
            break

    if not call_meta:
        print(f"Не нашли мета-данные для файла {audio_path.name}")
        return

    print(f"\nМета-данные звонка:")
    print(f"  ID: {call_meta['activity_id']}")
    print(f"  Менеджер: {call_meta['manager']['name']}")
    print(f"  Клиент: {call_meta['client']['name']}")
    print(f"  Компания: {call_meta['client']['company']}")
    print(f"  Направление: {call_meta['direction']}")
    print(f"  Время: {call_meta['created']}")

    print(f"\nОтправляем в Claude API ({MODEL})...")
    print("(это может занять 20-60 секунд)\n")

    result = analyze_call(audio_path, call_meta)

    # Показываем результат
    print("=" * 60)
    print("РЕЗУЛЬТАТ АНАЛИЗА")
    print("=" * 60)

    print(f"\n📋 Классификация:")
    print(f"   Тип: {result['classification']['type']}")
    print(f"   Цель: {result['classification']['goal']}")
    print(f"   Этап воронки: {result['classification']['funnel_stage']}")

    print(f"\n📝 Резюме:")
    print(f"   {result['summary']}")

    if result.get("key_quote"):
        kq = result["key_quote"]
        print(f"\n💬 Ключевая цитата клиента ({kq['time']}):")
        print(f"   «{kq['text']}»")

    print(f"\n📊 Оценки по критериям:")
    for criterion, score in result["scores"].items():
        bar = "█" * int(score) + "░" * (10 - int(score))
        print(f"   {criterion:35} {bar} {score}")

    print(f"\n⭐ ИТОГОВАЯ ОЦЕНКА: {result['overall_score']}/10")

    if result.get("triggers"):
        print(f"\n⚠️  Триггеры «Требует внимания» ({len(result['triggers'])}):")
        for t in result["triggers"]:
            print(f"   • {t['name']}")
            print(f"     {t['description']}")

    print(f"\n💡 Рекомендация менеджеру:")
    print(f"   {result['recommendation']}")

    print(f"\n💰 Стоимость анализа:")
    m = result["_meta"]
    print(f"   Входные токены: {m['input_tokens']:,}")
    print(f"   Выходные токены: {m['output_tokens']:,}")
    print(f"   Примерная цена: ${m['approx_cost_usd']:.4f}")

    # Сохраняем полный JSON
    out_file = Path("analysis_result.json")
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nПолный результат сохранён в {out_file}")
    print(f"\n✅ Тест пройден успешно!")


if __name__ == "__main__":
    main()
