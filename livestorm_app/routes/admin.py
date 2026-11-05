"""Admin routes: users and admins, cached sessions across orgs, model settings."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from livestorm_app.ai.jobs import InvalidModelSetting, check_model_setting, is_model_setting
from livestorm_app.auth import (
    Caller,
    env_admin_emails,
    require_admin,
)
from livestorm_app.db import (
    admin_delete_session,
    admin_list_sessions,
    admin_list_users,
    demote_admin,
    get_all_settings,
    promote_admin,
    set_setting,
)
from livestorm_app.routes.common import SessionId
from livestorm_app.routes.models import AdminPromoteRequest, AdminSettingRequest

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


@router.get("/api/admin/settings")
def admin_get_settings(_caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    return {"settings": get_all_settings()}


# Settings saved as given (non-empty). `llm_provider` is checked where it is
# read: llm_client.get_provider ignores any value but anthropic or openai.
# `image_model` is sent as saved, except that an empty or retired id becomes
# gpt-image-2 (cover_image.resolve_image_model).
_PLAIN_SETTING_KEYS = {"llm_provider", "image_model"}


@router.post("/api/admin/settings")
def admin_update_setting(body: AdminSettingRequest, _caller: Caller = Depends(require_admin)) -> Dict[str, Any]:
    key = str(body.key or "").strip()
    if key not in _PLAIN_SETTING_KEYS and not is_model_setting(key):
        raise HTTPException(status_code=400, detail={"resource": "Setting", "message": f"Unknown setting key: {key}"})
    value = str(body.value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail={"resource": "Setting", "message": "Value cannot be empty."})
    if is_model_setting(key):
        # Model ids come from the catalog (ai/models.py), per job (ai/jobs.py).
        try:
            check_model_setting(key, value)
        except InvalidModelSetting as exc:
            raise HTTPException(status_code=400, detail={"resource": "Setting", "message": str(exc)}) from exc
    set_setting(key, value)
    return {"ok": True, "key": key, "value": value}
