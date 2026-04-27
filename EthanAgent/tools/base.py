from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Dict, Any, Callable

class Tool(ABC):
    """工具基类"""
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calls."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the tool does."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""
        ...
        
    @property
    def exclusive(self) -> bool:
        """Whether this tool should run alone even if concurrency is enabled."""
        return False
    
    @property
    def read_only(self) -> bool:
        """Whether this tool is side-effect free and safe to parallelize."""
        return False
    
    @property
    def concurrency_safe(self) -> bool:
        return self.read_only and not self.exclusive

    @abstractmethod
    async def run(self, **kwargs: Any) -> Any:
        """执行工具"""
        pass

    def to_schema(self) -> dict[str, Any]:
        """Anthropic tool use schema."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


def tool_parameters(schema: dict[str, Any]) -> Callable[[type[Tool]], type[Tool]]:

    def derector(cls: type[Tool]) -> type[Tool]:
        frozen = deepcopy(schema)

        @property
        def parameters(self: Any) -> dict[str, Any]:
            return deepcopy(frozen)
        
        cls.parameters = parameters

        abstract = getattr(cls, "__abstractmethods__", None)
        if abstract is not None and "parameters" in abstract:
            cls.__abstractmethods__ = frozenset(abstract - {"parameters"})

        return cls
    
    return derector