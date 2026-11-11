"""Admin routes: users and admins, cached sessions across orgs, organizations' switches, model settings."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from livestorm_app.ai.jobs import InvalidModelSetting, check_model_setting, is_model_setting
from livestorm_app.ai.surprise_voice import TTS_VOICES, VOICE_PROVIDERS
from livestorm_app.ai.voice_elevenlabs import VOICE_ID_PATTERN
from livestorm_app.auth import (
    Caller,
    env_admin_emails,
    require_admin,
)
from livestorm_app.db import (
    admin_delete_session,
    admin_list_organizations,
    admin_list_sessions,
    admin_list_users,
    demote_admin,
    get_all_settings,
    promote_admin,
    save_surprise_enabled,
    set_setting,
)
from livestorm_app.routes.common import SessionId
from livestorm_app.routes.models import AdminPromoteRequest, AdminSettingRequest, OrgSettingRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/admin/check")
def admin_check(caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    return {"isAdmin": True, "email": (caller.connection or {}).get("email", "")}


@router.get("/api/admin/users")
def admin_get_users(_caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    return {"users": admin_list_users()}


@router.post("/api/admin/users/promote")
def admin_promote_user(body: AdminPromoteRequest, caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    actor_email = str((caller.connection or {}).get("email") or "").strip()
    promote_admin(body.email.strip(), body.user_id.strip(), actor_email)
    return {"ok": True}


@router.post("/api/admin/users/demote")
def admin_demote_user(body: AdminPromoteRequest, caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    actor_email = str((caller.connection or {}).get("email") or "").strip().lower()
    target_email = str(body.email or "").strip().lower()
    if target_email in env_admin_emails():
        raise HTTPException(status_code=400, detail={"resource": "Admin", "message": "Cannot demote a bootstrap admin set via ADMIN_EMAILS env var."})
    if target_email == actor_email:
        raise HTTPException(status_code=400, detail={"resource": "Admin", "message": "You cannot demote yourself."})
    demote_admin(target_email)
    return {"ok": True}


@router.get("/api/admin/sessions")
def admin_get_sessions(_caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    return {"sessions": admin_list_sessions()}


@router.delete("/api/admin/sessions/{session_id}")
def admin_remove_session(session_id: SessionId, _caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    admin_delete_session(session_id)
    return {"ok": True, "sessionId": session_id}


@router.get("/api/admin/organizations")
def admin_get_organizations(_caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    """Every organization with a cached session or a setting, with its
    switches (Surprise Me!)."""
    try:
        rows = admin_list_organizations()
    except Exception as exc:
        logger.exception("Admin organizations read failed")
        raise HTTPException(status_code=500, detail={"resource": "Organizations", "message": "The organizations could not be read."}) from exc
    return {
        "organizations": [
            {
                "organizationId": row["organization_id"],
                "sessions": int(row["sessions"] or 0),
                "lastSessionAt": row["last_session_at"].isoformat() if row.get("last_session_at") else None,
                "surpriseEnabled": bool(row["surprise_enabled"]),
                "updatedBy": row.get("updated_by_email"),
                "updatedAt": row["updated_at"].isoformat() if row.get("updated_at") else None,
            }
            for row in rows
        ]
    }


@router.post("/api/admin/organizations/settings")
def admin_update_organization(body: OrgSettingRequest, caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    """Turn Surprise Me! on or off for one organization."""
    org = str(body.organizationId or "").strip()
    if not org:
        raise HTTPException(status_code=400, detail={"resource": "Organizations", "message": "An organization id is required."})
    connection = caller.connection if isinstance(caller.connection, dict) else {}
    try:
        save_surprise_enabled(org, body.surpriseEnabled, updated_by_email=str(connection.get("email") or "") or None)
    except Exception as exc:
        logger.exception("Admin organization setting failed")
        raise HTTPException(status_code=500, detail={"resource": "Organizations", "message": "The setting could not be saved."}) from exc
    return {"ok": True, "organizationId": org, "surpriseEnabled": body.surpriseEnabled}


@router.get("/api/admin/settings")
def admin_get_settings(_caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    return {"settings": get_all_settings()}


# Settings saved as given (non-empty). There is no `llm_provider` any more:
# every text job runs on Claude. Surprise me's voice: `voice_provider` is
# `elevenlabs` (the main voice, OpenAI its backup) or `openai`
# (`surprise_voice.VOICE_PROVIDERS`); `elevenlabs_voice` is an ElevenLabs
# voice id (`voice_elevenlabs.VOICE_ID_PATTERN`); `tts_voice` must be one of
# the voices gpt-4o-mini-tts offers (`surprise_voice.TTS_VOICES`).
_PLAIN_SETTING_KEYS = {"tts_voice", "voice_provider", "elevenlabs_voice"}


@router.post("/api/admin/settings")
def admin_update_setting(body: AdminSettingRequest, _caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    key = str(body.key or "").strip()
    if key not in _PLAIN_SETTING_KEYS and not is_model_setting(key):
        raise HTTPException(status_code=400, detail={"resource": "Setting", "message": f"Unknown setting key: {key}"})
    value = str(body.value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail={"resource": "Setting", "message": "Value cannot be empty."})
    if key == "tts_voice":
        value = value.lower()
        if value not in TTS_VOICES:
            raise HTTPException(
                status_code=400,
                detail={"resource": "Setting", "message": f"Unknown voice {value!r}. Use one of: {', '.join(TTS_VOICES)}."},
            )
    if key == "voice_provider":
        value = value.lower()
        if value not in VOICE_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail={"resource": "Setting", "message": f"Unknown voice provider. Use one of: {', '.join(VOICE_PROVIDERS)}."},
            )
    if key == "elevenlabs_voice" and not re.match(VOICE_ID_PATTERN, value):
        raise HTTPException(
            status_code=400,
            detail={"resource": "Setting", "message": "An ElevenLabs voice id is 10 to 40 letters and digits."},
        )
    if is_model_setting(key):
        # Model ids come from the catalog (ai/models.py), per job (ai/jobs.py).
        try:
            check_model_setting(key, value)
        except InvalidModelSetting as exc:
            raise HTTPException(status_code=400, detail={"resource": "Setting", "message": str(exc)}) from exc
    set_setting(key, value)
    return {"ok": True, "key": key, "value": value}
