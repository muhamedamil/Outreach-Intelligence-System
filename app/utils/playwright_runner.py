# app/utils/playwright_runner.py
#
# PROFESSIONAL-GRADE PLAYWRIGHT ISOLATION FOR WINDOWS
# ─────────────────────────────────────────────────────
# WHY THIS MODULE EXISTS:
#   Uvicorn's --reload flag on Windows forces SelectorEventLoop,
#   which does NOT support asyncio.create_subprocess_exec().
#   Playwright internally calls create_subprocess_exec to launch
#   the browser binary, causing NotImplementedError.
#
# THE FIX:
#   We run ALL Playwright operations inside a dedicated thread
#   that creates its own ProactorEventLoop. This completely
#   decouples our browser automation from uvicorn's event loop.
# ─────────────────────────────────────────────────────

import sys
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Any

logger = logging.getLogger(__name__)

# A dedicated thread pool for Playwright operations
_playwright_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="playwright")


def _run_in_proactor_loop(coro_func: Callable, *args: Any) -> Any:
    """
    Execute an async coroutine inside a brand-new ProactorEventLoop
    running in a background thread. This guarantees Playwright works
    on Windows regardless of the main thread's event loop type.
    """
    if sys.platform == 'win32':
        # Create a fresh ProactorEventLoop for this thread
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_func(*args))
    finally:
        loop.close()


async def run_playwright_task(coro_func: Callable, *args: Any) -> Any:
    """
    Public API: Run a Playwright-based coroutine safely on Windows.
    
    Usage:
        result = await run_playwright_task(my_playwright_function, arg1, arg2)
    
    This offloads the coroutine to a dedicated thread with its own
    ProactorEventLoop, bypassing any SelectorEventLoop limitations
    from uvicorn --reload.
    """
    main_loop = asyncio.get_event_loop()
    
    # If we're already on a ProactorEventLoop, just run directly
    if sys.platform == 'win32':
        try:
            if isinstance(main_loop, asyncio.ProactorEventLoop):
                logger.debug("Already on ProactorEventLoop, running directly.")
                return await coro_func(*args)
        except Exception:
            pass
    else:
        # Non-Windows: Playwright works fine on any loop
        return await coro_func(*args)
    
    # Windows + SelectorEventLoop: offload to a dedicated thread
    logger.debug("Offloading Playwright task to ProactorEventLoop thread.")
    return await main_loop.run_in_executor(
        _playwright_executor,
        _run_in_proactor_loop,
        coro_func,
        *args
    )
