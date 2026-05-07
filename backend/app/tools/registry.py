import logging
import time
import json

from app.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self, cacheable_tool_names: list[str] | None = None, cache_ttl_seconds: int = 0) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._cacheable_tool_names = set(cacheable_tool_names or [])
        self._cache_ttl_seconds = max(0, cache_ttl_seconds)
        self._result_cache: dict[tuple[str, str], tuple[float, str]] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s", tool.name)

    @staticmethod
    def _canonicalize_args(args_json: str) -> str:
        try:
            return json.dumps(json.loads(args_json), sort_keys=True, separators=(",", ":"))
        except Exception:
            return args_json

    async def execute(self, name: str, args_json: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")

        cache_key: tuple[str, str] | None = None
        if self._cache_ttl_seconds > 0 and name in self._cacheable_tool_names:
            canonical_args = self._canonicalize_args(args_json)
            cache_key = (name, canonical_args)
            cached = self._result_cache.get(cache_key)
            if cached is not None:
                expires_at, cached_result = cached
                if time.monotonic() < expires_at:
                    logger.info("Tool cache hit: %s", name)
                    return cached_result
                self._result_cache.pop(cache_key, None)

        parsed_args = tool.input_model.model_validate_json(args_json)
        result = await tool.execute(parsed_args)
        result_json = result.model_dump_json()

        if cache_key is not None:
            self._result_cache[cache_key] = (time.monotonic() + self._cache_ttl_seconds, result_json)

        return result_json

    def get_definitions(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]
