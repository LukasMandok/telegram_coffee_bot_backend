"""
Telethon API module for Telegram bot functionality.

This module provides a comprehensive Telegram bot implementation using the Telethon library
with Pydantic models for data validation and type safety. It includes features for:

- Message handling and conversation management
- Coffee ordering system with inline keyboards
- User verification and authentication
- Automatic message cleanup
- Pydantic-based configuration and data models

Classes:
    - GroupMember: Represents a coffee group member
    - MessageModel: Telegram message wrapper with additional functionality
    - ConversationState: Tracks user conversation state
    - ConversationTimeout: Configuration for conversation timeouts
    - BotConfiguration: Main bot configuration settings
    - KeyboardButton: Keyboard button configuration
    - GroupState: Coffee group ordering state management
    - TelethonAPI: Main bot API handler
"""

import asyncio
import uuid
import re
from typing import Callable, Optional, Dict, List, Union, Any
from pydantic import BaseModel, Field, field_validator
from telethon import TelegramClient, events, errors, Button
from telethon.tl.types import UpdateShortMessage

from ..handlers import handlers, exceptions
from ..dependencies import dependencies as dep

# --- Pydantic Models for Type Safety and Data Validation ---

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

class ConversationState(BaseModel):
    """Represents the current state of a user conversation."""
    user_id: int = Field(..., description="ID of the user in conversation")
    step: str = Field(..., description="Current conversation step")
    data: Dict[str, Any] = Field(default_factory=dict, description="Conversation data storage")
    timeout: int = Field(default=30, gt=0, description="Conversation timeout in seconds")

class ConversationTimeout(BaseModel):
    """Configuration for conversation timeouts."""
    default: int = Field(default=30, gt=0, description="Default timeout in seconds")
    registration: int = Field(default=60, gt=0, description="Registration timeout in seconds") 
    password: int = Field(default=45, gt=0, description="Password input timeout in seconds")
    group_selection: int = Field(default=180, gt=0, description="Group selection timeout in seconds")

class BotConfiguration(BaseModel):
    """Configuration settings for the TelethonAPI bot."""
    api_id: int = Field(..., description="Telegram API ID")
    api_hash: str = Field(..., min_length=1, description="Telegram API hash")
    bot_token: str = Field(..., min_length=1, description="Bot token from BotFather")
    max_messages_cache: int = Field(default=10, gt=0, description="Maximum cached messages")
    message_cleanup_interval: int = Field(default=10, gt=0, description="Cleanup interval in seconds")
    timeouts: ConversationTimeout = Field(default_factory=ConversationTimeout, description="Timeout settings")
    
class KeyboardButton(BaseModel):
    """Represents a keyboard button configuration."""
    text: str = Field(..., description="Button display text")
    callback_data: str = Field(..., description="Data sent when button is pressed")
    row: int = Field(default=0, ge=0, description="Button row position")

# TODO: this has to be adjusted together with the members dictionary
class GroupState(BaseModel):
    """Represents the current state of the coffee group ordering system."""
    members: Dict[str, int] = Field(default_factory=dict, description="Member names and coffee counts")
    current_page: int = Field(default=0, ge=0, description="Current pagination page")
    current_group: Optional[str] = Field(default=None, description="Currently selected group name")
    
    def get_total_coffees(self) -> int:
        """Calculate total coffee orders across all members."""
        return sum(self.members.values())
    
    def reset_orders(self) -> None:
        """Reset all coffee orders to zero."""
        for member in self.members:
            self.members[member] = 0
            
    def add_coffee(self, member_name: str) -> bool:
        """Add a coffee for a member. Returns True if successful."""
        if member_name in self.members:
            self.members[member_name] += 1
            return True
        return False
    
    def remove_coffee(self, member_name: str) -> bool:
        """Remove a coffee for a member. Returns True if successful."""
        if member_name in self.members and self.members[member_name] > 0:
            self.members[member_name] -= 1
            return True
        return False



### API Handler

class TelethonAPI:
    """
    Main Telegram bot API handler.

    This class manages bot initialization, message handling, user conversations,
    and coffee group ordering through Telegram inline keyboards.
    """
    
    def __init__(self, api_id: Union[int, str], api_hash: str, bot_token: str) -> None:
        """
        Initialize the TelethonAPI bot and set up handlers and state.
        
        Args:
            api_id: Telegram API ID from my.telegram.org (can be string or int)
            api_hash: Telegram API hash from my.telegram.org  
            bot_token: Bot token from @BotFather
        """
        # Convert api_id to int if it's a string
        # IDEA: it should be possible to put this into property or isn't this the idea behind the property setter?
        if isinstance(api_id, str):
            api_id = int(api_id)
            
        # Store configuration using Pydantic model
        self.config = BotConfiguration(
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token
        )

        print(f"initialize and start bot: api_id: {self.config.api_id}, api_hash: {self.config.api_hash}, bot_token: {self.config.bot_token}")

        self.bot: TelegramClient = TelegramClient(
            'bot_' + str(uuid.uuid4()),
            self.config.api_id,
            self.config.api_hash
        ).start(bot_token=self.config.bot_token)

        # Initialize group state using Pydantic model
        # TODO: move this to a place for test initialization
        # TODO: this should be initialized from the database
        # TODO: this should contain user objects instead of this list to be converted into a keyboard layout or a json dict to the RestAPI, but in a different Bot class
        # IDEA: Isnt there also a GroupMember Class, which can be used for this?
        self.group_state = GroupState(
            members={
                "Lukas":0, "Heiko":0, "Barnie":0, "Klaus":0, "Hans":0,
                "David":0, "Jens":0, "Jürgen":0, "Ralf":0, "Rainer":0,
                "Jörg":0, "Johannes":0, "Max":0, "Peter":0, "Karlo":0,
                "Annie":0, "Marie":0, "Lena":0, "Lara":0, "Ruberta":0,
                "Susi1":0, "Susi2":0, "Susi3":0, "Susi4":0, "Susi5":0,
                "Marx1":0, "Marx2":0, "Marx3":0, "Marx4":0, "Marx5":0,
                "Leon1":0, "Leon2":0, "Leon3":0, "Leon4":0, "Leon5":0
            }
        )

        # List of messages or list of lists of messages (flexible for testing)
        # TODO: this should be per User (maybe identified by user_id)
        self.latest_messages: List[Union[MessageModel, List[MessageModel], Any]] = []
        
        # Active conversations
        self.active_conversations: Dict[int, ConversationState] = {}

        # Register all handlers
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Register all bot event handlers."""
        self.add_handler(self.start_command_handler, '/start')
        self.add_handler(self.group_command_handler, '/group')
        self.add_handler(self.test_password, '/password')
        self.add_handler(self.test_user_verification, "/user")
        self.add_handler(self.test_admin_verification, "/admin")
        self.add_handler(self.digits, events.NewMessage(incoming=True, pattern=re.compile(r'[0-9]+')))
        self.add_handler(self.unknown_command_handler)
    
    @property
    def api_id(self) -> int:
        """Backward compatibility property for api_id."""
        return self.config.api_id
    
    @property
    def api_hash(self) -> str:
        """Backward compatibility property for api_hash."""
        return self.config.api_hash
    
    @property
    def bot_token(self) -> str:
        """Backward compatibility property for bot_token."""
        return self.config.bot_token
    
    @property
    def group(self) -> Dict[str, int]:
        """Backward compatibility property for group members."""
        return self.group_state.members
    
    @group.setter 
    def group(self, value: Dict[str, int]) -> None:
        """Backward compatibility setter for group members."""
        self.group_state.members = value
        
    @property
    def current_page(self) -> int:
        """Backward compatibility property for current page."""
        return self.group_state.current_page
    
    # IDEA: shouldnt this check, that the page number is valid?
    @current_page.setter
    def current_page(self, value: int) -> None:
        """Backward compatibility setter for current page."""
        self.group_state.current_page = value
        
        
    
    async def run(self) -> None:
        """
        Start the message vanisher task and run the bot until disconnected.
        
        This method starts background tasks and keeps the bot running
        until manually disconnected or an error occurs.
        """
        print("!!!! start message vanisher")
        asyncio.create_task(self.message_vanisher()) 
        

        self.bot.run_until_disconnected()
        
    ### SECTION: handler administration
    
    def add_handler(
        self,
        handler: Callable[..., Any],
        event: Optional[Union[str, Any]] = None,  # More flexible type
        exception_handler: Optional[Callable[[Callable], Callable]] = None
    ) -> None:
        """
        Add a handler to the bot with optional event and exception handler.
        
        Args:
            handler: The async function to handle events
            event: Event pattern (string command or EventBuilder)
            exception_handler: Custom exception handler wrapper
        """
        
        if isinstance(event, str):
            event = events.NewMessage(pattern=event)
        elif event is None:
            event = events.NewMessage()
            
        if exception_handler is None:
            exception_handler = self.exception_handler
            
        async def wrapped_handler(event_obj) -> None:
            message = event_obj.message
            # Convert telegram message to our MessageModel for consistency
            message_model = MessageModel.from_telegram_message(message)
            self.add_latest_message(message_model, True, True)
            await handler(event_obj)
            
            # print("stop propagation")
            # NOTE: ist es ok, wenn ich die propagation hier stoppe?
            raise events.StopPropagation
            
        wrapped_handler_with_exception = exception_handler(wrapped_handler)
        self.bot.add_event_handler(wrapped_handler_with_exception, event)
        
    
    def exception_handler(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """
        Wrap a handler with exception handling logic.
        
        Args:
            func: The handler function to wrap
            
        Returns:
            Wrapped handler with exception handling
        """
        async def wrapper(event, *args, **kwargs) -> Any: 
            try: 
                sender_id = event.sender_id
                try:
                    return await func(event, *args, **kwargs)
                except exceptions.VerificationException as e:
                    print(f"Verification Exception caught: {e}")
                    await self.send_text(sender_id, e.message, True, True)
                    raise events.StopPropagation
                    # return False
                    
                except asyncio.TimeoutError as e:
                    print(f"Timeout Exception caught: {e}")
                    await self.send_text(sender_id, "Your request has expired. Please start again from the beginning.")
                    return False
                    
            except AttributeError as e:
                print("Not a valid event given.", e)
            except asyncio.TimeoutError as e:
                print(f"TimeoutError: {e}")
            except errors.rpcerrorlist.FloodWaitError as e:
                print(f"FloodWaitError: {e}")
            except errors.rpcerrorlist.UserIsBlockedError as e:
                print(f"UserIsBlockedError: {e}")
                
        return wrapper
    
    
    # subfunction of message_vanisher to make code more readable
    async def delete_oldest_message(self) -> None:
        """
        Delete the oldest message or list of messages from latest_messages.
        
        This method removes and deletes the oldest message(s) from the cache
        to prevent memory buildup and clean up old UI elements.
        """
        if not self.latest_messages:
            return
            
        message = self.latest_messages.pop(0)
        if isinstance(message, list):
            # QUESTION: Do you think this is actually a bad idea?
            await asyncio.gather(*(m.delete() for m in message))
        else:
            await message.delete()
            
            
    def get_latest_messages_length(self) -> List[Union[int, bool]]:
        """
        Return a list of lengths/types for latest_messages for debugging/UI.
        
        Returns:
            List where each element is either:
            - int: Length of a message list
            - bool: True for single messages
        """
        length = []
        for m in self.latest_messages:
            if isinstance(m, list):
                length.append(len(m))
            else:
                length.append(True)
        return length
    
    # NOTE: In theory, this is easier but does not work with consecutive lists
    # async def message_vanisher(self):
    #     while True:
    #         await asyncio.sleep(10)
            
    #         while len(self.latest_messages) > 3:
    #             self.delete_oldest_message()
            
    #         if len(self.latest_messages) == 0:
    #             continue
            
    #         i = 0
    #         while i < len(self.latest_messages):
    #             if isinstance(self.latest_messages[i], list):
    #                 while i > 0:
    #                     if (i < len(self.latest_messages) - 1):
    #                         self.delete_oldest_message()
    #                     i -= 1
    #                 break
    #             i += 1

    # IDEA: Maybe add an overall timer, that gets reset after each new message and just deletes all messages after 1h or so
    async def message_vanisher(self) -> None:
        """Background task to delete old messages after a timeout."""
        while(True):
            # print("start loop - length:", len(self.latest_messages), " content:", self.get_latest_messages_length())
            await asyncio.sleep(10)
            
            if len(self.latest_messages) == 0:
                continue
            
            i = 0
            while( i < len(self.latest_messages)):
                # delete older messages if list is longer than 3
                if len(self.latest_messages) > 3:
                    await self.delete_oldest_message()
                    continue
                
                # Check for lists in the remaining messages and delete everything before a list 
                if isinstance(self.latest_messages[i], list):
                    # print("i:", i, "length - 2:", len(self.latest_messages) - 1)
                    for j in range(i):
                        if ( j >= len(self.latest_messages) - 2):
                            break
                
                        await self.delete_oldest_message()
        
                i += 1                
                
            
    ### SECTION: Communication
    
    # TODO: Add timeouts for certain functions using a decorator
    
    def add_latest_message(
        self, 
        message: Union[MessageModel, Any],  # More flexible to support testing with various message types
        conv: bool = False, 
        new: bool = False
    ) -> None:
        """
        Add a message to latest_messages, supporting conversation and new flags.
        
        Args:
            message: The message to add (MessageModel or Telegram message object)
            conv: If True, add to existing conversation list
            new: If True, create a new conversation list
        """
        if new:
            self.latest_messages.append([message])
            
        elif conv:
            if len(self.latest_messages) > 0 and isinstance(self.latest_messages[-1], list):
                self.latest_messages[-1].append(message)
            else:
                self.latest_messages.append([message]) 
        else:
            self.latest_messages.append(message)
            
    async def send_text(
        self, 
        user_id: int, 
        text: str, 
        vanish: bool = True, 
        conv: bool = False
    ) -> Optional[MessageModel]:
        """
        Send a message to a user and optionally add it to latest_messages.
        
        Args:
            user_id: Telegram user ID to send message to
            text: Message text content
            vanish: If True, add message to latest_messages for cleanup
            conv: If True, add to conversation group
            
        Returns:
            The sent message object or None if failed
        """
        try:
            telegram_message = await self.bot.send_message(user_id, text)
            message_model = MessageModel.from_telegram_message(telegram_message)
            
            if vanish:
                self.add_latest_message(message_model, conv)

            return message_model
        except Exception as e:
            print(f"Failed to send message to user {user_id}: {e}")
            return None
            
    async def send_keyboard(
        self,
        user_id: int,
        text: str,
        keyboard_layout: Any,  # More flexible to handle various button types
        vanish: bool = True,
        conv: bool = False
    ) -> Optional[MessageModel]:
        """
        Send a keyboard to the user and optionally add it to latest_messages.
        
        Args:
            user_id: Telegram user ID to send keyboard to
            text: Message text content
            keyboard_layout: 2D list representing keyboard button layout
            vanish: If True, add message to cleanup cache
            conv: If True, add to conversation group
            
        Returns:
            The sent message with keyboard or None if failed
        """
        try:
            telegram_message = await self.bot.send_message(
                user_id,
                text,
                buttons=keyboard_layout,
                parse_mode='html'
            )
            message_model = MessageModel.from_telegram_message(telegram_message)
            
            if vanish:
                self.add_latest_message(message_model, conv)

            return message_model
        except Exception as e:
            print(f"Failed to send keyboard to user {user_id}: {e}")
            return None
            
    def keyboard_callback(self, user_id: int) -> events.CallbackQuery:
        """
        Return a CallbackQuery event filter for a specific user.
        
        Args:
            user_id: Telegram user ID to filter callbacks for
            
        Returns:
            CallbackQuery event filter
        """
        return events.CallbackQuery(func=lambda e: e.sender_id == user_id)



    ### SECTION: Event Handlers

    # TODO: Make sure that this is only called, if non of the other handlers are (so only for actual unknown commands), otherwise it could use a list of known commands, depending on what is more convenient
    async def unknown_command_handler(self, event: events.NewMessage.Event) -> None:
        """
        Handle unknown commands sent to the bot.
        
        Args:
            event: The NewMessage event containing an unknown command
        """
        sender_id = event.sender_id
        message = event.message.message
        
        await self.send_text(sender_id, f"**{message}** is an unknown command.", True, True)

    # INFO: this is only temporary, should be deleted again
    async def test_password(self, event: events.NewMessage.Event) -> None:
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
            
    # INFO: this is only temporary and will be deleted again
    async def digits(self, event: events.NewMessage.Event) -> None:
        """
        Handle digit messages.
        
        Args:
            event: The NewMessage event containing digits
        """
        user_id = event.sender_id
        await self.send_text(user_id, f'catches digits: {event.text}', True, True)
            
    @dep.verify_user
    async def test_user_verification(self, event: events.NewMessage.Event) -> None:
        """
        Handler to verify if a user is registered.
        
        Args:
            event: The NewMessage event from user verification test
        """
        user_id = event.sender_id
        await self.send_text(user_id, "You are a registered user.", True, True)

    @dep.verify_admin    
    async def test_admin_verification(self, event: events.NewMessage.Event) -> None:
        """
        Handler to verify if a user is an admin.
        
        Args:
            event: The NewMessage event from admin verification test
        """
        user_id = event.sender_id
        await self.send_text(user_id, "You are a registered admin.", True, True)
    
    async def start_command_handler(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /start command for user registration.
        
        Args:
            event: The NewMessage event containing /start command
        """
        user_id = event.sender_id
        print("sender id:", user_id)
    
        # Check if user is already registered
        if await handlers.check_user(user_id, dep.get_repo()):
            await self.send_text(user_id, "There is nothing more to do. You are already registered.", True, True)
            return 
        
        # do registration
        await self.register_conversation(user_id)

    # NOTE: add user verification
    async def group_command_handler(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /group command for group selection.
        
        Args:
            event: The NewMessage event containing /group command
        """
        user_id = event.sender_id
        await self.group_selection(user_id)
        
    
    ###### Communication formats:
    
        
    ### SECTION: Conversations
    
    async def register_conversation(self, user_id: int) -> bool:
        """Start the registration conversation with a user."""
        async with self.bot.conversation(user_id) as conv:
            chat = await conv.get_input_chat()

            # Start registration process (TODO: put into its own function)
            message_register = await self.send_keyboard(user_id, "Do you want to register?", self.keyboard_confirm, True, True)
            if message_register is None:
                return False
                
            button_event: events.CallbackQuery.Event = await conv.wait_event(self.keyboard_callback(user_id), timeout = 30)
            data = button_event.data.decode('utf8')
            if data == "No":
                await message_register.edit(f"Register process aborted.", buttons=None)
                return False
            await message_register.edit(f"Start Register process.", buttons=None)

            # Password request
            if not await self.request_authentication(conv):
                return False

            return True
            

    async def request_authentication(self, conv) -> bool:
        """Request password authentication from a user in a conversation."""
        chat = await conv.get_input_chat()
        
        message_password = await self.send_text(chat.user_id if hasattr(chat, 'user_id') else chat, "Please enter the password:", True, True)
        max_tries = 3
        tries = 0
        authenticated = False
        
        while (tries < max_tries):
            password_event = await conv.wait_event(events.NewMessage(incoming=True), timeout = 30)
            password = password_event.message.message
            await password_event.message.delete()
            
            authenticated = await handlers.check_password(password, dep.get_repo())
            if authenticated == True:
                return True
            else:
                chat_id = chat.user_id if hasattr(chat, 'user_id') else chat
                await self.send_text(chat_id, "Password incorrect. Please try again.", True, True)
                tries += 1
                
        if authenticated == False:
            chat_id = chat.user_id if hasattr(chat, 'user_id') else chat
            await self.send_text(chat_id, "Too many tries. Aborting registration.", True, True)
            return False
        
        return False  # Should not reach here, but ensures all paths return
    

    async def group_selection(self, user_id: int) -> None:
        """Start the group selection conversation with a user."""
        async with self.bot.conversation(user_id) as conv:
            chat = await conv.get_input_chat()
                        
            message = await self.send_keyboard(user_id, "Group View", self.getGroupKeyboard(), True, True)
            if message is None:
                return
                
            submitted = False
            current_keyboard = None
            
            while True:          
                button_event = await conv.wait_event(self.keyboard_callback(user_id), timeout = 180)
                button_data = button_event.data.decode('utf8')

                await button_event.answer()
                                            
                if button_data == "group_submit":
                    await message.edit("Submitted", buttons=None)
                    submitted = True
                    break
                elif button_data == "group_cancel":
                    # TODO: reset the group to initial state and set current_page to 0
                    await message.edit("Canceled", buttons=None)
                    break
                
                elif "group_plus" in button_data:
                    name = button_data.split("_")[2]
                    self.group[name] += 1
                elif "group_minus" in button_data:
                    name = button_data.split("_")[2]
                    self.group[name] -= 1 if self.group[name] > 0 else 0
                    
                elif "group_next" in button_data:
                    # TODO: replace this by the actual nummber of maximal pages
                    self.current_page = min(self.current_page + 1, 2)
                elif "group_prev" in button_data:
                    self.current_page = max(self.current_page - 1, 0)
                    
                group_keyboard = self.getGroupKeyboard()
                if current_keyboard != group_keyboard:
                    current_keyboard = group_keyboard
                    await message.edit("Group View", buttons=group_keyboard)
                
            if submitted == True:
                total = sum(self.group.values())
                result_message = f"added **{total}** coffees:\n"
                for name, value in self.group.items():
                    if value != 0:
                        result_message += f"\t{name}: {value}\n"
                print(result_message)
                await self.send_text(user_id, result_message, False)
                
                # TODO: do something with content of self.group
                
            
            # TODO: reset group to initial state
            
    
    ### helper functions
            
        
            
            
    ### SECTION: Data Management and Export
    
    def export_group_state(self) -> str:
        """
        Export the current group state as JSON string.
        
        Returns:
            JSON string representation of the group state
        """
        return self.group_state.model_dump_json(indent=2)
    
    def import_group_state(self, json_data: str) -> None:
        """
        Import group state from JSON string.
        
        Args:
            json_data: JSON string representation of group state
        """
        self.group_state = GroupState.model_validate_json(json_data)
    
    def get_group_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the current group state.
        
        Returns:
            Dictionary with group statistics and information
        """
        total_coffees = self.group_state.get_total_coffees()
        members_with_orders = sum(1 for count in self.group_state.members.values() if count > 0)
        
        return {
            "total_members": len(self.group_state.members),
            "total_coffees": total_coffees,
            "members_with_orders": members_with_orders,
            "current_page": self.group_state.current_page,
            "current_group": self.group_state.current_group,
            "members_summary": [
                {"name": name, "coffee_count": count}
                for name, count in self.group_state.members.items()
                if count > 0
            ]
        }
    
    def get_timeout(self, operation: str = "default") -> int:
        """
        Get timeout value for a specific operation.
        
        Args:
            operation: Operation type (default, registration, password, group_selection)
            
        Returns:
            Timeout in seconds
        """
        timeouts = self.config.timeouts
        return getattr(timeouts, operation, timeouts.default)
    
    def validate_group_member(self, name: str, coffee_count: int = 0) -> GroupMember:
        """
        Validate and create a GroupMember using Pydantic validation.
        
        Args:
            name: Member name to validate
            coffee_count: Coffee count to validate
            
        Returns:
            Validated GroupMember instance
            
        Raises:
            ValueError: If validation fails
        """
        return GroupMember(name=name, coffee_count=coffee_count)
    
    def create_message_model(self, telegram_message: Any) -> MessageModel:
        """
        Create a MessageModel from a Telegram message object.
        
        Args:
            telegram_message: Telegram message object
            
        Returns:
            MessageModel instance
        """
        return MessageModel.from_telegram_message(telegram_message)
    
    ### SECTION: Keyboard Generation with Pydantic Integration
    
    @property
    def keyboard_confirm(self) -> Any:  # More flexible type
        """Generate confirmation keyboard."""
        return [
            [  
                Button.inline("Yes", b"Yes"), 
                Button.inline("No", b"No")
            ],
        ]
    
    def getGroupKeyboard(self) -> Any:  # More flexible return type
        """
        Return the current group keyboard layout for the UI.
        
        Generates a paginated inline keyboard for coffee ordering with:
        - Member names with +/- buttons for coffee counts
        - Pagination controls for large groups (>15 members)
        - Submit button (when orders > 0) and Cancel button
        
        Returns:
            List of button rows for the inline keyboard
        """
        keyboard_group = []
        total = self.group_state.get_total_coffees()
        
        items = list(self.group_state.members.items())
        pages = len(items) // 15
        
        i_start = self.group_state.current_page * 15
        i_end = ((self.group_state.current_page + 1) * 15) if (self.group_state.current_page < pages) else None
        
        for name, value in items[i_start : i_end]:
            keyboard_group.append([
                Button.inline(str(name), "group_name"),
                Button.inline(str(value), "group_value"),
                Button.inline("+", f"group_plus_{name}"),
                Button.inline("-", f"group_minus_{name}")
            ])
            
        if pages > 0:
            navigation_buttons = []
            if self.current_page > 0:
                navigation_buttons.append(
                    Button.inline("prev", "group_prev")
                )
                
            if self.current_page < pages:
                navigation_buttons.append(
                    Button.inline("next", "group_next")
                )
    
            if navigation_buttons != []:
                keyboard_group.append(navigation_buttons)
            
        keyboard_group.append([
            Button.inline("Cancel", "group_cancel")
        ])
        
        if total > 0:
            keyboard_group[-1].append(Button.inline(f"Submit ({total})", "group_submit"))
        
        return keyboard_group
    
    def get_conversation_timeout(self) -> int:
        """
        Get the default conversation timeout value.
        
        Returns:
            Timeout in seconds for conversations
        """
        return self.config.timeouts.registration
    
    def with_timeout(self, timeout_seconds: int):
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
    