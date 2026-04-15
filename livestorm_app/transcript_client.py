from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Dict, Optional

from livestorm_app.gladia.transcriber import DEFAULT_MODEL, transcribe_livestorm_session_data


def fetch_session_transcript(
    gladia_api_key: str,
    session: str,
    *,
    livestorm_api_key: str,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    with TemporaryDirectory(prefix="gladia-session-transcript-") as temp_dir:
        payload = transcribe_livestorm_session_data(
            session_id=session,
            output_path=Path(temp_dir) / f"{session}.transcript.json",
            provider=DEFAULT_MODEL,
            gladia_api_key=gladia_api_key,
            livestorm_api_key=livestorm_api_key,
            on_progress=on_progress,
        )
    payload.pop("output_path", None)
    return payload
