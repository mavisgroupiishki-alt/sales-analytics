# Задачи: Память контекста компании

## Группа 1: Контракт хранения

- [ ] T001 Создать приватные таблицы фактов и срезов, индексы, RLS и безопасный backfill существующих анализов (файл: `migrations/006_company_context_memory.sql`) | balanced/medium | скилл: supabase | агент: —
- [x] T002 Добавить детерминированные helpers для CRM-связей, фактов и текущего среза (файл: `src/jarvis_store.py`) | frontier/high | скилл: systematic-debugging | агент: —

## Контрольная точка 1

Проверить: факты создаются только для явных CRM-связей, имеют ссылку на звонок/анализ и изолированы по воронке.

## Группа 2: Быстрый путь анализа

- [x] T003 Добавить чтение готовой памяти и преобразование её в ограниченный модельный контекст (файлы: `src/jarvis_store.py`, `src/claude_analyzer.py`) | frontier/high | скилл: systematic-debugging | агент: —
- [x] T004 Убрать исторический запрос Bitrix из production-пути с private БД; локальный legacy-режим без БД остаётся совместимым (файл: `src/claude_analyzer.py`) | balanced/medium | скилл: systematic-debugging | агент: —
- [x] T005 Синхронизировать ручной тип с контекстным фактом (файл: `src/jarvis_store.py`) | balanced/medium | скилл: systematic-debugging | агент: —

## Контрольная точка 2

Проверить: при наличии сохранённого контекста новый звонок не скачивает старое аудио, а prompt содержит максимум восемь фактов.

## Группа 3: Регрессии и выпуск

- [x] T006 Добавить тесты памяти, изоляции компании/воронки, ручной правки и fallback (файлы: `tests/test_jarvis_store.py`, `tests/test_claude_analyzer_prompt.py`) | balanced/medium | скилл: verification-before-completion | агент: —
- [ ] T007 Применить миграцию в Supabase, выполнить проверочные SQL и опубликовать worker (внешние среды) | balanced/medium | скилл: supabase, verification-before-completion | агент: —
