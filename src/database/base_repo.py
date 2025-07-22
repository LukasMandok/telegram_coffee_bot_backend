from abc import ABC, abstractmethod
from typing import List, Optional, Any

class BaseRepository(ABC):
    @abstractmethod
    async def connect(self, uri: str) -> None:
        pass
    
    @abstractmethod
    async def close(self) -> None:
        pass
    
    ### Users
    
    @abstractmethod
    async def find_all_users(self) -> Optional[List[Any]]:
        pass

    @abstractmethod
    async def find_user_by_id(self, id: int) -> Optional[Any]:
        pass
    
    
    
    ### Configuration
        
    @abstractmethod
    async def get_password(self) -> Optional[Any]:
        pass
    
    @abstractmethod
    async def get_admins(self) -> Optional[List[int]]:
        pass
    