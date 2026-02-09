from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal, Optional

class RollRequest(BaseModel):
    expr: str = Field(..., description="ex: d20+2, d6, 2d6+1")
    reason: str = Field("", description="Why this roll exists (optional).")

class Plan(BaseModel):
    type: Literal["talk", "act", "travel", "combat", "idle"] = "idle"
    target: Optional[str] = Field(None, description="NPC target if any (ex: Ataryxia)")
    intent: str = Field("", description="Short intent in French.")
    rolls: list[RollRequest] = Field(default_factory=list)
    narration_hooks: list[str] = Field(default_factory=list)
    state_patch: dict = Field(default_factory=dict)

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

    system: Optional[str] = None
