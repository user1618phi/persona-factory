# The Persona Factory

[![CI](https://github.com/user1618phi/persona-factory/actions/workflows/ci.yml/badge.svg)](https://github.com/user1618phi/persona-factory/actions/workflows/ci.yml) ![Python 3.12](https://img.shields.io/badge/python-3.12-blue) ![License MIT](https://img.shields.io/badge/license-MIT-green)

A private Telegram editor that writes in one person's voice, and refuses to invent the rest.

You send it a rough text or a voice note. It returns one draft in your voice, grounded in three things it is allowed to know: **confirmed persona rules**, **real examples of how you write**, and **verified experiences**. Anything it has not been told stays out of the draft. New facts about you only enter the system after you review them.

This is an LLM product designed primarily around what it must not fabricate. The interesting part is not the generation; it is the set of constraints.

Russian version of this README: [`docs/README.ru.md`](docs/README.ru.md). Design document: [`thePersonalFactory.md`](thePersonalFactory.md).

## Guarantees

1. **Books are not the author's personality.** Books are knowledge chunks the draft may cite, never a source of identity, beliefs or voice.
2. **Chat exports are language samples, never biography.** They teach the model how you phrase things, not what happened to you.
3. **New facts and permanent style rules activate only through `/review`.** The learning loop produces candidates; nothing becomes identity without a human click.
4. **Religious quotes only from `verified` knowledge chunks.** The bot never adds a hadith or an ayah from model memory.
5. **One main draft per LLM call.** No hidden multi-sampling, no silent regeneration.
6. **A final preview is mandatory before publishing.**
7. **`publish_token` makes publishing idempotent.** A draft cannot be posted to the channel twice.

## How it works

```
Telegram (aiogram)                     Supabase Postgres + pgvector
 ├─ text or voice note ──► Groq Whisper ──► text
 ├─ /review queue                       persona_rules · voice_examples
 └─ preview ──► publisher ──► channel    life_experiences · knowledge_chunks
        │                                        ▲
        ▼                                        │ vector RPCs, recency-reranked
 orchestrator                                    │
 ├─ context_builder   bounded context, strict memory-layer separation
 ├─ retrievers        similarity · exp(-λ · age_days)
 ├─ llm_router        LiteLLM: Gemini → Gemini fallback → Groq → Cohere → OpenRouter
 ├─ brainstorm        one draft, controlled rewrites (my_voice · clear · storytelling · show_your_work)
 ├─ learning          candidate-only: proposes rules and facts, never activates them
 ├─ conflict_resolver newer belief records deprecate older contradictory ones
 └─ publisher         idempotent publishing with publish_token

Ingest (separate image, heavy deps)
 ├─ Telegram export ──► data_cleaner ──► semantic chunking (multilingual MiniLM) ──► Gemini embeddings
 ├─ books (PDF) ──► marker ──► chunks ──► Cohere multilingual embeddings ──► knowledge only
 └─ YouTube ──► yt-dlp captions / audio ──► grounded briefing (see YOUTUBE_INGEST_ARCHITECTURE.md)
```

**Memory layers.** Persona rules (how to write), voice examples (how the author actually writes), life experiences (what happened, with the choice and the lesson), knowledge chunks (books and sources, with a `verified` flag), and regrets. The context builder assembles a bounded context from these layers separately, so a book paragraph can never be mistaken for a belief.

**Retrieval.** Vector search in Supabase (pgvector RPCs) reranked by recency: `score = similarity × exp(-λ · age_days)`. Newer rules and examples win ties; old ones fade rather than disappear.

**LLM routing.** LiteLLM with an ordered fallback chain and a primary plus fallback key per provider. Task-aware preference: cheap and fast for rewrites, larger context for briefings.

**Voice.** Telegram voice notes are transcribed with Groq Whisper (`whisper-large-v3-turbo`, Russian) before entering the same pipeline as text.

**Studio API.** A small FastAPI surface (`src/api`) for a private Next.js control panel that shows stats and the review queue. The panel itself is not in this repository.

## Repository

```
main.py                 FastAPI + bot entrypoint (health, studio API, Telegram)
src/bot/                handlers, keyboards, owner auth, session store, channel sync, /status
src/orchestrator/       context builder, retrievers glue, LLM router, learning loop, publisher, transcriber, YouTube ingest
src/db/                 Supabase client, memory repository, vector retrievers
src/embeddings/         Gemini and Cohere embedding clients (REST, key fallback)
scripts/                ingest pipeline: cleaning, chunking, book processing, seeding, backups, verification
supabase/migrations/    16 SQL migrations, RLS and RPCs, validated in tests with pglast
tests/                  116 tests, all network calls mocked
Dockerfile              slim production bot (requirements-bot.txt, no torch)
Dockerfile.ingest       heavy ingest image (marker, torch, sentence-transformers)
```

The dependency split is deliberate: `requirements-bot.txt` is what runs in production and stays small; `requirements.txt` is the full local ingest stack. Railway deploys the slim image with a `/health` check.

## Running locally

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-bot.txt -r requirements-dev.txt
cp .env.example .env            # fill in Telegram, Supabase and at least one LLM key
pytest tests/ -q                # runs without any real credentials
uvicorn main:app --host 0.0.0.0 --port 7860
```

Ingest (books, chat exports, voice examples) needs the full stack:

```bash
pip install -r requirements.txt
python run_pipeline.py --help
```

## Honest notes

- Built for one person. The system prompts address the author by name and the bot answers only its owner (`MY_USER_ID`). Making it multi-tenant would mean templating the prompts and moving the owner check into per-user config.
- The data is not here. Chat exports, books, embeddings and database backups are ignored by git and never entered history.
- The design document and the Russian README predate the English one and are the more detailed source.

## License

MIT.
