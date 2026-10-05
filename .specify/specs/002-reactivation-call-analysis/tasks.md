# Задачи: Полный анализ звонков реанимации

## Группа 1: Получение и подготовка истории

- [x] T001 Получать все завершённые прямые звонки открытых сделок реанимации из Bitrix (файл: `src/reactivation.py`) | balanced/medium | скилл: systematic-debugging | агент: —
- [x] T002 Добавить отдельный идемпотентный фоновый анализатор записей реанимации (файлы: `src/reactivation.py`, `src/jarvis_live_worker.py`) | frontier/high | скилл: systematic-debugging | агент: —

## Группа 2: Очередь и прозрачность

- [x] T003 Возвращать в очередь суммарное число звонков, число AI-разборов и причины недоступности (файл: `src/reactivation.py`) | balanced/medium | скилл: frontend-design | агент: —
- [x] T004 Показать новые честные счётчики в карточке очереди (файл: `../График платежей/.deployment-work/mavis-operational-dashboard/app/static/app.js`) | balanced/medium | скилл: frontend-design | агент: —

## Группа 3: Проверка

- [x] T005 Добавить тесты полного прямого контекста, идемпотентности и звонков без записи (файл: `tests/test_reactivation.py`) | balanced/medium | скилл: verification-before-completion | агент: —
