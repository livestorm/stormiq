# External providers

The services StormIQ calls, how models are chosen, and the timeout and retry rules for every outbound call.

| Service | Used for | Code | Credentials |
|---|---|---|---|
| **Livestorm REST API** | session, event, chat, questions, events list | [`services.livestorm_get`](../livestorm_app/services.py) | the user's OAuth token (or `LS_API_KEY` in local-dev mode) |
| **Livestorm OAuth** | sign-in, token refresh, `/me` | [`oauth_client.py`](../livestorm_app/oauth_client.py) | OAuth client id and secret |
| **Gladia v2 (pre-recorded)** | transcription with diarization, named entities, sentences and subtitles; the transcriber also fetches the session's recordings from Livestorm | [`gladia/transcriber.py`](../livestorm_app/gladia/transcriber.py) | `GLADIA_KEY` (worker only) |
| **Anthropic** (default) | overall and deep analysis, Smart Recap, content repurposing, translation | [`llm_client.py`](../livestorm_app/llm_client.py) | `CLAUDE_API_KEY` |
| **OpenAI Chat Completions** (optional) | the same flows, when the provider is `openai` | [`llm_client.py`](../livestorm_app/llm_client.py) | `OPENAI_API_KEY` |
| **OpenAI Images** | cover images, whatever the text provider | [`services.py`](../livestorm_app/services.py) | `OPENAI_API_KEY` |

## Choosing the provider and models

Each value is resolved in this order: the admin setting in `system_settings` (editable at `/admin`), then the env var, then the default in [`config.py`](../livestorm_app/config.py).

| What | Admin setting | Env var | Default |
|---|---|---|---|
| Text provider | `llm_provider` | `LLM_PROVIDER` | `anthropic` |
| Model for analysis, content and translation | `llm_default_model` | — | `claude-haiku-4-5-20251001` (Anthropic), `gpt-4o-mini` (OpenAI) |
| Model for Smart Recap | `llm_smart_recap_model` | — | `claude-sonnet-4-6` (Anthropic), `gpt-5.4-mini` (OpenAI) |
| Image model | `image_model` | `IMAGE_MODEL` | `gpt-image-2` |

Smart Recap deliberately uses a stronger model than the other flows. Image size and quality come from `IMAGE_SIZE` (default `1536x1024`) and `IMAGE_QUALITY` (default `high`). Switching provider needs the matching key on the worker.

## Timeouts and retries

Every outbound call has a timeout. Retries are limited to failures that almost always mean nothing was produced or billed. (A gateway 502/504 or a dropped connection can occasionally hide a billed LLM answer; that rare double charge is accepted over failing the job.)

| Call | Timeout | Retried on | Not retried |
|---|---|---|---|
| LLM (`llm_client._post_with_retry`) | 10 s connect, 300 s read | 429, 500, 502, 503, 504, 529 and connection errors; up to 4 attempts, exponential backoff with jitter, `Retry-After` honoured (capped at 30 s) | read timeouts (the model may still be generating, and billing, that answer); other 4xx |
| Livestorm API (`services.livestorm_get`, all GETs) | 10 s connect, 30 s read | 429, 5xx and connection errors; 3 retries, backoff 0/1/2 s, `Retry-After` capped at 10 s | other 4xx |
| Gladia JSON calls (`transcriber._urlopen_json`) | 60 s socket | status polls ride out up to 4 consecutive timeouts, 429s or 5xx; the Livestorm recordings GET gets up to 3 attempts | starting a transcription (a POST: a retry could pay twice); 4xx |
| Gladia upload, recording download | 120 s, 60 s socket | — | — |
| Livestorm OAuth (token exchange, refresh, `/me`) | 60 s | — | — |
| OpenAI Images (cover) | 180 s | — | — |

The Livestorm HTTP session is shared by every user's requests, so it keeps no cookies.

## Gladia configuration

`DEFAULT_GLADIA_OPTIONS` in [`gladia/transcriber.py`](../livestorm_app/gladia/transcriber.py):

- diarization on, with 1 to 20 speakers;
- named-entity recognition on;
- sentence segmentation on;
- subtitles on, in SRT and VTT.

Gladia's standard plan accepts up to 135 minutes of audio per request, so longer recordings are split into 135-minute chunks (`_split_audio_file`), each transcribed as its own Gladia job, and the results are combined into one transcript. For debugging, `python -m livestorm_app.gladia.cli <session_id>` transcribes a Livestorm session from the command line and writes the JSON locally. It uses the keys in your environment and costs Gladia credit.
