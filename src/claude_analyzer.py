"""
Анализ звонков через Whisper + Claude API (HTTP) — версия с типами звонков.

Изменения v2:
- Убрана зависимость от anthropic SDK → чистые HTTP-запросы (совместимо с любой средой)
- 11 типов звонков по классификации из ТЗ
- Для каждого типа — свой эталонный сценарий и критерии успеха
- Двухэтапный анализ: 1) определить тип → 2) оценить по эталону для этого типа
- Совместимость с Bitrix Vibe Code (NODE_ENV/webhook среда)
"""

import os
import json
import logging
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

logger = logging.getLogger(__name__)

MODEL_CLAUDE = "bitrix/bitrixgpt-5.5"   # бесплатная модель Vibe Code; замени на "auto" для автовыбора
MIN_DURATION_FOR_ANALYSIS = 30  # звонки короче этого порога только транскрибируются, без ИИ-анализа
MODEL_WHISPER = "base"
VIBE_AI_URL = "https://vibecode.bitrix24.tech/v1/ai/chat/completions"

# ============================================================
# 11 ТИПОВ ЗВОНКОВ
# ============================================================
CALL_TYPES = {
    "primary_incoming_new": {
        "label": "Первичный входящий (новый клиент)",
        "description": "Клиент позвонил сам первый раз, ранее не работал с компанией",
        "keywords": ["заявку", "сайта", "нашли", "посоветовали", "первый раз"],
        "crm_context": "входящий новый лид",
        "stages": [
            "Приветствие и установление контакта",
            "Программирование разговора (задам несколько вопросов)",
            "Выявление потребности (виды работ, рынок, сроки, тендеры)",
            "Выявление боли и мотива",
            "Резюмирование потребности",
            "Презентация решения через пользу",
            "Допродажа (ISO, кадры, смежные продукты)",
            "Работа с вопросами и возражениями",
            "Попытка закрытия / назначение следующего шага",
            "Фиксация договорённости и даты обратной связи",
            "Упоминание реферальной программы / Telegram-канала",
        ],
        "critical_stages": [
            "Выявление потребности (виды работ, рынок, сроки, тендеры)",
            "Резюмирование потребности",
            "Попытка закрытия / назначение следующего шага",
            "Фиксация договорённости и даты обратной связи",
        ],
        "success_criteria": "КП отправлено, назначен следующий звонок, зафиксирована дата",
    },
    "primary_incoming_existing": {
        "label": "Первичный входящий (действующий клиент)",
        "description": "Клиент позвонил сам, ранее уже работал с компанией",
        "keywords": ["снова", "опять", "уже работали", "в прошлый раз", "продлить"],
        "crm_context": "исходящий по действующему клиенту",
        "stages": [
            "Приветствие и узнавание клиента",
            "Уточнение текущей ситуации / статуса предыдущего проекта",
            "Выявление новой потребности",
            "Презентация следующего шага / нового продукта",
            "Допродажа смежных услуг",
            "Закрытие и фиксация договорённости",
        ],
        "critical_stages": [
            "Уточнение текущей ситуации / статуса предыдущего проекта",
            "Выявление новой потребности",
            "Закрытие и фиксация договорённости",
        ],
        "success_criteria": "Назначен следующий шаг, клиент подтвердил интерес к продолжению",
    },
    "cold_new": {
        "label": "Первичный холодный (новый клиент)",
        "description": "Менеджер позвонил первым, клиент не знает компанию",
        "keywords": ["хотел бы предложить", "нашёл вашу компанию", "мы занимаемся", "холодный"],
        "crm_context": "исходящий по новой сделке",
        "stages": [
            "Приветствие и представление компании",
            "Зацепка / причина звонка (актуальность)",
            "Квалификация клиента (ЛПР? есть потребность?)",
            "Выявление потребности и боли",
            "Краткая презентация ценности (не продавать — заинтересовать)",
            "Назначение следующего шага (встреча, КП, повторный звонок)",
            "Фиксация договорённости",
        ],
        "critical_stages": [
            "Зацепка / причина звонка (актуальность)",
            "Квалификация клиента (ЛПР? есть потребность?)",
            "Назначение следующего шага (встреча, КП, повторный звонок)",
        ],
        "success_criteria": "Клиент согласился на следующий шаг (КП, звонок, встреча)",
    },
    "cold_periodika": {
        "label": "Первичный холодный (периодика, действующий клиент)",
        "description": "Звонок действующему клиенту по поводу периодического продления/обновления",
        "keywords": ["периодика", "продление", "срок действия", "истекает", "обновить аттестат"],
        "crm_context": "исходящий по действующему клиенту",
        "stages": [
            "Приветствие и контекст (мы работали по X, срок истекает)",
            "Напоминание о ценности предыдущей работы",
            "Предложение продления / переоформления",
            "Выявление изменений (новые сотрудники, новые виды работ)",
            "Допродажа смежных продуктов",
            "Закрытие и фиксация даты",
        ],
        "critical_stages": [
            "Предложение продления / переоформления",
            "Выявление изменений (новые сотрудники, новые виды работ)",
            "Закрытие и фиксация даты",
        ],
        "success_criteria": "Клиент согласился продлить, назначена дата/оплата",
    },
    "cold_reactivation": {
        "label": "Первичный холодный (давно не было контакта, через пользу)",
        "description": "Реактивация клиента, с которым давно не было контакта — звонок через пользу/новость",
        "keywords": ["давно не общались", "давно не виделись", "хотел поделиться", "появилась информация"],
        "crm_context": "исходящий по действующему клиенту",
        "stages": [
            "Приветствие и напоминание о себе (кто звонит)",
            "Зацепка через пользу / актуальную новость / изменение в законодательстве",
            "Выяснение текущей ситуации клиента",
            "Привязка пользы к его ситуации",
            "Предложение конкретного следующего шага",
            "Фиксация договорённости",
        ],
        "critical_stages": [
            "Зацепка через пользу / актуальную новость / изменение в законодательстве",
            "Выяснение текущей ситуации клиента",
            "Предложение конкретного следующего шага",
        ],
        "success_criteria": "Клиент вовлёкся в разговор, назначен следующий шаг",
    },
    "kp_defense": {
        "label": "Защита КП",
        "description": "Звонок после отправки коммерческого предложения — обсуждение, защита цены и условий",
        "keywords": ["посмотрели", "получили КП", "коммерческое", "цена", "стоимость", "дорого"],
        "crm_context": "исходящий по сделке в работе",
        "stages": [
            "Уточнение — успел ли клиент посмотреть КП",
            "Сбор обратной связи по КП (что понравилось, что вопросы)",
            "Презентация ценности (не цены — результата)",
            "Отработка возражений по цене или условиям",
            "Сравнение с альтернативами в пользу компании",
            "Попытка закрытия или назначение чёткого следующего шага",
            "Фиксация даты решения",
        ],
        "critical_stages": [
            "Сбор обратной связи по КП (что понравилось, что вопросы)",
            "Презентация ценности (не цены — результата)",
            "Попытка закрытия или назначение чёткого следующего шага",
        ],
        "success_criteria": "Клиент движется к решению: назначена дата оплаты или следующего контакта",
    },
    "kp_feedback": {
        "label": "Обратная связь по КП",
        "description": "Звонок для получения обратной связи по отправленному КП",
        "keywords": ["обратная связь", "как вам", "что думаете", "посмотрели предложение"],
        "crm_context": "исходящий по сделке в работе",
        "stages": [
            "Уточнение — видел ли клиент КП",
            "Открытый вопрос: что думает, какие вопросы",
            "Уточнение возражений и сомнений",
            "Обработка возражений",
            "Продвижение к решению",
            "Фиксация следующего шага",
        ],
        "critical_stages": [
            "Открытый вопрос: что думает, какие вопросы",
            "Продвижение к решению",
            "Фиксация следующего шага",
        ],
        "success_criteria": "Понятна причина промедления, назначен следующий шаг",
    },
    "counteroffer": {
        "label": "Контроффер (особое предложение для клиента)",
        "description": "Звонок с индивидуальным предложением — скидка, бонус, специальные условия",
        "keywords": ["специальное предложение", "только для вас", "скидка", "особые условия", "акция"],
        "crm_context": "исходящий по сделке в работе",
        "stages": [
            "Приветствие и причина звонка (есть хорошая новость)",
            "Презентация особого предложения как ценности, а не уступки",
            "Ограничение по времени или условию",
            "Обработка реакции клиента",
            "Закрытие или фиксация решения",
        ],
        "critical_stages": [
            "Презентация особого предложения как ценности, а не уступки",
            "Ограничение по времени или условию",
            "Закрытие или фиксация решения",
        ],
        "success_criteria": "Клиент принял предложение или назначена чёткая дата ответа",
    },
    "objection_handling": {
        "label": "Отработка возражений",
        "description": "Звонок с целью снять возражения клиента (дорого, не сейчас, думаю, и т.д.)",
        "keywords": ["возражение", "дорого", "подумаю", "не сейчас", "посоветуюсь", "не уверен"],
        "crm_context": "исходящий по сделке в работе",
        "stages": [
            "Присоединение к возражению (не спорить)",
            "Уточнение истинной причины возражения",
            "Работа с возражением через пользу или аргумент",
            "Проверка — снято ли возражение",
            "Продвижение к следующему шагу",
            "Фиксация договорённости",
        ],
        "critical_stages": [
            "Уточнение истинной причины возражения",
            "Работа с возражением через пользу или аргумент",
            "Продвижение к следующему шагу",
        ],
        "success_criteria": "Возражение снято или минимизировано, назначен следующий шаг",
    },
    "payment_push": {
        "label": "Дожим клиента на оплату (через пользу или по итогам договорённостей)",
        "description": "Звонок для получения оплаты — клиент должен был заплатить но не заплатил",
        "keywords": ["оплата", "счёт", "оплатить", "перевести", "деньги", "дожим"],
        "crm_context": "исходящий по сделке в работе",
        "stages": [
            "Напоминание о договорённости (не обвинять)",
            "Уточнение — что мешает оплатить",
            "Снятие последнего возражения или препятствия",
            "Напоминание о пользе / срочности (ограничение по времени)",
            "Конкретный вопрос: когда будет оплата",
            "Фиксация точной даты/времени оплаты",
        ],
        "critical_stages": [
            "Уточнение — что мешает оплатить",
            "Конкретный вопрос: когда будет оплата",
            "Фиксация точной даты/времени оплаты",
        ],
        "success_criteria": "Клиент назвал конкретную дату оплаты или оплатил",
    },
    "successful_payment": {
        "label": "Успешная оплата",
        "description": "Звонок после получения оплаты — подтверждение, благодарность, следующий шаг",
        "keywords": ["оплатили", "деньги пришли", "поступило", "спасибо за оплату"],
        "crm_context": "исходящий по действующему клиенту",
        "stages": [
            "Подтверждение получения оплаты, благодарность",
            "Объяснение следующих шагов по проекту",
            "Установка ожиданий по срокам и процессу",
            "Допродажа или упоминание смежных продуктов (уместно!)",
            "Фиксация следующей точки контакта",
        ],
        "critical_stages": [
            "Объяснение следующих шагов по проекту",
            "Установка ожиданий по срокам и процессу",
        ],
        "success_criteria": "Клиент понимает что будет дальше, выращивание лояльности",
    },
    "upsell": {
        "label": "Доп продажа (отдельный звонок)",
        "description": "Звонок с целью предложить дополнительный продукт действующему клиенту",
        "keywords": ["дополнительно", "ещё", "также", "расширить", "добавить", "ISO", "кадры"],
        "crm_context": "исходящий по действующему клиенту",
        "stages": [
            "Приветствие и напоминание контекста",
            "Причина звонка — конкретная польза для клиента",
            "Презентация нового продукта через боль/выгоду клиента",
            "Квалификация интереса",
            "Обработка возражений",
            "Закрытие или следующий шаг",
            "Фиксация договорённости",
        ],
        "critical_stages": [
            "Причина звонка — конкретная польза для клиента",
            "Презентация нового продукта через боль/выгоду клиента",
            "Закрытие или следующий шаг",
        ],
        "success_criteria": "Клиент проявил интерес, назначен следующий шаг по допродаже",
    },
}

# ============================================================
# 17 КРИТЕРИЕВ
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

CRITERIA_WEIGHTS = {
    "Полнота выявления потребности": 0.10,
    "Глубина уточняющих вопросов": 0.05,
    "Резюмирование потребности": 0.05,
    "Презентация решения через пользу": 0.12,
    "Экспертность и уверенность": 0.08,
    "Распознавание возражения": 0.08,
    "Качество отработки возражений": 0.12,
    "Попытка закрытия / продвижение сделки": 0.10,
    "Фиксация следующего шага и даты связи": 0.07,
    "Выполнение обещаний и связь с CRM": 0.03,
    "Представление и корректное начало": 0.02,
    "Подготовленность к звонку": 0.02,
    "Четкость цели и рамки разговора": 0.01,
    "Управление диалогом": 0.02,
    "Речь и эмоциональный фон": 0.02,
    "Корректное завершение разговора": 0.01,
    "Допродажа / расширение решения": 0.10,
}
assert abs(sum(CRITERIA_WEIGHTS.values()) - 1.0) < 0.001

# ============================================================
# ТРИГГЕРЫ
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
    "Не использовал тип звонка по эталону",
    "Пропущены критические стадии для данного типа звонка",
]

CRITICAL_TRIGGER_RUDENESS = "Грубость или непрофессионализм"
CRITICAL_SCORE_THRESHOLD = 5.0
CRITICAL_TRIGGERS_COUNT = 2


# ============================================================
# УТИЛИТЫ
# ============================================================

def format_timecode(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def compute_weighted_score(scores: Dict[str, float]) -> float:
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
# РУЧНЫЕ ПРАВКИ
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
# ТРАНСКРИБАЦИЯ — Vibe Code Whisper Large v3 Turbo (бесплатно)
# ============================================================

VIBE_WHISPER_MODEL = "bitrix/deepdml/faster-whisper-large-v3-turbo-ct2"
VIBE_WHISPER_URL = "https://vibecode.bitrix24.tech/v1/audio/transcriptions"

def transcribe_audio(audio_path: Path, model=None) -> Dict[str, Any]:
    """
    Транскрибация через Vibe Code AI Router (Whisper Large v3 Turbo).
    Параметр model оставлен для обратной совместимости, не используется.
    """
    logger.info(f"Транскрибируем {audio_path.name} через Vibe Code Whisper...")

    api_key = os.environ.get("VIBE_API_KEY")
    if not api_key:
        raise RuntimeError("VIBE_API_KEY не задан")

    with open(audio_path, "rb") as f:
        response = requests.post(
            VIBE_WHISPER_URL,
            headers={"X-Api-Key": api_key},
            files={"file": (audio_path.name, f, "audio/mpeg")},
            data={
                "model": VIBE_WHISPER_MODEL,
                "language": "ru",
                "response_format": "verbose_json",
                "timestamp_granularities[]": "segment",
            },
            timeout=300,
        )

    response.raise_for_status()
    result = response.json()

    full_text = result.get("text", "").strip()
    duration_sec = 0
    segments = []

    for seg in result.get("segments", []):
        segments.append({
            "start": round(float(seg.get("start", 0)), 1),
            "end": round(float(seg.get("end", 0)), 1),
            "text": seg.get("text", "").strip(),
        })

    if segments:
        duration_sec = segments[-1]["end"]

    text_with_timecodes = "\n".join(
        f"[{format_timecode(s['start'])}] {s['text']}" for s in segments
    )

    if not segments and full_text:
        text_with_timecodes = full_text

    return {
        "text": full_text,
        "text_with_timecodes": text_with_timecodes,
        "segments": segments,
        "duration_sec": round(duration_sec, 1),
    }



# ============================================================
# CLAUDE API — HTTP (без SDK, совместимо с Bitrix Vibe Code)
# ============================================================

def call_claude_api(prompt: str, max_tokens: int = 10000) -> Tuple[str, Dict]:
    """
    Запрос к AI Router Vibe Code (OpenAI-совместимый формат).
    Авторизация: заголовок X-Api-Key с ключом Vibe Code (VIBE_API_KEY).
    Никакой Anthropic/OpenAI подписки не нужно — модели бесплатные.
    """
    api_key = os.environ.get("VIBE_API_KEY")
    if not api_key:
        raise RuntimeError("VIBE_API_KEY не задан. Возьми ключ в Vibe Code → API-ключи.")

    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
    }
    payload = {
        "model": MODEL_CLAUDE,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    response = requests.post(VIBE_AI_URL, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    data = response.json()

    if "error" in data:
        raise RuntimeError(f"Vibe AI error: {data['error']}")

    # OpenAI-совместимый формат ответа
    text = ""
    for choice in data.get("choices", []):
        text += choice.get("message", {}).get("content", "")

    usage = data.get("usage", {})
    meta = {
        "model": MODEL_CLAUDE,
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "approx_cost_usd": 0.0,  # бесплатно через Vibe Code
    }
    return text.strip(), meta


# ============================================================
# ОПРЕДЕЛЕНИЕ ТИПА ЗВОНКА
# ============================================================

def detect_call_type(transcript: str, call_meta: Dict) -> str:
    """
    Определяет тип звонка из 11 возможных через Claude.
    Возвращает ключ из CALL_TYPES.
    """
    direction = "входящий" if call_meta.get("direction") == "incoming" else "исходящий"
    crm_context = call_meta.get("crm", {}).get("owner_type", "")

    types_list = "\n".join(
        f'- "{key}": {info["label"]} — {info["description"]}'
        for key, info in CALL_TYPES.items()
    )

    prompt = f"""Ты — эксперт по анализу продаж. Определи тип звонка из списка ниже.

ИНФОРМАЦИЯ О ЗВОНКЕ:
- Направление: {direction}
- CRM-контекст: {crm_context}

ТРАНСКРИПТ (первые 2000 символов):
---
{transcript[:2000]}
---

ВОЗМОЖНЫЕ ТИПЫ ЗВОНКОВ:
{types_list}

Ответь ТОЛЬКО ключом из списка (например "primary_incoming_new"), без объяснений, без кавычек, без пробелов.
Выбери наиболее подходящий тип. Если не уверен — выбери ближайший по смыслу."""

    try:
        text, _ = call_claude_api(prompt, max_tokens=50)
        text = text.strip().strip('"\'').lower().replace(" ", "_")
        if text in CALL_TYPES:
            return text
        # Fallback: поиск ключа в ответе
        for key in CALL_TYPES:
            if key in text:
                return key
    except Exception as e:
        logger.warning(f"Ошибка определения типа звонка: {e}")

    # Эвристика по метаданным
    if call_meta.get("direction") == "incoming":
        return "primary_incoming_new"
    return "cold_new"


# ============================================================
# ПРОМПТ АНАЛИЗА — ЖИВАЯ ОЦЕНКА КАК РОП
# ============================================================

def build_analysis_prompt(
    transcript_with_timecodes: str,
    call_meta: Dict,
    scripts: List[Tuple[str, str]],
    call_type_key: str,
) -> str:
    call_type = CALL_TYPES.get(call_type_key, CALL_TYPES["primary_incoming_new"])

    call_info = (
        f"- Менеджер: {call_meta.get('manager', {}).get('name', 'неизвестно')}\n"
        f"- Клиент: {call_meta.get('client', {}).get('name', 'неизвестно')}\n"
        f"- Компания клиента: {call_meta.get('client', {}).get('company', 'неизвестно')}\n"
        f"- Направление: {'входящий' if call_meta.get('direction') == 'incoming' else 'исходящий'}\n"
        f"- Время: {call_meta.get('created', '')}\n"
        f"- Реальная длительность звонка (по записи): {call_meta.get('duration_sec', 0)} сек\n"
        f"- ТИП ЗВОНКА: {call_type['label']}\n"
        f"- Цель звонка: {call_type['description']}\n"
    )

    scripts_block = ""
    if scripts:
        scripts_block = "\nСКРИПТЫ MAVIS GROUP (ориентир, не чеклист):\n"
        for name, text in scripts:
            scripts_block += f"\n=== {name} ===\n{text[:800]}\n"

    stages_list = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(call_type["stages"]))

    prompt = f"""Ты — опытный руководитель отдела продаж (РОП) компании Mavis Group (Беларусь).
Компания продаёт: СРО, ISO, ГОСТ, СПК, аттестация специалистов в строительстве.

Твоя задача — прослушать звонок и дать честный, объективный разбор как живой человек, а не робот по чеклисту.

ПРАВИЛА ОЦЕНКИ:
- Оценивай реальный разговор, а не соответствие скрипту
- Скрипт — это ориентир, менеджер не обязан следовать ему дословно
- Короткий звонок (уточнение, перенос, быстрый ответ) — оценивай по его реальной цели, не требуй всех этапов
- Оценка 7/10 = хороший рабочий звонок, 8-9 = отличный, 10 = идеальный
- Оценка 5-6 = есть замечания но в целом нормально, ниже 5 = серьёзные проблемы
- НЕ снижай оценку если менеджер не сделал допродажу в коротком техническом звонке
- НЕ снижай оценку за "не использовал все этапы скрипта" если цель звонка была узкой
- СНИЖАЙ оценку за: грубость, потерю клиента, отсутствие следующего шага в важном звонке, неотработанное возражение когда клиент был готов купить

ИНФОРМАЦИЯ О ЗВОНКЕ:
{call_info}
{scripts_block}
ТИП ЗВОНКА: {call_type["label"]}
Типичные стадии для этого типа (ориентир):
{stages_list}
Критерий успеха: {call_type["success_criteria"]}

ТРАНСКРИПТ:
---
{transcript_with_timecodes}
---

ВАЖНО: транскрипт получен через Whisper — могут быть небольшие искажения слов, понимай по смыслу.
Таймкоды [MM:SS] используй в цитатах.

ОСОБЫЙ СЛУЧАЙ — СБОЙ ТРАНСКРИПЦИИ:
Если транскрипт содержит только повторяющиеся обрывки, помехи или явно не отражает
реальную длительность звонка (например звонок длится 1+ минуту, а текста почти нет) —
это технический сбой распознавания речи, а НЕ вина менеджера и НЕ признак плохого звонка.
В этом случае:
- НЕ ставь низкую оценку (1-3) только из-за нехватки текста
- Поставь нейтральную оценку 5-6 с пометкой в score_explanation: "Транскрипция звонка
  технически неполная, оценка ориентировочная — рекомендуем прослушать запись лично"
- В summary укажи, что текста недостаточно для полного разбора
- НЕ выставляй критические триггеры (is_critical=true) на основании одной лишь нехватки текста

ТВОЙ РАЗБОР:

1. Раздели реплики на менеджера и клиента с таймкодами.

2. Определи реальную цель этого конкретного звонка (1 предложение).

3. Краткое резюме — что произошло в звонке (2-3 предложения).

4. Итог — чем завершился звонок, конкретный результат.

5. Ключевые цитаты клиента (1-2 штуки) — самое важное что сказал клиент.

6. ОЦЕНКА 1-10 и объяснение почему именно такая оценка (2-3 предложения).
   Оценивай по реальному результату и качеству общения, не по чеклисту.

7. Что сделано хорошо (1-3 конкретных момента с таймкодом).

8. Что улучшить (1-3 конкретных момента с таймкодом и цитатой).
   Только реальные проблемы которые повлияли на результат звонка.

9. Главная рекомендация — одно конкретное действие которое больше всего улучшит следующий звонок.

10. Следующий контакт — если в разговоре договорились о дате/времени следующего звонка.

11. КЛЮЧЕВЫЕ МОМЕНТЫ звонка (3-5 штук) — самые важные эпизоды:
    - positive: что менеджер сделал особенно хорошо (с таймкодом)
    - negative: критическая ошибка или упущение (с таймкодом)
    - neutral: важный факт или поворотный момент разговора

11. Флаги — только если реально есть (не придумывай):
    - "critical": true если грубость, скандал, потеря клиента по вине менеджера
    - "missed_deal": true если клиент был готов купить а менеджер не закрыл
    - "no_next_step": true если важный звонок завершился без договорённости о следующем шаге

ОТВЕТ СТРОГО В JSON, БЕЗ ОБЁРТКИ ```json:

{{
  "call_type": {{
    "key": "{call_type_key}",
    "label": "{call_type["label"]}",
    "confirmed": true
  }},
  "transcript_split": [
    {{"speaker": "manager", "time": "MM:SS", "text": "..."}},
    {{"speaker": "client", "time": "MM:SS", "text": "..."}}
  ],
  "call_goal": "...",
  "summary": "...",
  "outcome": "...",
  "key_quotes": [
    {{"speaker": "client", "time": "MM:SS", "text": "..."}}
  ],
  "overall_score": 7.0,
  "score_explanation": "...",
  "strengths": [
    {{"text": "...", "time": "MM:SS"}}
  ],
  "improvements": [
    {{"text": "...", "quote": "...", "time": "MM:SS"}}
  ],
  "recommendation": "...",
  "next_contact": {{
    "date_or_period": null,
    "time": null,
    "initiator": null,
    "context": null
  }},
  "flags": {{
    "critical": false,
    "critical_reason": null,
    "missed_deal": false,
    "no_next_step": false
  }},
  "key_moments": [
    {{"type": "positive|negative|neutral", "time": "MM:SS", "text": "Краткое описание момента", "detail": "Цитата или пояснение"}}
  ],
  "scripts_used": {json.dumps([n for n, _ in scripts], ensure_ascii=False)}
}}"""
    return prompt


# ============================================================
# ОСНОВНАЯ ФУНКЦИЯ АНАЛИЗА
# ============================================================

def analyze_transcript(
    transcription: Dict,
    call_meta: Dict,
    scripts_db: Dict,
) -> Dict[str, Any]:
    transcript_text = transcription["text"]
    transcript_tc = transcription.get("text_with_timecodes") or transcript_text
    relevant_scripts = select_relevant_scripts(transcript_text, scripts_db)

    if relevant_scripts:
        logger.info(f"   Скрипты: {', '.join(n for n, _ in relevant_scripts)}")

    # Шаг 1: определяем тип звонка
    logger.info("   Определяем тип звонка...")
    call_type_key = detect_call_type(transcript_text, call_meta)
    logger.info(f"   Тип: {CALL_TYPES[call_type_key]['label']}")

    # Шаг 2: полный анализ с учётом типа
    prompt = build_analysis_prompt(transcript_tc, call_meta, relevant_scripts, call_type_key)
    text, meta = call_claude_api(prompt, max_tokens=10000)

    # Парсим JSON
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    decoder = json.JSONDecoder()
    result, _ = decoder.raw_decode(text)

    # Оценка берётся напрямую от модели (живая, не взвешенная)
    try:
        result["overall_score"] = float(result.get("overall_score", 0))
    except (TypeError, ValueError):
        result["overall_score"] = 0.0
    result["overall_score_method"] = "rop_judgement"

    # Критичность — из флагов которые поставила модель
    flags = result.get("flags", {})
    is_crit = bool(flags.get("critical", False))
    crit_reason = flags.get("critical_reason") or ""
    # Дополнительно: оценка ниже 4 = критично
    if result["overall_score"] < 4.0:
        is_crit = True
        crit_reason = crit_reason or f"Низкая оценка ({result['overall_score']}/10)"
    result["is_critical"] = is_crit
    result["critical_reason"] = crit_reason

    result["_meta"] = meta
    return result


# ============================================================
# CLI
# ============================================================

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("=" * 60)
    print(f"Анализ звонков (Vibe Whisper + {MODEL_CLAUDE}) — v2 с типами звонков")
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

    print(f"\nТранскрибация: Vibe Code {VIBE_WHISPER_MODEL}")
    print(f"Анализ: {MODEL_CLAUDE}\n")

    analyses_path = Path("analyses.json")
    if analyses_path.exists():
        analyses = json.loads(analyses_path.read_text(encoding="utf-8"))
    else:
        analyses = {}

    total_cost = 0.0
    success = 0
    failed = 0
    critical_count = 0
    type_stats = {}

    for i, audio_path in enumerate(audio_files, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(audio_files)}] {audio_path.name}")
        print(f"{'='*60}")

        file_id_str = audio_path.name.split("_")[0]
        call_meta = next(
            (c for c in calls if c.get("audio") and str(c["audio"].get("file_id")) == file_id_str),
            None,
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

        call_duration = call_meta.get("duration_sec") or 0
        transcribe_only_mode = call_duration and call_duration < MIN_DURATION_FOR_ANALYSIS

        try:
            transcription = transcribe_audio(audio_path)
            print(f"   Транскрипт: {len(transcription['text'])} символов, {transcription['duration_sec']} сек")

            if transcribe_only_mode:
                # Звонок 16-29 сек: сохраняем транскрипт, но без ИИ-анализа
                print(f"   ℹ Короткий звонок ({call_duration} сек) — только транскрипт, без анализа")
                analyses[activity_id] = {
                    "call_meta": call_meta,
                    "transcription": transcription,
                    "analysis": None,
                    "analyzed_at": datetime.now().isoformat(),
                }
                success += 1
                continue

            if len(transcription["text"]) < 50:
                print(f"   ⚠ Слишком короткий транскрипт, пропускаем анализ")
                failed += 1
                continue

            analysis = analyze_transcript(transcription, call_meta, scripts_db)
            analysis = apply_manual_corrections(activity_id, analysis, corrections)

            cost = analysis["_meta"]["approx_cost_usd"]
            total_cost += cost
            score = analysis.get("overall_score", 0)
            call_type_label = analysis.get("call_type", {}).get("label", "неизвестно")

            type_stats[call_type_label] = type_stats.get(call_type_label, 0) + 1

            crit_mark = ""
            if analysis.get("is_critical"):
                critical_count += 1
                crit_mark = f" 🔴 КРИТИЧНО ({analysis['critical_reason']})"

            missed = analysis.get("critical_stages_missed", [])
            missed_str = f" ⚠️  Пропущены: {', '.join(missed[:2])}" if missed else ""

            print(f"   ✅ Тип: {call_type_label}")
            print(f"   ✅ Оценка: {score}/10, стоимость: ${cost:.4f}{crit_mark}{missed_str}")

            analyses[activity_id] = {
                "call_meta": call_meta,
                "transcription": transcription,
                "analysis": analysis,
                "analyzed_at": datetime.now().isoformat(),
            }
            success += 1

            # Уведомление менеджеру в Bitrix
            try:
                from bitrix import send_manager_notifications
                send_manager_notifications([(call_meta, analysis)])
            except Exception as _notify_err:
                logger.debug(f"Уведомление не отправлено: {_notify_err}")

            if success % 10 == 0:
                analyses_path.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"   💾 Промежуточное сохранение ({success})")

        except Exception as e:
            print(f"   ❌ {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    analyses_path.write_text(json.dumps(analyses, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}\nИТОГИ\n{'='*60}")
    print(f"   ✅ Успешно: {success}")
    print(f"   ❌ Ошибок: {failed}")
    print(f"   🔴 Критичных: {critical_count}")
    print(f"   💰 Общая стоимость: ${total_cost:.4f}")
    print(f"   📊 Анализов в базе: {len(analyses)}")
    if type_stats:
        print(f"\nРаспределение по типам звонков:")
        for t, cnt in sorted(type_stats.items(), key=lambda x: -x[1]):
            print(f"   - {t}: {cnt}")


if __name__ == "__main__":
    main()
