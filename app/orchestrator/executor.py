# app/orchestrator/executor.py

import asyncio
from typing import Callable, Any
import time


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

    max_retries=2 and timeout=15 are sane defaults — adjust per agent if needed,
    a researcher hitting an external API needs more patience than an in-memory lookup.
    """

    def __init__(self, max_retries: int = 2, timeout: int = 15):
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

        while attempt <= self.max_retries:
            try:
                start = time.time()

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
                error = f"{agent_name} timed out"
            except Exception as e:
                error = f"{agent_name} failed: {str(e)}"

            attempt += 1
            await asyncio.sleep(1.5 ** attempt)  # exponential backoff

        return ExecutionResult(success=False, error=error, latency=latency)