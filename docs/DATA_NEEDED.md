# Данные и доступы для V2.1

## Уже доступны локально

- Telegram export: 2448 очищенных сообщений после repair повреждённого JSON.
- 62 ручных поста канала.
- 2386 сообщений переписок; используются только для голоса.
- Книжный knowledge corpus и profiles.
- Railway, Telegram, Gemini/Groq, Cohere и Supabase credentials.

## Что требует ручного действия владельца

1. Применить Supabase migrations 009 и 010.
2. Импортировать voice corpus.
3. Проверить и применить knowledge cleanup.
4. Выполнить `/review` для legacy persona rules и experiences.
5. Проверять религиозные knowledge-чанки вручную перед переводом в `verified`.
6. Только после этих шагов переключить Railway на V2.1.

Полная последовательность: [PRODUCTION_ROLLOUT.md](PRODUCTION_ROLLOUT.md).

## Обязательные environment variables

- `MY_USER_ID`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHANNEL_ID`
- `CHANNEL_SYNC_ENABLED`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `GEMINI_API_KEY`
- `GEMINI_API_KEY_FALLBACK`
- `GROQ_API_KEY`
- `COHERE_API_KEY`

V2.1 также использует:

- `CONTEXT_MAX_CHARS`
- `PERSONA_RULE_LIMIT`
- `VOICE_EXAMPLE_LIMIT`
- `EXPERIENCE_LIMIT`
