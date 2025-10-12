"""Utility functions for working with Beanie ODM."""

import asyncio
from functools import wraps
from typing import Callable, Any
from beanie import Document
from beanie.exceptions import CollectionWasNotInitialized


async def wait_for_beanie(document_class: type[Document], max_wait: float = 10.0) -> bool:
    """
    Wait for Beanie to be initialized for a given document class.
    
    Args:
        document_class: A Beanie Document class to check
        max_wait: Maximum seconds to wait (default 10)
        
    Returns:
        True if Beanie is ready, False if timeout
    """
    wait_interval = 0.5
    elapsed = 0.0
    
    while elapsed < max_wait:
        try:
            # More thorough check: try to build a query filter
            # This will fail if Beanie isn't fully initialized
            _ = document_class.find()
            return True
        except (CollectionWasNotInitialized, AttributeError, RuntimeError):
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval
        except Exception:
            # Unexpected error, but continue waiting
            await asyncio.sleep(wait_interval)
            elapsed += wait_interval
    
    return False


def requires_beanie(document_class: type[Document], max_wait: float = 10.0):
    """
    Decorator that waits for Beanie initialization before executing the function.
    
    Usage:
        @requires_beanie(CoffeeCard)
        async def load_from_db(self):
            self.cards = await CoffeeCard.find(...).to_list()
    
    Args:
        document_class: A Beanie Document class to wait for
        max_wait: Maximum seconds to wait (default 10)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            if not await wait_for_beanie(document_class, max_wait):
                print(f"⚠️  Warning: Beanie initialization timeout for {document_class.__name__}")
            return await func(*args, **kwargs)
        return wrapper
    return decorator
