# The Persona Factory V2.1

Приватный Telegram-редактор, который пишет голосом Мустафы на основе
подтверждённых правил, реальных примеров речи и подтверждённого опыта.

## Основные гарантии

- Книги не являются личностью автора.
- Переписки используются только как примеры языка, но не как источник биографии.
- Новые факты и постоянные правила активируются только через `/review`.
- Религиозные цитаты допускаются только из `verified` knowledge-чанков.
- Один основной черновик генерируется за один LLM-вызов.
- Перед публикацией обязателен финальный preview.
- `publish_token` блокирует повторную публикацию одного draft.

## Поток работы

1. Отправить боту обычный текст.
2. Выбрать: улучшить, развить, запомнить факт или создать правило стиля.
3. Выбрать длину и технику подачи.
4. Исправить текст локально либо предложить постоянное правило.
5. Подтвердить постоянные сведения через `/review`.
6. Открыть финальный preview и опубликовать.

Доступные техники:

- `my_voice` — только голос автора.
- `clear` — кратко и ясно.
- `storytelling` — история через ситуацию, выбор и вывод.
- `show_your_work` — показать процесс и практический урок.

## Локальная установка

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
pytest tests/ -q
uvicorn main:app --host 0.0.0.0 --port 7860
```

Production использует `requirements-bot.txt`, Docker и Railway.

### Локальные папки (не в git / не на Railway)

Для разработки в Cursor/Codex используются локальные skill-артефакты — они **не коммитятся** и **не попадают в Docker/Railway**:

- `.agents/` — локальные agent skills
- `skills/` — каталог skills для IDE
- `skills-lock.json` — lock-файл skills
- `studio/` — Next.js панель управления (только `npm run dev` локально)

Эти пути перечислены в `.gitignore`. На Railway деплоится только бот/FastAPI (`main.py`, `src/`, Docker). Studio подключается к API по `PERSONA_API_URL` с вашей машины.

## Supabase V2.1

Миграции:

- `001`–`008` — воспроизводимый legacy baseline.
- `009_persona_v21_schema.sql` — новая additive schema.
- `010_persona_v21_legacy_backfill.sql` — перенос legacy author data в `pending`.
- `post_cleanup/011_persona_v21_post_cleanup.sql` — запускать только после knowledge cleanup.

Новые таблицы:

- `persona_rules`
- `voice_examples`
- `life_experiences`
- `style_modes`
- `memory_candidates`
- `drafts`
- `draft_versions`
- `generation_runs`

Подробный ручной rollout: [docs/PRODUCTION_ROLLOUT.md](docs/PRODUCTION_ROLLOUT.md).

## Data pipelines

```bash
# Backup production до любых изменений
python scripts/backup_supabase.py

# Проверить объём voice corpus без записи
python scripts/import_voice_examples.py --dry-run

# После применения migrations 009–010
python scripts/import_voice_examples.py

# Сначала только план knowledge cleanup
python scripts/clean_knowledge.py

# После проверки backup и плана
python scripts/clean_knowledge.py --apply

# Проверка новой production-схемы
python scripts/verify_v21.py
```

Импорт voice corpus resumable: при исчерпании Gemini quota он останавливается
без потери прогресса, а повторный запуск пропускает fingerprints, уже
сохранённые в Supabase. Для ожидания минутного лимита доступен
`--wait-on-quota`.

Book profiles остаются offline-артефактами. `seed_book_profiles.py` намеренно
ничего не записывает в identity/style, чтобы книги больше не могли загрязнить
личность автора.

## Проверки

```bash
pytest tests/ -q
python -m compileall -q src scripts
docker build -t persona-factory-bot .
```

Критерии rollout:

- `active_book_beliefs = 0`
- 4 style modes
- не менее 2400 voice examples после импорта
- 10 legacy persona rules и 6 experiences ожидают `/review`
- Railway переключается только после применения схемы и успешного `verify_v21.py`

## Studio (локальная панель, не на Railway)

Next.js-приложение в `studio/` — **только для локальной разработки**, в git и на Railway не попадает. Управляет V2.1-памятью через тот же review-путь, что и Telegram `/review`, обращаясь к FastAPI (`PERSONA_API_URL`).

### Запуск локально

```bash
# Терминал 1 — FastAPI + bot (review API)
uvicorn main:app --host 0.0.0.0 --port 7860

# Терминал 2 — studio
cd studio
cp .env.example .env.local
npm install
npm run dev
```

Откройте `http://localhost:3000`. В production задайте `STUDIO_ACCESS_SECRET` — без него панель не пускает.

### Переменные окружения studio

См. [`studio/.env.example`](studio/.env.example):

- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` — server-only, без `NEXT_PUBLIC_` для service role
- `GEMINI_API_KEY`, `GEMINI_MODEL` — комната интервью
- `STUDIO_ACCESS_SECRET` — пароль входа в панель
- `PERSONA_API_URL` — URL FastAPI (например `http://localhost:7860`)
- `STUDIO_API_SECRET` — должен совпадать с `STUDIO_API_SECRET` в корневом `.env`

### Поток review в studio

1. **Интервью** — ответы попадают в `memory_candidates` (status `pending`)
2. **Очередь Review** (`/triage`) — accept/reject/skip через `/api/v1/review/*`
3. **Матрица** (`/matrix`) — просмотр active `persona_rules` и `life_experiences`

После accept правило попадает в `persona_rules` и используется `context_builder` при генерации в боте.
