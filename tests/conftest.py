"""Environment defaults so the suite runs without a real .env (CI, fresh clones).

Values are placeholders: unit tests mock every network call. setdefault keeps an
explicit shell override, and load_dotenv() never overrides existing variables, so
a developer's private .env cannot leak identifiers into assertions.
"""

from __future__ import annotations

import os

_DEFAULTS = {
    "MY_USER_ID": "123456789",
    "MY_TELEGRAM_FROM_ID": "user123456789",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
    "TELEGRAM_BOT_TOKEN": "123456:test-token",
    "TELEGRAM_CHANNEL_ID": "-1001234567890",
    "STUDIO_API_SECRET": "test-studio-secret",
    # scripts/lib/project_runtime: do not re-exec into .venv while collecting tests
    "PERSONA_SKIP_VENV_CHECK": "1",
}

for _key, _value in _DEFAULTS.items():
    os.environ.setdefault(_key, _value)
