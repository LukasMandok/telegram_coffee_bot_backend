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
    
    @abstractmethod
    async def create_passive_user(self, first_name: str, last_name: Optional[str] = None) -> Any:
        pass
    
    @abstractmethod
    async def find_passive_user_by_name(self, first_name: str, last_name: Optional[str] = None) -> Optional[Any]:
        pass
    
    @abstractmethod
    async def convert_passive_to_full_user(self, passive_user: Any, user_id: int, username: str, first_name: Optional[str] = None, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en") -> Any:
        pass
    
    @abstractmethod
    async def is_user_admin(self, user_id: int) -> bool:
        pass
    
    
    
    ### Configuration
        
    @abstractmethod
    async def get_password(self) -> Optional[Any]:
        pass
    
    @abstractmethod
    async def get_admins(self) -> Optional[List[int]]:
        pass
    
    @abstractmethod
    async def add_admin(self, user_id: int) -> bool:
        pass
    
    @abstractmethod
    async def remove_admin(self, user_id: int) -> bool:
        pass
    