"""
Telegram-specific Pydantic models for the bot.

This module contains Pydantic models that are specifically related to Telegram
functionality, message handling, and bot configuration.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
from .conversations import ConversationTimeout


class GroupMember(BaseModel):
    """Represents a coffee group member with their coffee count.
    
    This model provides validation for group member data including
    name validation and coffee count constraints.
    """
    name: str = Field(..., description="The member's name")
    coffee_count: int = Field(default=0, ge=0, description="Number of coffees ordered")
    
    @field_validator('name')
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        """Validate that name is not empty or just whitespace."""
        if not v.strip():
            raise ValueError('Name cannot be empty')
        return v.strip()


class MessageModel(BaseModel):
    """Represents a Telegram message with enhanced functionality.
    
    This model wraps Telegram message objects to provide:
    - Pydantic validation and serialization
    - Direct access to message properties  
    - Convenient edit/delete operations
    - Type safety for message handling
    """
    id: Optional[int] = Field(default=None, description="Message ID")
    text: Optional[str] = Field(default=None, description="Message text content")
    deleted: bool = Field(default=False, description="Whether message has been deleted")
    user_id: Optional[int] = Field(default=None, description="ID of the user who sent the message")
    parse_mode: Optional[str] = Field(default=None, description="Message parse mode (HTML, Markdown)")
    buttons: Optional[Any] = Field(default=None, description="Message inline keyboard buttons")
    
    # Store reference to the original Telegram message for operations like edit/delete
    telegram_message: Optional[Any] = Field(default=None, exclude=True)
    
    @classmethod
    def from_telegram_message(cls, telegram_message: Any) -> "MessageModel":
        """Create MessageModel from a Telegram message object.
        
        Args:
            telegram_message: Original Telegram message object
            
        Returns:
            MessageModel instance with properties extracted from telegram_message
        """
        return cls(
            id=getattr(telegram_message, 'id', None),
            text=getattr(telegram_message, 'text', None),
            user_id=getattr(telegram_message, 'from_id', None),
            telegram_message=telegram_message
        )
    
    async def edit(self, text: str, buttons: Any = None) -> None:
        """Edit the original Telegram message.
        
        Args:
            text: New message text
            buttons: New keyboard buttons (optional)
        """
        if self.telegram_message:
            await self.telegram_message.edit(text, buttons=buttons)
            self.text = text
            
    async def delete(self) -> None:
        """Delete the original Telegram message."""
        if self.telegram_message:
            await self.telegram_message.delete()
            self.deleted = True


class BotConfiguration(BaseModel):
    """Configuration settings for the TelethonAPI bot."""
    api_id: int = Field(..., description="Telegram API ID")
    api_hash: str = Field(..., min_length=1, description="Telegram API hash")
    bot_token: str = Field(..., min_length=1, description="Bot token from BotFather")
    max_messages_cache: int = Field(default=10, gt=0, description="Maximum cached messages")
    message_cleanup_interval: int = Field(default=10, gt=0, description="Cleanup interval in seconds")
    timeouts: ConversationTimeout = Field(default_factory=ConversationTimeout, description="Timeout settings")
    
    def get_timeout(self, operation: str = "default") -> int:
        """
        Get timeout value for a specific operation.
        
        Args:
            operation: Operation type (default, registration, password, group_selection)
            
        Returns:
            Timeout in seconds
        """
        return getattr(self.timeouts, operation, self.timeouts.default)
    
    def get_conversation_timeout(self) -> int:
        """
        Get the default conversation timeout value.
        
        Returns:
            Timeout in seconds for conversations
        """
        return self.timeouts.registration


class GroupMemberData(BaseModel):
    """Represents a group member with coffee count and user ID."""
    coffee: int = Field(default=0, description="Number of coffees ordered")
    user_id: Optional[int] = Field(default=None, description="Telegram user ID if known")

class GroupState(BaseModel):
    """Represents the current state of the coffee group ordering system."""
    members: Dict[str, GroupMemberData] = Field(default_factory=dict, description="Member names and their data")
    
    def add_member(self, member_name: str, user_id: Optional[int] = None) -> None:
        """Add a new member to the group with zero coffee count."""
        if member_name not in self.members:
            self.members[member_name] = GroupMemberData(coffee=0, user_id=user_id)
        else:
            raise ValueError(f"Member {member_name} already exists in the group")
    
    def get_total_coffees(self) -> int:
        """Calculate total coffee orders across all members."""
        return sum(member.coffee for member in self.members.values())
    
    def reset_orders(self) -> None:
        """Reset all coffee orders to zero."""
        for member_data in self.members.values():
            member_data.coffee = 0
            
    def add_coffee(self, member_name: str) -> bool:
        """Add a coffee for a member. Returns True if successful."""
        if member_name in self.members:
            self.members[member_name].coffee += 1
            return True
        return False
    
    def remove_coffee(self, member_name: str) -> bool:
        """Remove a coffee for a member. Returns True if successful."""
        if member_name in self.members and self.members[member_name].coffee > 0:
            self.members[member_name].coffee -= 1
            return True
        return False
    
    def export_state(self) -> str:
        """
        Export the current group state as JSON string.
        
        Returns:
            JSON string representation of the group state
        """
        return self.model_dump_json(indent=2)
    
    @classmethod
    def import_state(cls, json_data: str) -> "GroupState":
        """
        Import group state from JSON string.
        
        Args:
            json_data: JSON string representation of group state
            
        Returns:
            GroupState instance
        """
        return cls.model_validate_json(json_data)
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the current group state.
        
        Returns:
            Dictionary with group statistics and information
        """
        total_coffees = self.get_total_coffees()
        members_with_orders = sum(1 for member_data in self.members.values() if member_data.coffee > 0)
        
        return {
            "total_members": len(self.members),
            "total_coffees": total_coffees,
            "members_with_orders": members_with_orders,
            "members_summary": [
                {"name": name, "coffee_count": member_data.coffee, "user_id": member_data.user_id}
                for name, member_data in self.members.items()
                if member_data.coffee > 0
            ]
        }
