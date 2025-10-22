from abc import ABC, abstractmethod
from typing import List, Optional, Any, Dict

class BaseRepository(ABC):
    @abstractmethod
    async def connect(self, uri: str) -> None:
        pass
    
    @abstractmethod
    async def close(self) -> None:
        pass
    
    ### Users
    
    @abstractmethod
    async def find_all_users(self, exclude_archived: bool = False, exclude_disabled: bool = False) -> Optional[List[Any]]:
        pass
    
    @abstractmethod
    async def find_all_telegram_users(self, exclude_archived: bool = False, exclude_disabled: bool = False) -> Optional[List[Any]]:
        pass

    @abstractmethod
    async def find_user_by_id(self, id: int) -> Optional[Any]:
        pass

    @abstractmethod
    async def find_user_by_display_name(self, display_name: str) -> Optional[Any]:
        pass
    
    @abstractmethod
    async def create_telegram_user(self, user_id: int, username: str, first_name: str, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en", paypal_link: Optional[str] = None) -> Any:
        pass
    
    @abstractmethod
    async def create_passive_user(self, first_name: str, last_name: Optional[str] = None) -> Any:
        pass
    
    @abstractmethod
    async def find_passive_user_by_name(self, first_name: str, last_name: Optional[str] = None) -> Optional[Any]:
        pass
    
    @abstractmethod
    async def convert_passive_to_telegram_user(self, passive_user: Any, user_id: int, username: str, first_name: Optional[str] = None, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en", paypal_link: Optional[str] = None) -> Any:
        pass
    
    @abstractmethod
    async def is_user_admin(self, user_id: int) -> bool:
        pass
    
    ### User Settings
    
    @abstractmethod
    async def get_user_settings(self, user_id: int) -> Optional[Any]:
        pass
    
    @abstractmethod
    async def update_user_settings(self, user_id: int, **kwargs) -> Any:
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
    
    @abstractmethod
    async def get_log_settings(self) -> Optional[Dict[str, Any]]:
        """Get logging settings from config."""
        pass
    
    @abstractmethod
    async def update_log_settings(self, **kwargs) -> bool:
        """Update logging settings in config."""
        pass
    