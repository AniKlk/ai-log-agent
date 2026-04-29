from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class BaseTool(ABC):
    name: str
    description: str
    input_model: type[BaseModel]

    @abstractmethod
    async def execute(self, args: BaseModel) -> BaseModel:
        ...

    def schema(self) -> dict[str, Any]:
        json_schema = self.input_model.model_json_schema()
        # Remove $defs and title that are not needed for OpenAI function calling
        json_schema.pop("$defs", None)
        json_schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": json_schema,
            },
        }
