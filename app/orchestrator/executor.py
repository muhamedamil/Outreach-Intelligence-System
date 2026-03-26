# app/orchestrator/executor.py

import asyncio
from typing import Callable, Any
import time


from app.config.settings import settings
import logging

logger = logging.getLogger(__name__)

class ExecutionResult:
    def __init__(self, success: bool, result: Any = None, error: str = None, latency: float = None):
        """
        What every agent run comes back as — no exceptions, no surprises.
        success=False with an error message is a valid outcome, not a crash.
        """
        self.success = success
        self.result = result
        self.error = error
        self.latency = latency


class AgentExecutor:
    """
    The safety net that wraps every agent call in the pipeline.
    Handles timeouts, retries, and backoff so the agents themselves don't have to.

    max_retries=2 and timeout from settings are sane defaults.
    """

    def __init__(self, max_retries: int = 2, timeout: int = settings.AGENT_TIMEOUT):
        self.max_retries = max_retries
        self.timeout = timeout

    async def run(
        self,
        agent_name: str,
        func: Callable,
        *args,
        **kwargs
    ) -> ExecutionResult:
        """
        Runs an agent function with timeout protection and exponential backoff.
        Always returns an ExecutionResult — never raises. Caller decides what to do with failure.
        """

        attempt = 0
        total_start = time.time()

        while attempt <= self.max_retries:
            start = time.time()
            try:

                result = await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=self.timeout
                )

                latency = round((time.time() - start) * 1000, 2)

                return ExecutionResult(
                    success=True,
                    result=result,
                    latency=latency
                )

            except asyncio.TimeoutError:
                error = f"{agent_name} timed out after {self.timeout}s"
                logger.error(error)
            except Exception as e:
                error = f"{agent_name} failed: {str(e)}"
                logger.error(error)

            attempt += 1
            await asyncio.sleep(1.5 ** attempt)  # exponential backoff

        latency = round((time.time() - total_start) * 1000, 2)
        return ExecutionResult(success=False, error=error, latency=latency)