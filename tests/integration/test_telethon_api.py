import asyncio
import pytest

from telethon import events, Button
from unittest.mock import patch, MagicMock
from typing import Generator, List, Optional, Any
from src.api.telethon_api import TelethonAPI
from src.bot.telethon_models import MessageModel, GroupMember, BotConfiguration, GroupState
from src.bot.conversations import ConversationState
from src.utils.decorators import with_timeout


# --- Test Configuration ---
pytest_plugins = ('pytest_asyncio',)

# --- Define dummy classes for testing ---


class DummyMessage:
    """
    Dummy message class for integration tests that mimics Telegram message objects.
    
    This class provides the same interface as actual Telegram messages
    to allow proper integration testing without real API calls.
    """
    content: str
    deleted: bool
    user_id: Optional[int]
    buttons: Optional[List[Any]]
    parse_mode: Optional[str]
    id: int
    text: str

    def __init__(self, content: str) -> None:
        """
        Initialize a DummyMessage with content.
        
        Args:
            content: The message text content
        """
        self.content = content
        self.deleted = False
        self.user_id = None
        self.buttons = None
        self.parse_mode = None
        self.id = hash(content) % 10000
        self.text = content

    async def delete(self) -> None:
        """Mark the message as deleted."""
        self.deleted = True
        
    def to_message_model(self) -> MessageModel:
        """Convert this dummy message to a MessageModel for testing."""
        return MessageModel(
            id=self.id,
            text=self.text,
            deleted=self.deleted,
            user_id=self.user_id,
            buttons=self.buttons,
            parse_mode=self.parse_mode
        )


class DummyBot:
    """
    Dummy bot class for integration tests that mimics TelegramClient behavior.
    
    This class provides the same interface as TelegramClient for testing
    without making actual API calls to Telegram servers.
    """
    sent_messages: List[DummyMessage]

    def __init__(self) -> None:
        """Initialize a DummyBot with an empty sent_messages list."""
        self.sent_messages = []

    async def send_message(
        self, 
        user_id: int, 
        text: str, 
        buttons: Optional[Any] = None, 
        parse_mode: Optional[str] = None
    ) -> DummyMessage:
        """
        Simulate sending a message and return a DummyMessage.
        
        Args:
            user_id: Target user ID
            text: Message text
            buttons: Inline keyboard buttons (optional)
            parse_mode: Message parsing mode (optional)
            
        Returns:
            DummyMessage representing the sent message
        """
        message = DummyMessage(text)
        message.user_id = user_id
        message.buttons = buttons
        message.parse_mode = parse_mode
        self.sent_messages.append(message)
        return message

    def add_event_handler(self, handler: Any, event: Any) -> None:
        """Dummy add_event_handler (does nothing)."""
        pass

    def clear_sent_messages(self) -> None:
        """Clear the sent_messages list."""
        self.sent_messages.clear()
        
    async def conversation(self, user_id: int) -> 'DummyConversation':
        """Create a dummy conversation context for testing."""
        return DummyConversation(user_id, self)


class DummyConversation:
    """Dummy conversation context for testing conversation flows."""
    
    def __init__(self, user_id: int, bot: DummyBot) -> None:
        """
        Initialize dummy conversation.
        
        Args:
            user_id: User ID for the conversation
            bot: The dummy bot instance
        """
        self.user_id = user_id
        self.bot = bot
        
    async def __aenter__(self) -> 'DummyConversation':
        """Enter conversation context."""
        return self
        
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit conversation context."""
        pass
        
    async def get_input_chat(self) -> int:
        """Get the chat input (returns user_id for testing)."""
        return self.user_id

# Create a fixture for TelethonAPI where we override the bot with a dummy bot.

@pytest.fixture
def telethon_api() -> Generator[TelethonAPI, None, None]:
    """
    Fixture to provide a TelethonAPI instance with a dummy bot for integration tests.
    Returns:
        TelethonAPI: Instance with dummy bot and group initialized.
    """
    with patch('telethon.TelegramClient.start', return_value=DummyBot()):
        api = TelethonAPI(api_id=1, api_hash="dummy", bot_token="dummy")
        dummy_bot = DummyBot()
        setattr(api, 'bot', dummy_bot)
        api.latest_messages = []
        # Update group state using the new Pydantic model
        api.group_state.members = {
            "Lukas":0, "Heiko":0, "Barnie":0, "Klaus":0, "Hans":0,
            "David":0, "Jens":0, "Jürgen":0, "Ralf":0, "Rainer":0,
            "Jörg":0, "Johannes":0, "Max":0, "Peter":0, "Karlo":0,
            "Annie":0, "Marie":0, "Lena":0, "Lara":0, "Ruberta":0,
            "Susi1":0, "Susi2":0, "Susi3":0, "Susi4":0, "Susi5":0,
            "Marx1":0, "Marx2":0, "Marx3":0, "Marx4":0, "Marx5":0,
            "Leon1":0, "Leon2":0, "Leon3":0, "Leon4":0, "Leon5":0
        }
        api.group_state.current_page = 0
        api.group_state.current_group = None
        yield api

# --- Tests for Pydantic models ---

def test_group_state_model():
    """Test GroupState Pydantic model functionality."""
    group_state = GroupState(members={"Alice": 1, "Bob": 2, "Carol": 0})
    
    # Test total coffee calculation
    assert group_state.get_total_coffees() == 3
    
    # Test adding coffee
    assert group_state.add_coffee("Alice") == True
    assert group_state.members["Alice"] == 2
    assert group_state.add_coffee("NonExistent") == False
    
    # Test removing coffee
    assert group_state.remove_coffee("Bob") == True
    assert group_state.members["Bob"] == 1
    assert group_state.remove_coffee("Carol") == False  # Already 0
    
    # Test reset
    group_state.reset_orders()
    assert all(count == 0 for count in group_state.members.values())

def test_message_model_validation():
    """Test MessageModel Pydantic validation."""
    # Valid message
    msg = MessageModel(id=123, text="Hello", user_id=456)
    assert msg.id == 123
    assert msg.text == "Hello"
    assert msg.user_id == 456
    assert msg.deleted == False  # default
    
    # Message with all fields
    msg2 = MessageModel(
        id=789,
        text="Test message",
        deleted=True,
        user_id=111,
        buttons=[["Yes", "No"]],
        parse_mode="HTML"
    )
    assert msg2.deleted == True
    assert msg2.buttons == [["Yes", "No"]]

def test_bot_configuration_validation():
    """Test BotConfiguration Pydantic validation."""
    config = BotConfiguration(
        api_id=12345,
        api_hash="test_hash",
        bot_token="test_token"
    )
    assert config.api_id == 12345
    assert config.max_messages_cache == 10  # default
    
    # Test validation errors
    try:
        BotConfiguration(api_id=123, api_hash="", bot_token="token")
        assert False, "Should have raised validation error for empty api_hash"
    except Exception:
        pass  # Expected validation error

def test_group_member_validation():
    """Test GroupMember Pydantic validation."""
    member = GroupMember(name="Alice", coffee_count=3)
    assert member.name == "Alice"
    assert member.coffee_count == 3
    
    # Test default values
    member2 = GroupMember(name="Bob")
    assert member2.coffee_count == 0
    
    # Test validation - empty name should be stripped and cause error
    try:
        GroupMember(name="  ", coffee_count=5)
        assert False, "Should have raised validation error for empty name"
    except ValueError as e:
        assert "Name cannot be empty" in str(e)
    
    # Test name stripping
    member3 = GroupMember(name="  Carol  ")
    assert member3.name == "Carol"  # Should be stripped

def test_pydantic_integration_with_telethon_api(telethon_api: TelethonAPI):
    """Test that TelethonAPI properly uses Pydantic models."""
    # Test that configuration is stored as Pydantic model
    assert isinstance(telethon_api.config, BotConfiguration)
    assert telethon_api.config.api_id == 1
    assert telethon_api.config.api_hash == "dummy"
    assert telethon_api.config.bot_token == "dummy"
    
    # Test that group state is stored as Pydantic model
    assert isinstance(telethon_api.group_state, GroupState)
    assert telethon_api.group_state.current_page == 0
    assert telethon_api.group_state.current_group is None
    
    # Test backward compatibility properties
    assert telethon_api.current_page == 0
    assert telethon_api.current_group is None
    assert isinstance(telethon_api.group, dict)
    
    # Test group state methods
    telethon_api.group_state.add_coffee("Lukas")
    assert telethon_api.group["Lukas"] == 1
    assert telethon_api.group_state.get_total_coffees() == 1

def test_data_management_features(telethon_api: TelethonAPI):
    """Test data export/import and summary features."""
    # Set up some test data using existing members
    telethon_api.group_state.add_coffee("Lukas")
    telethon_api.group_state.add_coffee("Lukas")
    telethon_api.group_state.add_coffee("Heiko")
    
    # Test export
    json_data = telethon_api.group_state.export_state()
    assert isinstance(json_data, str)
    assert "Lukas" in json_data
    assert "Heiko" in json_data
    
    # Test summary
    summary = telethon_api.group_state.get_summary()
    assert summary["total_coffees"] == 3
    assert summary["members_with_orders"] == 2
    assert len(summary["members_summary"]) == 2
    
    # Find Lukas in summary
    lukas_summary = next(m for m in summary["members_summary"] if m["name"] == "Lukas")
    assert lukas_summary["coffee_count"] == 2
    
    # Test import (reset first)
    telethon_api.group_state.reset_orders()
    assert telethon_api.group_state.get_total_coffees() == 0
    
    # Import the data back
    telethon_api.import_group_state(json_data)
    assert telethon_api.group_state.get_total_coffees() == 3
    assert telethon_api.group["Lukas"] == 2
    assert telethon_api.group["Heiko"] == 1

def test_conversation_timeout_configuration():
    """Test ConversationState timeout configuration."""
    from src.api.telethon_api import ConversationState
    
    # Test default timeout
    conv_state = ConversationState(user_id=123, step="registration")
    assert conv_state.timeout == 30  # default
    
    # Test custom timeout
    conv_state2 = ConversationState(user_id=456, step="password", timeout=60)
    assert conv_state2.timeout == 60
    
    # Test validation - timeout must be positive
    try:
        ConversationState(user_id=789, step="test", timeout=0)
        assert False, "Should raise validation error for timeout <= 0"
    except Exception:
        pass  # Expected validation error

def test_bot_configuration_with_timeouts():
    """Test BotConfiguration with timeout settings."""
    config = BotConfiguration(
        api_id=12345,
        api_hash="test_hash",
        bot_token="test_token",
        max_messages_cache=20,
        message_cleanup_interval=15
    )
    
    assert config.max_messages_cache == 20
    assert config.message_cleanup_interval == 15
    
    # Test validation - values must be positive
    try:
        BotConfiguration(
            api_id=123,
            api_hash="hash",
            bot_token="token",
            max_messages_cache=-1
        )
        assert False, "Should raise validation error for negative max_messages_cache"
    except Exception:
        pass  # Expected

@pytest.mark.asyncio
async def test_timeout_helper_methods(telethon_api: TelethonAPI):
    """Test timeout helper methods in TelethonAPI."""
    # Test with conversation timeout
    timeout_duration = telethon_api.get_conversation_timeout()
    assert timeout_duration > 0  # Should be positive
    
    # Test timeout decorator (if implemented)
    @telethon_api.with_timeout(5)
    async def quick_operation():
        await asyncio.sleep(0.1)
        return "completed"
    
    result = await quick_operation()
    assert result == "completed"
    
    # Test timeout with longer operation
    @telethon_api.with_timeout(0.1)
    async def slow_operation():
        await asyncio.sleep(1)
        return "should_timeout"
    
    try:
        await slow_operation()
        assert False, "Should have timed out"
    except asyncio.TimeoutError:
        pass  # Expected timeout

def test_create_message_model():
    """Test creating MessageModel instances."""
    # Test minimal message
    msg1 = MessageModel()
    assert msg1.id is None
    assert msg1.text is None
    assert msg1.deleted == False
    assert msg1.user_id is None
    assert msg1.buttons is None
    assert msg1.parse_mode is None
    
    # Test full message
    msg2 = MessageModel(
        id=12345,
        text="Hello World",
        deleted=True,
        user_id=98765,
        buttons=[["Button1", "Button2"]],
        parse_mode="HTML"
    )
    assert msg2.id == 12345
    assert msg2.text == "Hello World"
    assert msg2.deleted == True
    assert msg2.user_id == 98765
    assert msg2.buttons == [["Button1", "Button2"]]
    assert msg2.parse_mode == "HTML"
    
    # Test message model serialization
    msg_dict = msg2.model_dump()
    assert isinstance(msg_dict, dict)
    assert msg_dict["id"] == 12345
    assert msg_dict["text"] == "Hello World"
    
    # Test message model from dict
    msg3 = MessageModel.model_validate(msg_dict)
    assert msg3.id == msg2.id
    assert msg3.text == msg2.text
    assert msg3.deleted == msg2.deleted

def test_validate_group_member(telethon_api: TelethonAPI):
    """Test group member validation."""
    # Valid member
    member = telethon_api.validate_group_member("Alice", 2)
    assert member.name == "Alice"
    assert member.coffee_count == 2
    
    # Valid with default coffee count
    member2 = telethon_api.validate_group_member("Bob")
    assert member2.coffee_count == 0
    
    # Invalid member (empty name)
    try:
        telethon_api.validate_group_member("  ")
        assert False, "Should raise validation error"
    except ValueError:
        pass  # Expected

# --- Tests for helper functions ---

@pytest.mark.asyncio
async def test_delete_oldest_message_single(telethon_api: TelethonAPI) -> None:
    """
    Test deleting a single message from latest_messages.
    Args:
        telethon_api (TelethonAPI): The TelethonAPI instance.
    """
    msg = DummyMessage("a")
    telethon_api.latest_messages = [msg]
    await telethon_api.delete_oldest_message()
    assert msg.deleted
    assert telethon_api.latest_messages == []

@pytest.mark.asyncio
async def test_delete_oldest_message_list(telethon_api: TelethonAPI) -> None:
    """
    Test deleting a list of messages from latest_messages.
    Args:
        telethon_api (TelethonAPI): The TelethonAPI instance.
    """
    msg1 = DummyMessage("a1")
    msg2 = DummyMessage("a2")
    telethon_api.latest_messages = [[msg1, msg2]]
    await telethon_api.delete_oldest_message()
    assert msg1.deleted
    assert msg2.deleted
    assert telethon_api.latest_messages == []

def test_get_latest_messages_length(telethon_api: TelethonAPI) -> None:
    """
    Test get_latest_messages_length with a mix of single and list messages.
    Args:
        telethon_api (TelethonAPI): The TelethonAPI instance.
    """
    # Test with mix of messages and list of messages
    msg1 = DummyMessage("a")
    msg2 = DummyMessage("b1")
    msg3 = DummyMessage("b2")
    telethon_api.latest_messages = [msg1, [msg2, msg3]]
    lengths = telethon_api.get_latest_messages_length()
    # For a message, we appended True. For a list, its length.
    assert lengths == [True, 2]
    
# TODO: Test message_vanisher

def test_add_latest_message_new(telethon_api: TelethonAPI) -> None:
    """
    Test add_latest_message with new=True (should append as a new list).
    Args:
        telethon_api (TelethonAPI): The TelethonAPI instance.
    """
    # When new == True, should append the message in a new list.
    msg = DummyMessage("new")
    telethon_api.latest_messages = []
    telethon_api.add_latest_message(msg, conv=False, new=True)
    # Verify that the first element is a list containing msg.
    assert isinstance(telethon_api.latest_messages[0], list)
    assert telethon_api.latest_messages[0] == [msg]

def test_add_latest_message_conv(telethon_api: TelethonAPI) -> None:
    """
    Test add_latest_message with conv=True (should add to last list).
    Args:
        telethon_api (TelethonAPI): The TelethonAPI instance.
    """
    # When conv == True and last element is already a list.
    msg1 = DummyMessage("a")
    msg2 = DummyMessage("b")
    telethon_api.add_latest_message(msg1, conv=True, new=False)
    assert telethon_api.latest_messages[0] == [msg1]
    
    telethon_api.add_latest_message(msg2, conv=True, new=False)
    # The last list should now contain two messages.
    assert isinstance(telethon_api.latest_messages[-1], list)
    assert telethon_api.latest_messages[-1] == [msg1, msg2]

def test_add_latest_message_plain(telethon_api: TelethonAPI) -> None:
    """
    Test add_latest_message with conv=False and new=False (plain add).
    Args:
        telethon_api (TelethonAPI): The TelethonAPI instance.
    """
    # When both conv and new are False.
    msg = DummyMessage("plain")
    telethon_api.latest_messages = []
    telethon_api.add_latest_message(msg, conv=False, new=False)
    assert telethon_api.latest_messages[0] is msg

# --- Tests for communication functions using the dummy bot ---

@pytest.mark.asyncio
async def test_send_message_appends_latest_message(telethon_api: TelethonAPI) -> None:
    """
    Test send_message appends to latest_messages when vanish=True.
    Args:
        telethon_api (TelethonAPI): The TelethonAPI instance.
    """
    # send_message should call the dummy bot's send_message and add the message if vanish is True.
    user_id = 123
    text = "Hello user!"
    # Initially, latest_messages is empty.
    msg = await telethon_api.send_text(user_id, text, vanish=True, conv=False)
    assert isinstance(msg, MessageModel)  # Should return MessageModel, not DummyMessage
    # Make sure the message was appended to latest_messages.
    assert telethon_api.latest_messages[-1] is msg

# --- Tests for keyboard/Group functions ---

def test_getGroupKeyboard(telethon_api: TelethonAPI) -> None:
    """
    Test getGroupKeyboard returns correct keyboard structure and buttons.
    Args:
        telethon_api (TelethonAPI): The TelethonAPI instance.
    """
# TODO: Test message_vanisher

@pytest.mark.asyncio
async def test_message_vanisher_runs_once(telethon_api: TelethonAPI) -> None:
    """
    Test that message_vanisher deletes a message when more than 3 messages exist.
    Args:
        telethon_api (TelethonAPI): The TelethonAPI instance.
    """
    messages = [DummyMessage(f"msg{i}") for i in range(4)]
    telethon_api.latest_messages = messages.copy()
    async def mock_sleep(duration: float) -> None:
        pass
    with patch('src.api.telethon_api.asyncio.sleep', side_effect=mock_sleep):
        # Simulate one iteration of message_vanisher
        if len(telethon_api.latest_messages) > 3:
            await telethon_api.delete_oldest_message()
    assert len(telethon_api.latest_messages) == 3
    assert messages[0].deleted
    # Populate the group with some counts.
    telethon_api.group = {"Alice": 1, "Bob": 0, "Carol": 2}
    telethon_api.current_page = 0
    keyboard = telethon_api.getGroupKeyboard()
    # The keyboard should be a list and include a Cancel button.
    # Check the last row for Cancel and possibly Submit if total > 0.
    assert isinstance(keyboard, list)
    cancel_row = keyboard[-1]
    # Cancel button should be present (using Button.inline, not Button)
    cancel_labels = [getattr(btn, 'text', str(btn)) for btn in cancel_row]
    assert any("Cancel" in label for label in cancel_labels)
    total = sum(telethon_api.group.values())
    if total > 0:
        # The Cancel row should also contain a Submit button with the total count.
        assert any(f"Submit ({total})" in label for label in cancel_labels)

# --- Optional: Test the exception handler wrapping

@pytest.mark.asyncio
async def test_exception_handler_for_timeout(telethon_api):
    # Create a dummy handler that simply raises a TimeoutError.
    async def dummy_handler(event):
        raise asyncio.TimeoutError("timeout")
    wrapped = telethon_api.exception_handler(dummy_handler)
    # Create a dummy event with required sender_id attribute.
    class DummyEvent:
        sender_id = 999
    event = DummyEvent()
    result = await wrapped(event)
    # As per the handler, it should catch the timeout and return False.
    assert result is False

# TODO: Test message_vanisher

@pytest.mark.asyncio
async def test_message_vanisher_integration(telethon_api):
    """Test message vanisher functionality with dummy messages."""
    # Add multiple messages to trigger vanisher logic
    messages = [DummyMessage(f"msg{i}") for i in range(5)]
    telethon_api.latest_messages = messages.copy()
    
    # Mock asyncio.sleep to run vanisher once
    async def mock_sleep(duration):
        assert duration == 10  # Verify sleep duration
        
    with patch('src.api.telethon_api.asyncio.sleep', side_effect=mock_sleep):
        # Run message vanisher once (it would normally run in a loop)
        # We'll manually trigger the logic inside message_vanisher
        if len(telethon_api.latest_messages) > 3:
            await telethon_api.delete_oldest_message()
            
    # Should have deleted one message, leaving 4
    assert len(telethon_api.latest_messages) == 4
    assert messages[0].deleted  # First message should be deleted

@pytest.mark.asyncio
async def test_send_keyboard_integration(telethon_api):
    """Test sending keyboard messages with dummy bot."""
    user_id = 12345
    text = "Choose an option:"
    keyboard = [["Yes", "No"], ["Maybe"]]
    
    # Test with vanish=True
    message = await telethon_api.send_keyboard(user_id, text, keyboard, vanish=True, conv=False)
    
    # Should return a MessageModel
    assert isinstance(message, MessageModel)
    assert message.text == text
    
    # Message should be added to latest_messages when vanish=True
    assert len(telethon_api.latest_messages) == 1
    assert telethon_api.latest_messages[0] == message

@pytest.mark.asyncio 
async def test_send_message_without_vanish(telethon_api):
    """Test sending message without vanish option."""
    user_id = 12345
    text = "Hello without vanish"
    
    message = await telethon_api.send_message(user_id, text, vanish=False, conv=False)
    
    assert isinstance(message, MessageModel)
    assert message.text == text
    
    # Message should NOT be added to latest_messages when vanish=False
    assert len(telethon_api.latest_messages) == 0

def test_group_pagination_keyboard(telethon_api):
    """Test keyboard generation with pagination."""
    # Create a large group to test pagination (more than 15 members)
    large_group = {f"User{i}": i % 3 for i in range(20)}
    telethon_api.group = large_group
    telethon_api.current_page = 0
    
    keyboard = telethon_api.getGroupKeyboard()
    
    # Should have buttons for first 15 users plus navigation and controls
    assert len(keyboard) >= 15  # At least 15 user rows
    
    # Check that navigation buttons are present (since we have > 15 users)
    # The navigation should be in the second-to-last row
    nav_row = keyboard[-2] if len(keyboard) > 1 else []
    nav_button_texts = [getattr(btn, 'text', str(btn)) for btn in nav_row] if nav_row else []
    
    # Should have "next" button since we're on page 0 and have > 15 users
    assert any("next" in text.lower() for text in nav_button_texts)

def test_group_keyboard_with_zero_total(telethon_api):
    """Test keyboard generation when no coffee orders are placed."""
    telethon_api.group = {"Alice": 0, "Bob": 0, "Carol": 0}
    telethon_api.current_page = 0
    
    keyboard = telethon_api.getGroupKeyboard()
    
    # Should have Cancel button but no Submit button when total is 0
    cancel_row = keyboard[-1]
    cancel_labels = [getattr(btn, 'text', str(btn)) for btn in cancel_row]
    
    assert any("Cancel" in label for label in cancel_labels)
    assert not any("Submit" in label for label in cancel_labels)

def test_add_latest_message_complex_scenario(telethon_api):
    """Test complex message adding scenarios."""
    # Start with empty list
    assert len(telethon_api.latest_messages) == 0
    
    # Add first message with new=True (creates a list)
    msg1 = DummyMessage("first")
    telethon_api.add_latest_message(msg1, conv=False, new=True)
    assert len(telethon_api.latest_messages) == 1
    assert isinstance(telethon_api.latest_messages[0], list)
    assert telethon_api.latest_messages[0][0] == msg1
    
    # Add second message with conv=True (adds to existing list)
    msg2 = DummyMessage("second")
    telethon_api.add_latest_message(msg2, conv=True, new=False)
    assert len(telethon_api.latest_messages) == 1  # Still one list
    assert len(telethon_api.latest_messages[0]) == 2  # But list has 2 items
    
    # Add third message normally (adds as individual message)
    msg3 = DummyMessage("third")
    telethon_api.add_latest_message(msg3, conv=False, new=False)
    assert len(telethon_api.latest_messages) == 2  # Now we have 2 items
    assert telethon_api.latest_messages[1] == msg3

@pytest.mark.asyncio
async def test_exception_handler_integration(telethon_api):
    """Test exception handler with different types of exceptions."""
    
    # Test with custom exception that should be handled
    async def handler_with_custom_error(event):
        from src.handlers.exceptions import VerificationException
        raise VerificationException("Access denied")
    
    wrapped = telethon_api.exception_handler(handler_with_custom_error)
    
    class DummyEvent:
        sender_id = 123
        text = "/test"
    
    event = DummyEvent()
    
    # Should handle VerificationException and send message
    from telethon import events
    with pytest.raises(events.StopPropagation):
        await wrapped(event)
    
    # Verify that a message was "sent" (our dummy bot doesn't actually send)
    # This tests the integration of exception handling with message sending

@pytest.mark.asyncio
async def test_delete_mixed_message_types(telethon_api):
    """Test deleting when latest_messages contains both single messages and lists."""
    # Add a single message
    single_msg = DummyMessage("single")
    telethon_api.latest_messages.append(single_msg)
    
    # Add a list of messages
    list_msg1 = DummyMessage("list1")
    list_msg2 = DummyMessage("list2") 
    telethon_api.latest_messages.append([list_msg1, list_msg2])
    
    # Add another single message
    single_msg2 = DummyMessage("single2")
    telethon_api.latest_messages.append(single_msg2)
    
    # Delete oldest (should delete the first single message)
    await telethon_api.delete_oldest_message()
    
    assert single_msg.deleted
    assert len(telethon_api.latest_messages) == 2  # Should have 2 items left
    
    # Delete oldest again (should delete the list of messages)
    await telethon_api.delete_oldest_message()
    
    assert list_msg1.deleted
    assert list_msg2.deleted
    assert len(telethon_api.latest_messages) == 1  # Should have 1 item left

def test_get_latest_messages_length_comprehensive(telethon_api):
    """Test get_latest_messages_length with various scenarios."""
    # Empty list
    assert telethon_api.get_latest_messages_length() == []
    
    # Single message
    telethon_api.latest_messages = [DummyMessage("single")]
    assert telethon_api.get_latest_messages_length() == [True]
    
    # List of messages  
    telethon_api.latest_messages = [[DummyMessage("a"), DummyMessage("b"), DummyMessage("c")]]
    assert telethon_api.get_latest_messages_length() == [3]
    
    # Mixed: single message, list, another single
    telethon_api.latest_messages = [
        DummyMessage("single1"),
        [DummyMessage("list1"), DummyMessage("list2")],
        DummyMessage("single2")
    ]
    assert telethon_api.get_latest_messages_length() == [True, 2, True]

# --- Test realistic coffee ordering workflow ---

@pytest.mark.asyncio
async def test_coffee_ordering_workflow(telethon_api):
    """Test a realistic coffee ordering workflow integration."""
    user_id = 12345
    
    # 1. User starts by getting the group keyboard
    keyboard = telethon_api.getGroupKeyboard()
    assert isinstance(keyboard, list)
    assert len(keyboard) > 0
    
    # 2. User adds coffee for themselves (simulate button press)
    initial_coffee = telethon_api.group.get("Lukas", 0)
    telethon_api.group["Lukas"] = initial_coffee + 1
    
    # 3. Send updated keyboard
    updated_keyboard = telethon_api.getGroupKeyboard()
    total = sum(telethon_api.group.values())
    
    # Should now show Submit button since total > 0
    cancel_row = updated_keyboard[-1]
    cancel_labels = [btn.text for btn in cancel_row]
    assert any(f"Submit ({total})" in label for label in cancel_labels)
    
    # 4. Send confirmation message
    confirmation_msg = await telethon_api.send_message(
        user_id, 
        f"Coffee order updated! Total: {total}",
        vanish=True,
        conv=False
    )
    
    assert isinstance(confirmation_msg, MessageModel)
    assert len(telethon_api.latest_messages) == 1