from abc import ABC, abstractmethod
from ast import UAdd
from tkinter import ANCHOR
from typing import Any
from dataclasses import dataclass, field

@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)


    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def should_excute_tools(self) -> bool:
        """
        finish_reason / stop_reason
        Anthropic: 使用tool_use表示工具调用,使用end_turn表示完成
        OpenAI: 使用tool_calls表示仅有工具调用,使用stop表示输出和模型调用均完成
        """
        if not self.has_tool_calls:
            return False
        return self.finish_reason in ("tool_calls", "stop")


class LLMProvider(ABC):

    def  __init__(
        self, 
        api_key: str | None = None,
        api_base: str | None = None,
    ):
        self.api_key = api_key
        self.api_base = api_base

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        raise NotImplementedError("Subclasses must implement this method")
    

class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "deepseek-chat",
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model

        from anthropic import AsyncAnthropic

        client_kw: dict[str, Any] = {}
        if api_key:
            client_kw["api_key"] = api_key
        if api_base:
            client_kw["base_url"] = api_base
        self._client = AsyncAnthropic(**client_kw)


    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    )->LLMResponse:
        response = await self._client.messages.create(
            model=model or self.default_model, 
            messages=messages,
            tools=tools, 
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return self._parse_response(response)

    def _parse_response(self, response: Any) -> LLMResponse:
        """
        解析Anthropic模型输出
        {
            "id": "msg_01Aq9w938a90dw8q",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-sonnet-20241022",
            "content": [
                {
                "type": "text",
                "text": "好的，我来帮你查询天气。"
                },
                {
                "type": "tool_use",
                "id": "toolu_01A09q90qw90lq917835lq92",
                "name": "get_weather",
                "input": {
                    "location": "Beijing"
                }
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": null,
            "usage": {
                "input_tokens": 250,
                "output_tokens": 100
            }
        }
        """
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        # 输出统一
        stop_map = {"tool_use": "tool_calls", "end_turn" : "stop", "max_tokens" : "length"}
        finish_reason = stop_map.get(response.stop_reason or "", response.stop_reason or "stop")

        usage: dict[str, int] = {}
        if response.usage:
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            total_tokens = input_tokens + output_tokens
            usage = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": total_tokens,
            }

        #改成统一的OpenAI格式
        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage
        )