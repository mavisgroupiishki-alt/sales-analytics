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
import re
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
    "unknown": {
        "label": "Тип звонка требует проверки",
        "description": "CRM-контекста и содержания недостаточно для безопасной классификации",
        "keywords": [],
        "crm_context": "не подтверждён",
        "stages": [],
        "critical_stages": [],
        "success_criteria": "Сначала подтвердить цель звонка и CRM-контекст",
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

CRITICAL_RULE_IDS = {
    "payment_commitment_broken",
    "ready_to_buy_not_closed",
    "material_objection_unhandled",
    "confirmed_rudeness",
    "promised_action_missing_crm",
}

# The model returns an evidenced observation per criterion.  This deterministic
# table, not its free-form final score, decides which observations count and
# how much. Narrow calls therefore are not penalised for irrelevant stages.
RUBRIC_CRITERIA = {
    "opening": ("Понятное начало и цель разговора", 0.06),
    "need": ("Выявление потребности или причины решения", 0.16),
    "presentation": ("Презентация через пользу клиента", 0.16),
    "expertise": ("Экспертность и точность ответа", 0.10),
    "objection": ("Распознавание и отработка существенного возражения", 0.18),
    "closing": ("Продвижение сделки к решению", 0.14),
    "next_step": ("Конкретный следующий шаг", 0.16),
    "communication": ("Корректность и ясность общения", 0.04),
}

_FULL_SALES_RUBRIC = ("opening", "need", "presentation", "expertise", "objection", "closing", "next_step", "communication")
_CALL_TYPE_CRITERIA = {
    "primary_incoming_new": _FULL_SALES_RUBRIC,
    "primary_incoming_existing": _FULL_SALES_RUBRIC,
    "cold_new": _FULL_SALES_RUBRIC,
    "cold_periodika": _FULL_SALES_RUBRIC,
    "cold_reactivation": _FULL_SALES_RUBRIC,
    "kp_defense": _FULL_SALES_RUBRIC,
    "kp_feedback": ("opening", "need", "presentation", "expertise", "objection", "next_step", "communication"),
    "counteroffer": _FULL_SALES_RUBRIC,
    "objection_handling": ("opening", "need", "expertise", "objection", "closing", "next_step", "communication"),
    "payment_push": ("opening", "need", "expertise", "objection", "closing", "next_step", "communication"),
    "successful_payment": ("opening", "expertise", "next_step", "communication"),
    "upsell": _FULL_SALES_RUBRIC,
}
EXPECTED_RUBRIC_CODE = "jarvis_rop"
EXPECTED_RUBRIC_VERSION = 1


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


def applicable_criteria(call_type_key: str) -> tuple[str, ...]:
    """Return only criteria relevant to a confirmed call purpose."""
    return _CALL_TYPE_CRITERIA.get(call_type_key, ())


def compute_applicable_score(call_type_key: str, observations: Any) -> Optional[float]:
    """Calculate a reproducible score from applicable AI observations only."""
    applicable = applicable_criteria(call_type_key)
    if not applicable or not isinstance(observations, list):
        return None
    ratings: Dict[str, float] = {}
    for item in observations:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        if code not in applicable or item.get("applicable") is not True:
            continue
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError):
            continue
        if 0 <= score <= 10:
            ratings[code] = score
    # Missing evidence is not silently reweighted away.  The ROP sees an
    # incomplete rubric as a review item instead of an inflated score.
    if set(ratings) != set(applicable):
        return None
    denominator = sum(RUBRIC_CRITERIA[code][1] for code in applicable)
    return round(sum(ratings[code] * RUBRIC_CRITERIA[code][1] for code in applicable) / denominator, 1)


def valid_timecode(value: str, duration_seconds: Any = None) -> bool:
    """Accept only exact MM:SS evidence timestamps, never arbitrary text."""
    match = re.fullmatch(r"(\d{2,}):([0-5]\d)", value or "")
    if not match:
        return False
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError):
        return True
    return int(match.group(1)) * 60 + int(match.group(2)) <= duration


def evaluate_triage(analysis: Dict[str, Any]) -> Tuple[str, str, str]:
    """Return `(status, reason, rule_id)` without treating score as an incident.

    A model may suggest a critical flag, but it is accepted only when it names an
    allowed rule and provides a quote plus a timestamp. Ambiguity is visible to
    the ROP as `needs_review`, not silently converted into a false alarm.
    """
    # Exclusions are deterministic pipeline outcomes (for example a call that is
    # too short to judge).  They must never reappear as a normal or urgent call
    # when a stored analysis is read back by the dashboard.
    if analysis.get("review_status") == "excluded" or analysis.get("exclude_from_stats"):
        return "excluded", str(analysis.get("exclusion_reason") or analysis.get("poor_audio_reason") or analysis.get("not_sales_reason") or "Звонок исключён из оценки"), ""

    flags = analysis.get("flags") or {}
    evidence = flags.get("critical_evidence") or {}
    rule_id = str(flags.get("critical_rule_id") or "")
    quote = str(evidence.get("quote") or "").strip()
    time = str(evidence.get("time") or "").strip()

    if flags.get("critical") and rule_id in CRITICAL_RULE_IDS and len(quote) >= 8 and valid_timecode(time, analysis.get("source_duration_seconds")):
        return "critical", str(flags.get("critical_reason") or rule_id), rule_id
    if flags.get("critical"):
        return "needs_review", "Нужна проверка РОПом: критичный флаг не подтверждён доказательством", ""

    try:
        low_score = float(analysis.get("overall_score")) < 4.0
    except (TypeError, ValueError):
        low_score = False
    if low_score or analysis.get("overall_score") is None or analysis.get("call_type", {}).get("key") == "unknown":
        return "needs_review", "Нужна проверка РОПом: недостаточно надёжных оснований для критичности", ""
    return "normal", "", ""


def is_critical(analysis: Dict[str, Any]) -> Tuple[bool, str]:
    """Backward-compatible helper for legacy report code."""
    status, reason, _ = evaluate_triage(analysis)
    return status == "critical", reason


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

    Ответь строго JSON: {"call_type_key":"ключ из списка или unknown","confirmed":true|false,"evidence":"короткая цитата или факт из транскрипта"}.
    `confirmed=true` допустим только если тип прямо подтверждается транскриптом или CRM-контекстом. Не угадывай, является ли клиент новым, холодным или действующим: если основания нет, верни `unknown`."""

    try:
        text, _ = call_claude_api(prompt, max_tokens=50)
        payload = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
        key = str(payload.get("call_type_key") or "").strip().lower()
        evidence = str(payload.get("evidence") or "").strip()
        if payload.get("confirmed") is True and key in CALL_TYPES and key != "unknown" and len(evidence) >= 8:
            return key
    except Exception as e:
        logger.warning(f"Ошибка определения типа звонка: {e}")

    # Нельзя по одному направлению звонка угадывать тип клиента или цель.
    return "unknown"


# ============================================================
# ПРОМПТ АНАЛИЗА — ЖИВАЯ ОЦЕНКА КАК РОП
# ============================================================

def build_analysis_prompt(
    transcript_with_timecodes: str,
    call_meta: Dict,
    scripts: List[Tuple[str, str]],
    call_type_key: str,
) -> str:
    call_type = CALL_TYPES.get(call_type_key, CALL_TYPES["unknown"])

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
    criteria_contract = "\n".join(
        f'- `{code}` — {RUBRIC_CRITERIA[code][0]}' for code in applicable_criteria(call_type_key)
    ) or "Тип звонка не подтверждён: критерии не оценивай."

    prompt = f"""Ты — Игорь, лучший тренер по продажам в СНГ с 15-летним опытом в B2B.
Ты лично закрыл сотни сложных сделок и обучил десятки отделов продаж.
Компания Mavis Group (Беларусь) продаёт: СРО, ISO, ГОСТ, СПК, аттестация специалистов в строительстве.

Твоя задача — разобрать этот звонок как жёсткий, честный тренер по продажам.
Ты видишь сквозь вежливые слова и общие фразы. Тебя интересует только одно: продвинулась ли сделка вперёд?

ПРАВИЛА ОЦЕНКИ:
- Оценивай по реальному результату для сделки, а не по "вежливости" и "представился ли менеджер"
- "Уточнил способ связи", "представился" — это НЕ достижения, это базовый минимум. Не хвали за это.
- Хвали только за реальные продажные действия: выявил боль клиента, отработал возражение, закрыл на конкретный шаг
- Критикуй конкретно: не "мог лучше работать с возражениями", а "клиент сказал 'дорого' [01:23] — менеджер не спросил с чем сравнивает и не обосновал цену"
- Оценка 7+ = менеджер реально продвинул сделку. 5-6 = топтание на месте. Ниже 5 = потенциальный клиент потерян или сделка зависла
- НЕ снижай за отсутствие этапов скрипта если цель звонка была узкой (уточнение, перенос)
- СНИЖАЙ за: нет конкретного следующего шага, не отработано возражение когда клиент был готов, звонок завершился в никуда

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
Если транскрипт пустой или явно не отражает длительность звонка —
- НЕ ставь низкую оценку только из-за нехватки текста
- Поставь нейтральную оценку 5-6 с пометкой в score_explanation что транскрипция неполная
- НЕ выставляй critical=true на основании одной лишь нехватки текста

ТВОЙ РАЗБОР:

1. Раздели реплики на менеджера и клиента с таймкодами.

2. Определи реальную цель этого конкретного звонка (1 предложение).

3. Краткое резюме — что реально произошло в продажном смысле (2-3 предложения). Без воды.

4. Итог — сделка продвинулась, стоит на месте или потеряна? Конкретно.

5. Ключевые цитаты клиента (1-2 штуки) — самое важное что сказал клиент про его ситуацию или решение.

6. ОЦЕНКА 1-10 и объяснение — почему именно столько с точки зрения продажного результата.

6a. ФАКТЫ ДЛЯ РАСЧЁТА БАЛЛА. Оцени ТОЛЬКО перечисленные критерии. Для каждого дай score 0–10, конкретный факт и цитату/таймкод. Не добавляй неприменимые критерии и не ставь им ноль:
{criteria_contract}

7. Что сделано хорошо (1-3 момента) — ТОЛЬКО реальные продажные действия с таймкодом и цитатой.
   НЕ хвали за "представился", "был вежлив", "уточнил контакт" — это базовый минимум.

8. Что улучшить (1-3 момента) — КОНКРЕТНО: что именно сказал менеджер [таймкод], что надо было сказать вместо этого.
   Пример хорошего замечания: "В [01:45] клиент сказал 'я подумаю' — менеджер согласился и положил трубку. Нужно было спросить: 'Что именно вас останавливает?' и отработать возражение."

9. Главная рекомендация — одно конкретное действие для следующего звонка с этим клиентом.

10. Следующий контакт — если в разговоре договорились о дате/времени.

11. КЛЮЧЕВЫЕ МОМЕНТЫ звонка (3-5 штук) — самые важные повороты:
    - positive: конкретное продажное действие которое реально помогло
    - negative: момент где сделка могла продвинуться но не продвинулась
    - neutral: важный факт о клиенте или ситуации

12. Флаги — только если реально есть:
    - "critical": true ТОЛЬКО при подтверждённом высоком риске. Выбери один `critical_rule_id`:
      • `payment_commitment_broken` — сорвана подтверждённая договорённость об оплате/закрытии;
      • `ready_to_buy_not_closed` — клиент прямо готов купить/продолжить, но менеджер не подвёл к обязательному следующему шагу;
      • `material_objection_unhandled` — существенное возражение осталось без ответа, после чего возможность потеряна;
      • `confirmed_rudeness` — есть дословная грубость менеджера, а не предположение по тону транскрипта;
      • `promised_action_missing_crm` — менеджер обещал действие, а CRM-контекст явно подтверждает его отсутствие.
      Для `critical=true` ОБЯЗАТЕЛЬНЫ точная цитата и таймкод в `critical_evidence`. Неиспользованная допродажа,
      общий низкий балл, короткий звонок, непонятный результат или сомнение в транскрипте — это не critical.
    - "missed_deal": true если клиент был готов купить а менеджер не закрыл
    - "no_next_step": true если важный звонок завершился без договорённости о следующем шаге
    - "poor_audio": true если запись с сильными помехами — текст расшифрован плохо. Укажи причину в poor_audio_reason.
    - "not_sales": true если звонок НЕ касается продаж: ошибочный номер, не тот человек, технический вопрос не по теме. Укажи причину в not_sales_reason.


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
  "criteria": [
    {{"code": "one_of_the_listed_codes", "applicable": true, "score": 0.0, "finding": "конкретный факт", "time": "MM:SS", "quote": "..."}}
  ],
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
    "critical_rule_id": null,
    "critical_evidence": {{"time": null, "quote": null}},
    "missed_deal": false,
    "no_next_step": false,
    "poor_audio": false,
    "poor_audio_reason": null,
    "not_sales": false,
    "not_sales_reason": null
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

    result["source_duration_seconds"] = call_meta.get("duration_sec")

    # The model's broad assessment stays as context. The stored score is
    # calculated only from structured, applicable rubric observations.
    try:
        result["model_overall_score"] = float(result.get("overall_score", 0))
    except (TypeError, ValueError):
        result["model_overall_score"] = None
    rubric_score = compute_applicable_score(call_type_key, result.get("criteria"))
    result["overall_score"] = rubric_score
    result["overall_score_method"] = "applicable_rubric_v1" if rubric_score is not None else "not_scored"

    flags = result.get("flags", {}) or {}

    # Плохое качество записи — проверяем программно ДО вызова ИИ
    result["poor_audio"] = bool(flags.get("poor_audio", False))
    result["poor_audio_reason"] = flags.get("poor_audio_reason") or ""

    # Дополнительная программная проверка: мало текста относительно длительности
    call_dur = call_meta.get("duration_sec") or 0
    transcript_len = len(transcription.get("text", ""))
    transcript_dur = transcription.get("duration_sec") or 0
    if not result["poor_audio"]:
        if transcript_len < 30:
            result["poor_audio"] = True
            result["poor_audio_reason"] = "Транскрипт пустой или почти пустой"
        elif call_dur >= 30 and transcript_dur > 0 and (transcript_dur / call_dur) < 0.3:
            result["poor_audio"] = True
            result["poor_audio_reason"] = f"Whisper расшифровал только {int(transcript_dur)}с из {int(call_dur)}с — вероятно помехи в начале"

    # Нерелевантный звонок (не продажи)
    result["not_sales"] = bool(flags.get("not_sales", False))
    result["not_sales_reason"] = flags.get("not_sales_reason") or ""

    # Если плохое качество или нерелевантный — не учитываем в общей статистике
    if result["poor_audio"] or result["not_sales"]:
        result["exclude_from_stats"] = True
        result["is_critical"] = False  # не показываем в срочных
        result["review_status"] = "excluded"
        result["critical_reason"] = ""
        result["critical_rule_id"] = ""
    else:
        status, reason, rule_id = evaluate_triage(result)
        result["review_status"] = status
        result["is_critical"] = status == "critical"
        result["critical_reason"] = reason
        result["critical_rule_id"] = rule_id

    result["_meta"] = meta
    return result


# ============================================================
# CLI
# ============================================================

def reanalysis_scope(environment: Dict[str, str], today: Optional[str] = None) -> Tuple[set[str], Optional[str]]:
    """Return the explicit reanalysis target without widening a normal batch.

    ``REANALYZE_ID`` is used by the card action.  ``REANALYZE_TODAY=1`` is for
    the private worker after it has fetched today's recordings.  A date override
    makes a historical shadow run deterministic and is intentionally not used
    by the web UI.
    """
    ids = {item.strip() for item in environment.get("REANALYZE_ID", "").split(",") if item.strip()}
    if environment.get("REANALYZE_TODAY") != "1":
        return ids, None
    return ids, environment.get("REANALYZE_DATE") or today or datetime.now().date().isoformat()


def is_reanalysis_target(call: Dict[str, Any], ids: set[str], date: Optional[str]) -> bool:
    """Select only explicitly requested calls; dates are compared in source time."""
    if str(call.get("activity_id") or "") in ids:
        return True
    return bool(date and str(call.get("created") or "")[:10] == date)


def mirror_analyses_to_jarvis(calls: list[Dict[str, Any]], analyses: Dict[str, Any]) -> int:
    """Persist completed analyses when the private worker has DB settings.

    This is deliberately a no-op for local legacy runs.  Production requires an
    explicitly configured rubric id, so no score is stored under an implicit
    evaluation standard.
    """
    database_url = os.environ.get("JARVIS_DATABASE_URL")
    if not database_url:
        return 0
    try:
        rubric_id = int(os.environ.get("JARVIS_RUBRIC_ID", ""))
    except ValueError as exc:
        raise RuntimeError("JARVIS_RUBRIC_ID must be configured for private analysis storage") from exc

    from jarvis_store import JarvisStore

    store = JarvisStore.connect(database_url)
    try:
        return store.write_analysis_snapshot(
            calls,
            analyses,
            rubric_id,
            force_new_version=os.environ.get("JARVIS_FORCE_ANALYSIS_VERSION") == "1",
        )
    finally:
        store.close()

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

    requested_ids, requested_date = reanalysis_scope(os.environ)
    targeted_reanalysis = bool(requested_ids or requested_date)
    if targeted_reanalysis:
        target_description = requested_date or ", ".join(sorted(requested_ids))
        print(f"♻️  Повторный разбор только для: {target_description}")

    total_cost = 0.0
    success = 0
    failed = 0
    critical_count = 0
    type_stats = {}
    completed_activity_ids: set[str] = set()

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
        if targeted_reanalysis and not is_reanalysis_target(call_meta, requested_ids, requested_date):
            print("   ⏭ Вне заданного повторного разбора")
            continue
        if is_reanalysis_target(call_meta, requested_ids, requested_date):
            analyses.pop(activity_id, None)
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
                    "analysis": {
                        "review_status": "excluded",
                        "exclude_from_stats": True,
                        "exclusion_reason": "Звонок короче 30 секунд: транскрипт сохранён без оценки качества",
                    },
                    "analyzed_at": datetime.now().isoformat(),
                }
                completed_activity_ids.add(activity_id)
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
            completed_activity_ids.add(activity_id)
            success += 1

            # РОП-поток не пишет сотрудникам автоматически. Уведомления —
            # отдельная, явно включаемая интеграция, чтобы повторный разбор
            # не создавал лишних сообщений в Bitrix.
            if os.environ.get("NOTIFY_MANAGERS") == "1":
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
    try:
        completed_analyses = {
            activity_id: analyses[activity_id]
            for activity_id in completed_activity_ids
            if activity_id in analyses
        }
        mirrored = mirror_analyses_to_jarvis(calls, completed_analyses)
        if os.environ.get("JARVIS_DATABASE_URL"):
            print(f"   🔒 В закрытую базу сохранено анализов: {mirrored}")
    except Exception as exc:
        # The process exits non-zero so n8n records a failed run instead of
        # presenting stale data as successfully updated.
        logger.error("Jarvis analysis persistence failed: %s", type(exc).__name__)
        raise

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
