from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass, field
from loguru import logger
from typing import Callable, Awaitable
import asyncio

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
    def should_execute_tools(self) -> bool:
        """
        finish_reason / stop_reason
        Anthropic: 使用tool_use表示工具调用,使用end_turn表示完成
        OpenAI: 使用tool_calls表示仅有工具调用,使用stop表示输出和模型调用均完成
        """
        if not self.has_tool_calls:
            return False
        return self.finish_reason in ("tool_calls", "stop")



class LLMProvider(ABC):
    _CHAT_RETRY_DELAYS = (1, 2, 4)
    _PERSISTENT_MAX_DELAY = 60
    _PERSISTENT_IDENTICAL_ERROR_LIMIT = 10
    _RETRY_HEARTBEAT_CHUNK = 30


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
        max_tokens: int = 10000,
        temperature: float = 0.7,
    ) -> LLMResponse:
        raise NotImplementedError("Subclasses must implement this method")
    
    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 10000,
        temperature: float = 0.7,
        retry_mode: str = "standard",
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:

        kw: dict[str, Any] = dict(
            messages=messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
        )
        return await self._run_with_retry(
            self._safe_chat,
            kw,
            messages,
            retry_mode=retry_mode,
            on_retry_wait=on_retry_wait,
        )

    async def _safe_chat(self, **kwargs: Any) -> LLMResponse:
        """Call chat() and convert unexpected exceptions to error responses."""
        try:
            return await self.chat(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return LLMResponse(content=f"Error calling LLM: {exc}", finish_reason="error")


    async def _run_with_retry(
        self, 
        call: Callable[..., Awaitable[LLMResponse]],
        kw: dict[str, Any],
        *,
        retry_mode: str,
    ) -> LLMResponse:
        attempt = 0
        delays = list(self._CHAT_RETRY_DELAYS)
        persistent = retry_mode == "persistent"
        last_response: LLMResponse | None = None
        last_error_key: str | None = None
        identical_error_count = 0
        while True:
            attempt += 1
            response = await call(**kw)
            if response.finish_reason != "error":
                return response
            last_response = response
            error_key = ((response.content or "").strip().lower() or None)
            if error_key and error_key == last_error_key:
                identical_error_count += 1
            else:
                last_error_key = error_key
                identical_error_count = 1 if error_key else 0
            
            if persistent and identical_error_count >= self._PERSISTENT_IDENTICAL_ERROR_LIMIT:
                logger.warning(
                    "Stopping persistent retry after {} identical transient errors: {}",
                    identical_error_count,
                    (response.content or "").strip().lower()[:120],
                )
                return response
            
            if not persistent and attempt > len(delays):
                logger.warning(
                    "LLM request failed after {} retries, giving up: {}",
                    attempt,
                    (response.content or "").strip().lower()[:120],
                )
                break
            
            base_delay = delays[min(attempt - 1, len(delays) - 1)]
            delay = base_delay
            if persistent:
                delay = min(delay, self._PERSISTENT_MAX_DELAY)
            
            logger.warning(
                "LLM transient error (attempt {}{}), retrying in {}s: {}",
                attempt,
                "+" if persistent and attempt > len(delays) else f"/{len(delays)}",
                int(round(delay)),
                (response.content or "").strip().lower()[:120],
            )

            await asyncio.sleep(delay)


        return last_response if last_response is not None else await call(**kw)



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
        max_tokens: int = 10000,
        temperature: float = 0.7,
    )->LLMResponse:

        system, anthropic_msgs = self._convert_messages(messages)
        response = await self._client.messages.create(
            model=model or self.default_model, 
            messages=anthropic_msgs,
            tools=tools, 
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if response is None:
            logger.error(
                "Anthropic messages.create returned None (model={})",
                model or self.default_model,
            )
            return LLMResponse(
                content=None,
                tool_calls=[],
                finish_reason="error",
                usage={},
            )
        return self._parse_response(response)

    def _convert_messages(self, messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """
        将system和messages分开
        """
        system: str | list[dict[str, Any]] = ""
        raw: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content")

            if role == "system":
                system = content if isinstance(content, (str, list)) else str(content or "")
                continue

            else:
                raw.append({
                    "role": role,
                    "content": content,
                })

        return system, raw


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
        if response is None:
            logger.error("Anthropic _parse_response received None")
            return LLMResponse(
                content=None,
                tool_calls=[],
                finish_reason="error",
                usage={},
            )

        blocks = getattr(response, "content", None) or []
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in blocks:
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