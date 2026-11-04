"""Test bootstrap.

Runs before any test module imports `app`. `load_env_file()` never
overrides variables that are already set, so blanking these keeps the real
`.env` (DATABASE_URL, API keys, local-dev flags) out of the test process:
no test can reach a real database, Redis, or upstream API by accident.
"""

import os
import sys
from pathlib import Path

for _name in (
    "DATABASE_URL",
    "POSTGRES_URL",
    "RENDER_POSTGRES_URL",
    "LS_API_KEY",
    "STORMIQ_LOCAL_DEV",
    "ADMIN_EMAILS",
    "RENDER",
    "GLADIA_KEY",
    "CLAUDE_API_KEY",
    "OPENAI_API_KEY",
    "SESSION_SECRET",
    "LIVESTORM_OAUTH_CLIENT_ID",
    "LIVESTORM_OAUTH_CLIENT_SECRET",
    "LIVESTORM_OAUTH_REDIRECT_URI",
):
    os.environ[_name] = ""
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
