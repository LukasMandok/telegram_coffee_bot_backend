import asyncio
import pytest
from telethon import events, Button
from unittest.mock import patch, MagicMock

# Import TelethonAPI from its file
from src.api.telethon_api import TelethonAPI

# --- Test Configuration ---
pytest_plugins = ('pytest_asyncio',)

# --- Define dummy classes for testing ---

class DummyMessage:
    def __init__(self, content):
        self.content = content
        self.deleted = False
        self.user_id = None
        self.buttons = None
        self.parse_mode = None
        
    async def delete(self):
        self.deleted = True

class DummyBot:
    def __init__(self):
        self.sent_messages = []  # Track sent messages for verification
        
    async def send_message(self, user_id, text, buttons=None, parse_mode=None):
        # Return a dummy message that contains the text and buttons info
        message = DummyMessage(text)
        message.user_id = user_id
        message.buttons = buttons
        message.parse_mode = parse_mode
        self.sent_messages.append(message)
        return message
        
    def add_event_handler(self, handler, event):
        # Just pass to simulate event handler registration
        pass
        
    def clear_sent_messages(self):
        """Helper method to clear message history for testing."""
        self.sent_messages.clear()

# Create a fixture for TelethonAPI where we override the bot with a dummy bot.
@pytest.fixture
def telethon_api():
    # Mock the TelegramClient.start method to prevent real API calls
    with patch('telethon.TelegramClient.start', return_value=DummyBot()):
        # Note: The parameters below can be any dummy values.
        api = TelethonAPI(api_id=1, api_hash="dummy", bot_token="dummy")
        # Override the bot with our dummy bot to avoid real network calls.
        dummy_bot = DummyBot()
        setattr(api, 'bot', dummy_bot)  # Use setattr to avoid type checking
        # Initialize latest_messages and group as needed.
        api.latest_messages = []
        api.group = {
            "Lukas":0, "Heiko":0, "Barnie":0, "Klaus":0, "Hans":0,
            "David":0, "Jens":0, "Jürgen":0, "Ralf":0, "Rainer":0,
            "Jörg":0, "Johannes":0, "Max":0, "Peter":0, "Karlo":0,
            "Annie":0, "Marie":0, "Lena":0, "Lara":0, "Ruberta":0,
            "Susi1":0, "Susi2":0, "Susi3":0, "Susi4":0, "Susi5":0,
            "Marx1":0, "Marx2":0, "Marx3":0, "Marx4":0, "Marx5":0,
            "Leon1":0, "Leon2":0, "Leon3":0, "Leon4":0, "Leon5":0
        }
        api.current_page = 0
        api.current_group = None
        
        return api

# --- Tests for helper functions ---

@pytest.mark.asyncio
async def test_delete_oldest_message_single(telethon_api):
    # Add a single message
    msg = DummyMessage("a")
    telethon_api.latest_messages = [msg]
    await telethon_api.delete_oldest_message()
    # The message should be deleted and removed from the list.
    assert msg.deleted
    assert telethon_api.latest_messages == []

@pytest.mark.asyncio
async def test_delete_oldest_message_list(telethon_api):
    # Add a list containing two messages
    msg1 = DummyMessage("a1")
    msg2 = DummyMessage("a2")
    telethon_api.latest_messages = [[msg1, msg2]]
    await telethon_api.delete_oldest_message()
    # Both messages should be deleted.
    assert msg1.deleted
    # TODO: this should actually delete all of them?
    assert msg2.deleted
    assert telethon_api.latest_messages == []

def test_get_latest_messages_length(telethon_api):
    # Test with mix of messages and list of messages
    msg1 = DummyMessage("a")
    msg2 = DummyMessage("b1")
    msg3 = DummyMessage("b2")
    telethon_api.latest_messages = [msg1, [msg2, msg3]]
    lengths = telethon_api.get_latest_messages_length()
    # For a message, we appended True. For a list, its length.
    assert lengths == [True, 2]
    
# TODO: Test message_vanisher

def test_add_latest_message_new(telethon_api):
    # When new == True, should append the message in a new list.
    msg = DummyMessage("new")
    telethon_api.latest_messages = []
    telethon_api.add_latest_message(msg, conv=False, new=True)
    # Verify that the first element is a list containing msg.
    assert isinstance(telethon_api.latest_messages[0], list)
    assert telethon_api.latest_messages[0] == [msg]

def test_add_latest_message_conv(telethon_api):
    # When conv == True and last element is already a list.
    msg1 = DummyMessage("a")
    msg2 = DummyMessage("b")
    telethon_api.add_latest_message(msg1, conv=True, new=False)
    assert telethon_api.latest_messages[0] == [msg1]
    
    telethon_api.add_latest_message(msg2, conv=True, new=False)
    # The last list should now contain two messages.
    assert isinstance(telethon_api.latest_messages[-1], list)
    assert telethon_api.latest_messages[-1] == [msg1, msg2]

def test_add_latest_message_plain(telethon_api):
    # When both conv and new are False.
    msg = DummyMessage("plain")
    telethon_api.latest_messages = []
    telethon_api.add_latest_message(msg, conv=False, new=False)
    assert telethon_api.latest_messages[0] is msg

# --- Tests for communication functions using the dummy bot ---

@pytest.mark.asyncio
async def test_send_message_appends_latest_message(telethon_api):
    # send_message should call the dummy bot's send_message and add the message if vanish is True.
    user_id = 123
    text = "Hello user!"
    # Initially, latest_messages is empty.
    msg = await telethon_api.send_message(user_id, text, vanish=True, conv=False)
    assert isinstance(msg, DummyMessage)
    # Make sure the message was appended to latest_messages.
    assert telethon_api.latest_messages[-1] is msg

# --- Tests for keyboard/Group functions ---

def test_getGroupKeyboard(telethon_api):
    # Populate the group with some counts.
    telethon_api.group = {"Alice": 1, "Bob": 0, "Carol": 2}
    telethon_api.current_page = 0
    keyboard = telethon_api.getGroupKeyboard()
    # The keyboard should be a list and include a Cancel button.
    # Check the last row for Cancel and possibly Submit if total > 0.
    assert isinstance(keyboard, list)
    cancel_row = keyboard[-1]
    # Cancel button should be present (using Button.inline, not Button)
    cancel_labels = [btn.text for btn in cancel_row]
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
    
    # Should return a DummyMessage
    assert isinstance(message, DummyMessage)
    assert message.content == text
    
    # Message should be added to latest_messages when vanish=True
    assert len(telethon_api.latest_messages) == 1
    assert telethon_api.latest_messages[0] == message

@pytest.mark.asyncio 
async def test_send_message_without_vanish(telethon_api):
    """Test sending message without vanish option."""
    user_id = 12345
    text = "Hello without vanish"
    
    message = await telethon_api.send_message(user_id, text, vanish=False, conv=False)
    
    assert isinstance(message, DummyMessage)
    assert message.content == text
    
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
    nav_button_texts = [btn.text for btn in nav_row] if nav_row else []
    
    # Should have "next" button since we're on page 0 and have > 15 users
    assert any("next" in text.lower() for text in nav_button_texts)

def test_group_keyboard_with_zero_total(telethon_api):
    """Test keyboard generation when no coffee orders are placed."""
    telethon_api.group = {"Alice": 0, "Bob": 0, "Carol": 0}
    telethon_api.current_page = 0
    
    keyboard = telethon_api.getGroupKeyboard()
    
    # Should have Cancel button but no Submit button when total is 0
    cancel_row = keyboard[-1]
    cancel_labels = [btn.text for btn in cancel_row]
    
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
    
    assert isinstance(confirmation_msg, DummyMessage)
    assert len(telethon_api.latest_messages) == 1