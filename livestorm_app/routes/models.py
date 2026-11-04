"""Request bodies accepted by the API."""

from __future__ import annotations

from typing import Dict, Literal

from pydantic import BaseModel, Field, field_validator

MAX_SPEAKER_LABELS = 100
MAX_SPEAKER_ID_LENGTH = 64
MAX_SPEAKER_LABEL_LENGTH = 120


class EventSessionsRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    event_id: str = Field(..., alias="eventId")


class WorkspaceEventsRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    page_number: int = Field(0, alias="pageNumber")
    page_size: int = Field(20, alias="pageSize")
    title: str = Field("", alias="title")
    scheduling_status: str = Field("", alias="schedulingStatus")


class FetchSessionRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    force_refresh: bool = Field(False, alias="forceRefresh")


class SpeakerLabelsRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    speaker_names: Dict[str, str] = Field(default_factory=dict, alias="speakerNames")

    @field_validator("speaker_names")
    @classmethod
    def _limit_speaker_names(cls, value: Dict[str, str]) -> Dict[str, str]:
        if len(value) > MAX_SPEAKER_LABELS:
            raise ValueError(f"At most {MAX_SPEAKER_LABELS} speaker labels are allowed.")
        for speaker, label in value.items():
            if len(str(speaker)) > MAX_SPEAKER_ID_LENGTH or len(str(label)) > MAX_SPEAKER_LABEL_LENGTH:
                raise ValueError(
                    f"Speaker ids are limited to {MAX_SPEAKER_ID_LENGTH} characters "
                    f"and labels to {MAX_SPEAKER_LABEL_LENGTH}."
                )
        return value


class AnalysisRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    # Each language is a separate paid job; see config.OUTPUT_LANGUAGE_MAP.
    output_language: Literal["English", "French"] = Field("English", alias="outputLanguage")


class SmartRecapRequest(BaseModel):
    api_key: str = Field("", alias="apiKey")
    # See config.SMART_RECAP_TONES.
    tone: Literal["professional", "hype", "surprise"]


class AdminPromoteRequest(BaseModel):
    email: str
    user_id: str = ""


class AdminSettingRequest(BaseModel):
    key: str
    value: str
