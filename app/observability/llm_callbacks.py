"""Privacy-safe LangChain callback for LLM latency, token counts, and error types.
Never logs prompts, applicant text, or model responses.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from app.observability.metrics import record_pipeline_event

logger = logging.getLogger(__name__)


class TelemetryCallbackHandler(AsyncCallbackHandler):
    """Track LLM execution latency, token usage, and errors securely."""

    def __init__(self, model_name: str = "unknown") -> None:
        self.model_name = model_name
        self._start_time: float = 0.0

    async def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any
    ) -> None:
        self._start_time = time.perf_counter()

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        duration = max(0.0, time.perf_counter() - self._start_time)
        token_usage = {}
        if response.llm_output and isinstance(response.llm_output, dict):
            token_usage = response.llm_output.get("token_usage", {})
        total_tokens = token_usage.get("total_tokens", 0)
        logger.info(
            "llm call completed model=%s duration_ms=%.2f tokens=%s",
            self.model_name,
            duration * 1000,
            total_tokens,
        )
        record_pipeline_event("llm.completed", outcome="success", stage="graph")

    async def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        duration = max(0.0, time.perf_counter() - self._start_time)
        err_type = type(error).__name__
        logger.error(
            "llm call failed model=%s duration_ms=%.2f error_type=%s",
            self.model_name,
            duration * 1000,
            err_type,
        )
        record_pipeline_event("llm.failed", outcome="failure", stage="graph", error_type=err_type)
