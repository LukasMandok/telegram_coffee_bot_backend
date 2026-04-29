from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Any, Dict, TYPE_CHECKING

from ..models.beanie_models import DebtSettings, GsheetSettings, SnapshotSettings

if TYPE_CHECKING:
    from .snapshot_manager import SnapshotManager


@dataclass(frozen=True)
class EffectiveNotificationPolicy:
    can_send: bool
    silent: bool
    blocked_reason: str | None = None

class BaseRepository(ABC):
    snapshot_manager: "SnapshotManager | None" = None

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
    async def create_telegram_user(self, user_id: int, username: str, first_name: str, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en", paypal_link: Optional[str] = None, display_name: Optional[str] = None) -> Any:
        pass
    
    @abstractmethod
    async def create_passive_user(self, first_name: str, last_name: Optional[str] = None) -> Any:
        pass
    
    @abstractmethod
    async def find_passive_user_by_name(self, first_name: str, last_name: Optional[str] = None) -> Optional[Any]:
        pass
    
    @abstractmethod
    async def convert_passive_to_telegram_user(self, passive_user: Any, user_id: int, username: str, first_name: Optional[str] = None, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en", paypal_link: Optional[str] = None, display_name: Optional[str] = None) -> Any:
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
    async def update_password(self, new_password: str) -> bool:
        """Update the registration password document."""
        pass
    
    @abstractmethod
    async def get_admins(self) -> Optional[List[int]]:
        pass

    async def get_registered_admins(self) -> List[int]:
        admins = await self.get_admins()
        if not admins:
            return []
        return [int(admin_id) for admin_id in admins]
    
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

    # App-wide notification settings
    async def get_notification_settings(self) -> Optional[Dict[str, Any]]:
        """Get app-wide notification settings."""
        raise NotImplementedError

    async def get_effective_notification_settings(
        self,
        user_id: int,
        *,
        force_silent: bool = False,
        notification_settings: Dict[str, Any] | None = None,
        telegram_user: Any | None = None,
        user_settings: Any | None = None,
    ) -> EffectiveNotificationPolicy:
        """Compute whether a background notification should be sent and whether it should be silent.

        Centralizes the effective notification policy (global enabled/silent + user enabled/silent
        + archived/disabled user filtering) so message senders do not reimplement this logic.
        """
        raise NotImplementedError

    async def update_notification_settings(self, **kwargs) -> bool:
        """Update app-wide notification settings."""
        raise NotImplementedError

    # App-wide debt settings
    async def get_debt_settings(self) -> Optional[DebtSettings]:
        """Get app-wide debt calculation settings."""
        raise NotImplementedError

    async def update_debt_settings(self, **kwargs) -> bool:
        """Update app-wide debt calculation settings."""
        raise NotImplementedError

    # App-wide Google Sheets settings
    async def get_gsheet_settings(self) -> Optional[GsheetSettings]:
        """Get Google Sheets synchronization settings."""
        raise NotImplementedError

    async def update_gsheet_settings(self, **kwargs) -> bool:
        """Update Google Sheets synchronization settings."""
        raise NotImplementedError

    # App-wide snapshot settings
    async def get_snapshot_settings(self) -> Optional[SnapshotSettings]:
        """Get snapshot settings."""
        raise NotImplementedError

    async def update_snapshot_settings(self, **kwargs) -> bool:
        """Update snapshot settings."""
        raise NotImplementedError
    