from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal, Optional


class RollRequest(BaseModel):
    expr: str = Field(..., description="ex: d20+2, d6, 2d6+1")
    reason: str = Field("", description="Why this roll exists (optional).")


class ChoiceOption(BaseModel):
    id: str
    text: str
    risk_tag: str = ""
    effects_hint: str = ""
    state_patch: dict = Field(default_factory=dict)


class Plan(BaseModel):
    type: Literal["talk", "act", "travel", "combat", "idle"] = "idle"
    target: Optional[str] = Field(None, description="NPC target if any (ex: Ataryxia)")
    intent: str = Field("", description="Short intent in French.")
    rolls: list[RollRequest] = Field(default_factory=list)
    narration_hooks: list[str] = Field(default_factory=list)
    state_patch: dict = Field(default_factory=dict)
    decision_type: Literal["dialogue", "combat", "event", "choice"] = "dialogue"
    tension_delta: int = 0
    morale_delta: int = 0
    corruption_delta: int = 0
    attraction_delta: int = 0
    output_type: Literal["choice_required", "dialogue", "event"] = "dialogue"
    choices: list[ChoiceOption] = Field(default_factory=list)
    options: list[ChoiceOption] = Field(default_factory=list)
    event_text: str = ""
    event_state_patch: dict = Field(default_factory=dict)


class RollResult(BaseModel):
    expr: str
    total: int
    detail: str = ""


class TurnResult(BaseModel):
    mode: Literal["auto", "debug"] = "auto"
    model_used: Optional[str] = None

    narration: Optional[str] = None
    speaker: Optional[str] = None
    dialogue: Optional[str] = None

    plan: Optional[Plan] = None
    rolls: list[RollResult] = Field(default_factory=list)
    output_type: Literal["choice_required", "dialogue", "event"] = "dialogue"
    options: list[ChoiceOption] = Field(default_factory=list)
    event_text: Optional[str] = None

    system: Optional[str] = None
    media_keyword: Optional[str] = None
    generated_image_prompt: Optional[str] = None
