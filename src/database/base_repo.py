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
    
    @abstractmethod
    async def create_telegram_user(self, user_id: int, username: str, first_name: str, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en") -> Any:
        pass
    
    @abstractmethod
    async def create_full_user(self, user_id: int, username: str, first_name: str, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en") -> Any:
        pass
    
    
    
    ### Configuration
        
    @abstractmethod
    async def get_password(self) -> Optional[Any]:
        pass
    
    @abstractmethod
    async def get_admins(self) -> Optional[List[int]]:
        pass
    