from typing import Any
from .base import Tool

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None
    
    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def has(self, name: str) -> bool:
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """
        获取所有工具的描述,按照工具名排序,本地工具在前,MCP工具在后
        结果会缓存
        """
        if self._cached_definitions is not None:
            return self._cached_definitions
        
        definitions = [tool.to_schema() for tool in self._tools.values()]
        local_tools: list[dict[str, Any]] = []
        mcp_tools: list[dict[str, Any]] = []
        for schema in definitions:
            name = schema.get("name")
            if name.startswith("mcp_"):
                mcp_tools.append(schema)
            else:
                local_tools.append(schema)
        local_tools.sort(key=lambda x: x.get("name"))
        mcp_tools.sort(key=lambda x: x.get("name"))
        self._cached_definitions = local_tools + mcp_tools
        return self._cached_definitions

    def prepare_before_call(self, name: str, params: dict[str, Any]) -> tuple[Tool | None, dict[str, Any], str | None]:
        """
        准备调用前做检查和转换
        """
        # 如果读写文件参数不是字典,则返回错误
        if not isinstance(params, dict) and name in ('write_file', 'read_file'):
            return None, params,f"Error: Tool '{name}' parameters must be a JSON object, got {type(params).__name__}. Use named parameters: tool_name(param1=\"value1\", param2=\"value2\")"
    

        #获取工具
        tool = self._tools.get(name)
        if not tool:
            return None, params, (
                f"Error: Tool '{name}' not found. Available: {', '.join(self._tools.keys())}"
            )
        return tool, params, None

    
    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        """
        Registry执行某个工具
        """
        _ERROR_HINT = "\n\n[分析错误，并尝试使用不同的方法解决]"
        tool , params, error = self.prepare_before_call(name, params)
        if error: return error + _ERROR_HINT

        try:
            assert tool is not None
            result = await tool.run(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + _ERROR_HINT
            return result

        except Exception as e:
            return f"Error executing {name}: {str(e)}" + _ERROR_HINT


    def tool_names(self) -> list[str]:
        """
        获取所有工具名
        """
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools