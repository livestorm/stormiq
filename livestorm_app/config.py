import os
from pathlib import Path

API_BASE = "https://api.livestorm.co/v1"
LIVESTORM_OAUTH_AUTHORIZE_URL = "https://app.livestorm.co/oauth/authorize"
LIVESTORM_OAUTH_TOKEN_URL = "https://app.livestorm.co/oauth/token"
LIVESTORM_OAUTH_ME_URL = f"{API_BASE}/me"
LIVESTORM_OAUTH_DEFAULT_SCOPES = "identity:read events:read"
# Livestorm's developer docs ("Pagination") give 50 as the maximum page
# size. Some endpoints accept more today; the pagers keep to the documented
# limit and never ask for more.
LIVESTORM_MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = LIVESTORM_MAX_PAGE_SIZE
MAX_PAGES = 1000
START_PAGE_NUMBER = 0

# Anthropic (Claude): every text job (the owner's decision, 2026-11-11).
# Text model ids live in ai/models.py, each job's default in ai/jobs.py.
# The SDK client (llm_client) is given this base URL, so ANTHROPIC_BASE_URL in
# the environment can't send the key elsewhere.
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
# The API version the `anthropic` SDK sends: 1.x sends 2023-06-01, the last
# version that takes `temperature` (llm_client.anthropic_accepts_temperature
# reads it). tests/test_llm_transport.py checks the SDK sends this one.
ANTHROPIC_API_VERSION = "2023-06-01"

# Cover image generation. The cover is built from the session's
# Professional Smart Recap and rendered with the OpenAI Images API.
# All three parameters can be overridden via env vars:
#   IMAGE_MODEL   — e.g. gpt-image-2 (default: gpt-image-2; gpt-image-1 was shut down)
#   IMAGE_SIZE    — e.g. 1536x1024, 1024x1024      (default: 1536x1024)
#   IMAGE_QUALITY — e.g. high, medium, low          (default: high)
OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
COVER_IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-2")
COVER_IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1536x1024")
COVER_IMAGE_QUALITY = os.getenv("IMAGE_QUALITY", "high")

PROMPTS_DIR = Path("prompts")
ANALYSIS_DEEP_PROMPT_PATH = PROMPTS_DIR / "analysis_deep_prompt.txt"
CONTENT_REPURPOSE_BUNDLE_PROMPT_PATH = PROMPTS_DIR / "content_repurpose_bundle_prompt.txt"
SMART_RECAP_PROFESSIONAL_PROMPT_PATH = PROMPTS_DIR / "smart_recap_professional_prompt.txt"
SMART_RECAP_HYPE_PROMPT_PATH = PROMPTS_DIR / "smart_recap_hype_prompt.txt"
SMART_RECAP_SURPRISE_PROMPT_PATH = PROMPTS_DIR / "smart_recap_surprise_prompt.txt"
COVER_IMAGE_PROMPT_PATH = PROMPTS_DIR / "cover_image_prompt.txt"
CHAT_LABELS_PROMPT_PATH = PROMPTS_DIR / "chat_labels.txt"  # v1: ai/chat_labelling.py
UNDERSTANDING_PROMPT_PATH = PROMPTS_DIR / "understanding.txt"  # v1: ai/understanding.py
SESSION_ANALYSIS_PROMPT_PATH = PROMPTS_DIR / "session_analysis.txt"  # v1: ai/session_analysis.py
FAQ_PROMPT_PATH = PROMPTS_DIR / "faq.txt"  # v1: ai/faq.py
DECISIONS_PROMPT_PATH = PROMPTS_DIR / "decisions.txt"  # v1: ai/decisions.py
TEAM_DIGEST_PROMPT_PATH = PROMPTS_DIR / "team_digest.txt"  # v1: ai/team_digest.py
# v1.1 public formats (`ai/public_formats.py`): `prompts/<kind>.txt`.
PUBLIC_FORMAT_PROMPT_PATHS = {
    "attendee_followup": PROMPTS_DIR / "attendee_followup.txt",
}

ENV_PATH = Path(".env")

OUTPUT_LANGUAGE_MAP = {
    "English": "English",
    "French": "French",
}

# Each distinct language or tone is a separate paid LLM job, so requests
# outside these sets are rejected (routes/models.py + api_logic).
SMART_RECAP_TONES = ("professional", "hype", "surprise")


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_runtime_secret(name: str, default: str = "") -> str:
    return os.getenv(name, default)
