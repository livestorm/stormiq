import os
from pathlib import Path

API_BASE = "https://api.livestorm.co/v1"
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
LIVESTORM_OAUTH_AUTHORIZE_URL = "https://app.livestorm.co/oauth/authorize"
LIVESTORM_OAUTH_TOKEN_URL = "https://app.livestorm.co/oauth/token"
LIVESTORM_OAUTH_ME_URL = f"{API_BASE}/me"
LIVESTORM_OAUTH_DEFAULT_SCOPES = "identity:read events:read"
DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 1000
START_PAGE_NUMBER = 0

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEEP_ANALYSIS_OPENAI_MODEL = "gpt-4o-mini"
SMART_RECAP_OPENAI_MODEL = "gpt-5.4-mini"

# Anthropic (Claude) — used when LLM_PROVIDER=anthropic (the default)
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"
SMART_RECAP_CLAUDE_MODEL = "claude-sonnet-4-6"

# Cover image generation. The cover is built from the session's
# Professional Smart Recap and rendered with the OpenAI Images API.
# All three parameters can be overridden via env vars:
#   IMAGE_MODEL   — e.g. gpt-image-1, gpt-image-2 (default: gpt-image-2)
#   IMAGE_SIZE    — e.g. 1536x1024, 1024x1024      (default: 1536x1024)
#   IMAGE_QUALITY — e.g. high, medium, low          (default: high)
OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
COVER_IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-2")
COVER_IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1536x1024")
COVER_IMAGE_QUALITY = os.getenv("IMAGE_QUALITY", "high")

PROMPTS_DIR = Path("prompts")
ANALYSIS_BASE_PROMPT_PATH = PROMPTS_DIR / "analysis_base_prompt.txt"
ANALYSIS_CHAT_PROMPT_PATH = PROMPTS_DIR / "analysis_chat_prompt.txt"
ANALYSIS_QUESTIONS_PROMPT_PATH = PROMPTS_DIR / "analysis_questions_prompt.txt"
ANALYSIS_TRANSCRIPT_PROMPT_PATH = PROMPTS_DIR / "analysis_transcript_prompt.txt"
ANALYSIS_ALL_SOURCES_PROMPT_PATH = PROMPTS_DIR / "analysis_all_sources_prompt.txt"
ANALYSIS_DEEP_PROMPT_PATH = PROMPTS_DIR / "analysis_deep_prompt.txt"
CONTENT_REPURPOSE_SUMMARY_PROMPT_PATH = PROMPTS_DIR / "content_repurpose_summary_prompt.txt"
CONTENT_REPURPOSE_EMAIL_PROMPT_PATH = PROMPTS_DIR / "content_repurpose_email_prompt.txt"
CONTENT_REPURPOSE_BLOG_PROMPT_PATH = PROMPTS_DIR / "content_repurpose_blog_prompt.txt"
CONTENT_REPURPOSE_SOCIAL_MEDIA_PROMPT_PATH = PROMPTS_DIR / "content_repurpose_social_media_prompt.txt"
SMART_RECAP_PROFESSIONAL_PROMPT_PATH = PROMPTS_DIR / "smart_recap_professional_prompt.txt"
SMART_RECAP_HYPE_PROMPT_PATH = PROMPTS_DIR / "smart_recap_hype_prompt.txt"
SMART_RECAP_SURPRISE_PROMPT_PATH = PROMPTS_DIR / "smart_recap_surprise_prompt.txt"
COVER_IMAGE_PROMPT_PATH = PROMPTS_DIR / "cover_image_prompt.txt"

ENV_PATH = Path(".env")
BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
ICONS_DIR = ASSETS_DIR / "icons"

OUTPUT_LANGUAGE_MAP = {
    "English": "English",
    "French": "French",
}


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
