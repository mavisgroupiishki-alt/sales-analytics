# Задачи: Проверка звонков и контекст сделки

## Группа 1: Доступ и сохранность интерфейса

- [x] T001 Разрешить подписанной роли дашборда доступ к вкладкам РОПа, не ослабляя вход без сессии (файл: `app.py`) | balanced/medium | скилл: systematic-debugging | агент: —
- [x] T002 Сохранить iframe звонков при фоновой перерисовке основного дашборда (файл: `../mavis-operational-dashboard/app/static/app.js`) | balanced/medium | скилл: systematic-debugging | агент: —
- [ ] T003 Добавить регрессионные тесты ролей и сохранения iframe (файлы: `tests/test_operations_exports.py`, dashboard tests) | balanced/medium | скилл: verification-before-completion | агент: —

## Контрольная точка 1

Проверить: подписанная роль открывает требуемые вкладки; refresh не заменяет активный iframe загрузочной заглушкой.

## Группа 2: Ручная проверка и фильтрация

- [x] T004 Добавить persistent review contract и миграцию private schema с JSON fallback (файлы: `migrations/002_call_reviews.sql`, `src/jarvis_store.py`, `app.py`) | balanced/medium | скилл: systematic-debugging | агент: —
- [x] T005 Реализовать в карточке подтверждение типа, причину и точечный reanalysis (файлы: `app.py`, `src/jarvis_dashboard.py`) | balanced/medium | скилл: frontend-design | агент: —
- [x] T006 Добавить фильтры журнала и очередь решений РОПа (файлы: `src/jarvis_rop.py`, `src/jarvis_dashboard.py`) | balanced/medium | скилл: frontend-design | агент: —
- [x] T007 Покрыть API и фильтры тестами (файлы: `tests/test_jarvis_dashboard.py`, `tests/test_jarvis_rop.py`, `tests/test_operations_exports.py`) | balanced/medium | скилл: verification-before-completion | агент: —

## Контрольная точка 2

Проверить: ручной тип сохраняется, имеет приоритет в фильтрах и содержит автора/причину.

## Группа 3: Контекст и калибровка

- [x] T008 Собрать ограниченную историю одной сделки и передать её в классификацию и анализ (файл: `src/claude_analyzer.py`) | frontier/high | скилл: systematic-debugging | агент: —
- [x] T009 Сохранить и показать контекстный снимок на карточке звонка (файлы: `src/jarvis_store.py`, `src/jarvis_dashboard.py`) | balanced/medium | скилл: frontend-design | агент: —
- [x] T010 Расширить отчёт калибровки причинами ручных ошибок (файл: `app.py`) | balanced/medium | скилл: systematic-debugging | агент: —
- [x] T011 Добавить тесты границы в 8 звонков, изоляции сделки и неизменности ручной проверки (файлы: `tests/test_claude_analyzer_prompt.py`, `tests/test_jarvis_store.py`) | balanced/medium | скилл: verification-before-completion | агент: —

## Контрольная точка 3

Проверить: анализ использует только предыдущие звонки своей сделки, отображает их число и не теряет ручное решение.
