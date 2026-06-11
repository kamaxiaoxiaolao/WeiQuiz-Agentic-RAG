from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class LLMTask(str, Enum):
    ROUTER = "router"
    CLARIFICATION = "clarification"
    TOOL_PLANNER = "tool_planner"
    REWRITE = "rewrite"
    HYDE = "hyde"
    STEP_BACK = "step_back"
    DECOMPOSE = "decompose"
    GENERATION = "generation"
    INTERMEDIATE_SYNTHESIS = "intermediate_synthesis"
    GROUNDING = "grounding"
    MEMORY_SUMMARY = "memory_summary"
    LIGHTWEIGHT_CHAT = "lightweight_chat"


@dataclass(frozen=True)
class LLMTaskConfig:
    model: str
    api_key: str
    api_base: str
    timeout: float
    max_retries: int = 0


class LLMGateway:
    """Centralized OpenAI-compatible chat gateway.

    The first version keeps the existing OpenAI response shape so current
    modules can migrate without behavior changes. It centralizes model choice,
    timeout, retry policy, and lightweight call metrics.
    """

    def chat_completion(
        self,
        *,
        task: LLMTask,
        messages: list[dict[str, Any]],
        temperature: float = 0,
        max_tokens: int | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Any:
        task_config = self._task_config(task)
        client = OpenAI(
            api_key=task_config.api_key,
            base_url=task_config.api_base,
            timeout=task_config.timeout,
            max_retries=task_config.max_retries,
        )
        payload: dict[str, Any] = {
            "model": task_config.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
            **kwargs,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        started = time.perf_counter()
        try:
            response = client.chat.completions.create(**payload)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "[LLM] task=%s model=%s stream=%s success=true duration_ms=%s input_chars=%s",
                task.value,
                task_config.model,
                stream,
                duration_ms,
                self._message_chars(messages),
            )
            return response
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.warning(
                "[LLM] task=%s model=%s stream=%s success=false duration_ms=%s error=%s",
                task.value,
                task_config.model,
                stream,
                duration_ms,
                exc,
            )
            raise

    def stream_chat_completion(
        self,
        *,
        task: LLMTask,
        messages: list[dict[str, Any]],
        temperature: float = 0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        return self.chat_completion(
            task=task,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )

    def _task_config(self, task: LLMTask) -> LLMTaskConfig:
        model = self._task_model(task)
        api_base = self._task_api_base(task)
        api_key = self._task_api_key(task)
        timeout = self._task_timeout(task)
        return LLMTaskConfig(
            model=model,
            api_key=api_key,
            api_base=api_base,
            timeout=timeout,
            max_retries=settings.llm_gateway_max_retries,
        )

    @staticmethod
    def _task_model(task: LLMTask) -> str:
        mapping = {
            LLMTask.ROUTER: settings.llm_model_router or settings.router_model or settings.llm_model,
            LLMTask.CLARIFICATION: settings.llm_model_router or settings.router_model or settings.llm_model,
            LLMTask.TOOL_PLANNER: settings.llm_model_router or settings.router_model or settings.llm_model,
            LLMTask.REWRITE: settings.llm_model_rewrite or settings.router_model or settings.llm_model,
            LLMTask.HYDE: settings.llm_model_hyde or settings.llm_model,
            LLMTask.STEP_BACK: settings.llm_model_step_back or settings.llm_model,
            LLMTask.DECOMPOSE: settings.llm_model_decompose or settings.router_model or settings.llm_model,
            LLMTask.GENERATION: settings.llm_model_generation or settings.llm_model,
            LLMTask.INTERMEDIATE_SYNTHESIS: settings.llm_model_intermediate_synthesis or settings.llm_model,
            LLMTask.GROUNDING: settings.llm_model_grounding or settings.router_model or settings.llm_model,
            LLMTask.MEMORY_SUMMARY: settings.llm_model_memory_summary or settings.router_model or settings.llm_model,
            LLMTask.LIGHTWEIGHT_CHAT: settings.llm_model_lightweight_chat or settings.router_model or settings.llm_model,
        }
        return mapping[task]

    @staticmethod
    def _task_api_base(task: LLMTask) -> str:
        if task in {LLMTask.ROUTER, LLMTask.CLARIFICATION, LLMTask.TOOL_PLANNER}:
            return settings.router_api_base or settings.llm_api_base
        return settings.llm_api_base

    @staticmethod
    def _task_api_key(task: LLMTask) -> str:
        if task in {LLMTask.ROUTER, LLMTask.CLARIFICATION, LLMTask.TOOL_PLANNER}:
            return settings.qwen_llm_api_key or settings.llm_api_key
        return settings.llm_api_key

    @staticmethod
    def _task_timeout(task: LLMTask) -> float:
        mapping = {
            LLMTask.ROUTER: settings.router_timeout_seconds,
            LLMTask.CLARIFICATION: settings.router_timeout_seconds,
            LLMTask.TOOL_PLANNER: settings.router_timeout_seconds,
            LLMTask.REWRITE: settings.llm_timeout_rewrite_seconds,
            LLMTask.HYDE: settings.llm_timeout_hyde_seconds,
            LLMTask.STEP_BACK: settings.llm_timeout_step_back_seconds,
            LLMTask.DECOMPOSE: settings.llm_timeout_decompose_seconds,
            LLMTask.GENERATION: settings.llm_timeout_generation_seconds,
            LLMTask.INTERMEDIATE_SYNTHESIS: settings.llm_timeout_intermediate_synthesis_seconds,
            LLMTask.GROUNDING: settings.llm_timeout_grounding_seconds,
            LLMTask.MEMORY_SUMMARY: settings.llm_timeout_memory_summary_seconds,
            LLMTask.LIGHTWEIGHT_CHAT: settings.llm_timeout_lightweight_chat_seconds,
        }
        return mapping[task]

    @staticmethod
    def _message_chars(messages: list[dict[str, Any]]) -> int:
        total = 0
        for message in messages:
            total += len(str(message.get("content") or ""))
        return total


_gateway = LLMGateway()


def get_llm_gateway() -> LLMGateway:
    return _gateway
