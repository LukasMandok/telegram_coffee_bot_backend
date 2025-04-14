import asyncio
import pytest
from telethon import events, Button

# Import TelethonAPI from its file
from src.api.telethon_api import TelethonAPI

# --- Define dummy classes for testing ---

class DummyMessage:
    def __init__(self, content):
        self.content = content
        self.deleted = False
    async def delete(self):
        self.deleted = True

class DummyBot:
    async def send_message(self, user_id, text, buttons=None, parse_mode=None):
        # Return a dummy message that contains the text
        return DummyMessage(text)
    def add_event_handler(self, handler, event):
        # Just pass to simulate event handler registration
        pass

# Create a fixture for TelethonAPI where we override the bot with a dummy bot.
@pytest.fixture
def telethon_api():
    # Note: The parameters below can be any dummy values.
    api = TelethonAPI(api_id=1, api_hash="dummy", bot_token="dummy")
    # Override the bot with our dummy bot to avoid real network calls.
    api.bot = DummyBot()
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
    # Cancel button label should be "Cancel"
    cancel_labels = [btn.text for btn in cancel_row if isinstance(btn, Button)]
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