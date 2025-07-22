"""
Typing utilities for better Link field type checking.
Based on GitHub issue suggestions for Beanie Link typing.
"""

from typing import TYPE_CHECKING, TypeVar, Union

if TYPE_CHECKING:
    from beanie import Link as BeanieLink
    
    T = TypeVar('T')
    
    # Union type that represents both the linked document and the Link wrapper  
    type Link[T] = T | BeanieLink[T]
        
else:
    from beanie import Link
