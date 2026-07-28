from typing import Literal

from pydantic import BaseModel, Field


class ConversationSummary(BaseModel):
    what_user_wants: str
    what_has_been_tried: list[str] = []
    current_status: str
    open_questions: list[str] = []
    suggested_next_action: str
    sentiment: Literal["positive", "neutral", "frustrated", "angry"]
    confidence: float = Field(ge=0, le=1)
