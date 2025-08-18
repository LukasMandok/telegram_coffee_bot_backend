"""
Utility decorators for the Telegram bot.

This module contains useful decorators for common functionality
like timeouts, error handling, and other cross-cutting concerns.
"""
from typing import TYPE_CHECKING

import asyncio
from typing import Callable
from functools import wraps



def with_timeout(timeout_seconds: int):
    """
    Decorator to add timeout to async functions.
    
    Args:
        timeout_seconds: Timeout duration in seconds
        
    Returns:
        Decorator function
    """
    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_seconds)
        return wrapper
    return decorator
