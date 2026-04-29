import logging

from app.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s", tool.name)

    async def execute(self, name: str, args_json: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")
        parsed_args = tool.input_model.model_validate_json(args_json)
        result = await tool.execute(parsed_args)
        return result.model_dump_json()

    def get_definitions(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]
