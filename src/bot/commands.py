"""
Telegram bot commands and Telegram-specific business logic.

This module contains command handlers and orchestration functions that combine
Telegram-specific operations with domain business logic.
"""

from typing import Dict, Any, List, TYPE_CHECKING
from telethon import events
from ..models.beanie_models import TelegramUser
from .conversations import ConversationManager
from ..handlers import handlers
from ..dependencies import dependencies as dep
from ..handlers.coffee_handlers import (
    start_coffee_session,
    update_session_coffee_counts
)
from ..exceptions.coffee_exceptions import (
    InvalidCoffeeCountError, 
    InsufficientCoffeeError, 
    SessionNotActiveError,
    UserNotFoundError
)

if TYPE_CHECKING:
    from ..api.telethon_api import TelethonAPI


# Command Handlers - These handle Telegram events and orchestrate business logic

class CommandManager:
    """
    Manages all command handling for the Telegram bot.
    
    This class provides a centralized way to handle all bot commands
    with proper dependency injection and state management.
    """
    
    def __init__(self, api: "TelethonAPI"):
        """
        Initialize the command manager.
        
        Args:
            api: The TelethonAPI instance for bot communication
        """
        self.api = api
        self.conversation_manager = ConversationManager(api)
    
    async def handle_start_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /start command for user registration.
        
        Args:
            event: The NewMessage event containing /start command
        """
        user_id = event.sender_id
        print("sender id:", user_id)

        # Check if user is already registered
        if await handlers.check_user(user_id, dep.get_repo()):
            await self.api.message_manager.send_text(
                user_id, 
                "There is nothing more to do. You are already registered.", 
                True, 
                True
            )
            return 

        # Use conversation manager for registration
        await self.conversation_manager.register_conversation(user_id)

    async def handle_group_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /group command for group selection.
        
        Args:
            event: The NewMessage event containing /group command
        """
        user_id = event.sender_id
        
        # Use conversation manager for group selection
        await self.conversation_manager.group_selection(user_id)

    async def handle_password_command(self, event: events.NewMessage.Event) -> None:
        """
        Test password handler for /password command.
        
        Args:
            event: The NewMessage event containing /password command
        """
        # get the message and cut off the /password part
        message = event.message.message
        print("message: ", message)
        try:
            password = message.split(" ")[1]
            
            password_correct = await handlers.check_password(password, dep.get_repo())
            print("Password is correct:", password_correct)
            
        # TODO: improve exceptions
        except Exception as e:
            print("password was not provided", e)

    @dep.verify_user
    async def handle_user_verification_command(self, event: events.NewMessage.Event) -> None:
        """
        Handler to verify if a user is registered.
        
        Args:
            event: The NewMessage event from user verification test
        """
        user_id = event.sender_id
        await self.api.message_manager.send_text(user_id, "You are a registered user.", True, True)

    @dep.verify_admin    
    async def handle_admin_verification_command(self, event: events.NewMessage.Event) -> None:
        """
        Handler to verify if a user is an admin.
        
        Args:
            event: The NewMessage event from admin verification test
        """
        user_id = event.sender_id
        await self.api.message_manager.send_text(user_id, "You are a registered admin.", True, True)

    async def handle_digits_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle digit messages (temporary handler for testing).
        
        Args:
            event: The NewMessage event containing digits
        """
        user_id = event.sender_id
        await self.api.message_manager.send_text(user_id, f'catches digits: {event.text}', True, True)

    async def handle_unknown_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle unknown commands sent to the bot.
        
        Args:
            event: The NewMessage event containing an unknown command
        """
        sender_id = event.sender_id
        message = event.message.message
        
        await self.api.message_manager.send_text(sender_id, f"**{message}** is an unknown command.", True, True)