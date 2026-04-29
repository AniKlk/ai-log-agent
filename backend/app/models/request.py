from pydantic import BaseModel, Field


class ConversationMessage(BaseModel):
    role: str = Field(..., description="Message role: user or assistant")
    content: str = Field(..., description="Message content")


class AnalyzeRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    conversation_history: list[ConversationMessage] = Field(
        default_factory=list,
        description="Prior conversation messages for follow-up context",
    )
