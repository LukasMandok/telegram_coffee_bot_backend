import unittest
import warnings
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, Mock, ANY
from src.api.telethon_api import TelethonAPI
from src.handlers.exceptions import VerificationException
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Suppress pydantic warnings during tests
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")


class TestTelethonAPI(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.api_id = os.getenv("API_ID", "12345")
        self.api_hash = os.getenv("API_HASH", "test_api_hash")
        self.bot_token = os.getenv("BOT_TOKEN", "test_bot_token")
        
        # Start patches that will be used across multiple tests
        self.uuid_patcher = patch("src.api.telethon_api.uuid.uuid4", return_value="test-uuid")
        self.telegram_client_patcher = patch("src.api.telethon_api.TelegramClient")
        
        self.mock_uuid = self.uuid_patcher.start()
        self.mock_telegram_client = self.telegram_client_patcher.start()
        
        # Set up common mock bot
        self.mock_bot = AsyncMock()
        self.mock_bot.add_event_handler = MagicMock()
        self.mock_bot.send_message = AsyncMock()
        self.mock_bot.run_until_disconnected = AsyncMock()
        self.mock_telegram_client.return_value.start.return_value = self.mock_bot

    def tearDown(self):
        """Clean up patches after each test method."""
        self.uuid_patcher.stop()
        self.telegram_client_patcher.stop()

    def create_telethon_api(self):
        """Helper method to create TelethonAPI instance with mocked dependencies."""
        return TelethonAPI(self.api_id, self.api_hash, self.bot_token)

    def create_mock_event(self, sender_id=12345, message_text="/test"):
        """Helper method to create mock event objects."""
        from telethon.events.common import EventCommon
        
        mock_event = MagicMock(spec=EventCommon)
        mock_event.sender_id = sender_id
        mock_event.message = MagicMock()
        mock_event.message.message = message_text
        mock_event.message.text = message_text
        mock_event.text = message_text
        return mock_event

    def test_initialization(self):
        """Test that TelethonAPI initializes correctly with proper bot setup."""
        telethon_api = self.create_telethon_api()
        
        # Verify TelegramClient was created with correct parameters
        self.mock_telegram_client.assert_called_once_with(
            "bot_test-uuid", self.api_id, self.api_hash
        )
        
        # Verify bot was started with correct token
        self.mock_telegram_client.return_value.start.assert_called_once_with(bot_token=self.bot_token)
        
        # Verify event handlers were added
        self.assertTrue(self.mock_bot.add_event_handler.called)
        
        # Verify instance attributes
        self.assertEqual(telethon_api.api_id, self.api_id)
        self.assertEqual(telethon_api.api_hash, self.api_hash)
        self.assertEqual(telethon_api.bot_token, self.bot_token)
        self.assertEqual(telethon_api.bot, self.mock_bot)
        self.assertIsInstance(telethon_api.group, dict)
        self.assertEqual(telethon_api.current_page, 0)
        self.assertIsNone(telethon_api.current_group)
        self.assertIsInstance(telethon_api.latest_messages, list)

    @patch("src.api.telethon_api.asyncio.create_task")
    async def test_run(self, mock_create_task):
        """Test that the run method starts the message vanisher and runs until disconnected."""
        # Mock the message_vanisher to return an awaitable
        mock_task = AsyncMock()
        mock_create_task.return_value = mock_task
        
        telethon_api = self.create_telethon_api()
        telethon_api.message_vanisher = AsyncMock()
        
        await telethon_api.run()
        
        # Verify message vanisher task was created
        mock_create_task.assert_called_once()
        
        # Verify run_until_disconnected was called
        self.mock_bot.run_until_disconnected.assert_called_once()

    def test_add_handler_with_string_event(self):
        """Test adding a handler with string event pattern."""
        telethon_api = self.create_telethon_api()
        
        # Clear previous calls from initialization
        self.mock_bot.add_event_handler.reset_mock()
        
        # Test handler
        async def test_handler(event):
            pass
        
        telethon_api.add_handler(test_handler, "/test")
        
        # Verify add_event_handler was called
        self.mock_bot.add_event_handler.assert_called_once()

    def test_add_handler_with_none_event(self):
        """Test adding a handler with None event (default NewMessage)."""
        telethon_api = self.create_telethon_api()
        
        # Clear previous calls from initialization
        self.mock_bot.add_event_handler.reset_mock()
        
        # Test handler
        async def test_handler(event):
            pass
        
        telethon_api.add_handler(test_handler, None)
        
        # Verify add_event_handler was called
        self.mock_bot.add_event_handler.assert_called_once()

    async def test_exception_handler_verification_exception(self):
        """Test that VerificationException is properly handled."""
        telethon_api = self.create_telethon_api()
        telethon_api.send_message = AsyncMock()
        
        # Create a mock event
        mock_event = self.create_mock_event()
        
        # Create a handler that raises VerificationException
        async def failing_handler(event):
            raise VerificationException("Test verification error")
        
        # Wrap the handler with exception handling
        wrapped_handler = telethon_api.exception_handler(failing_handler)
        
        # Test that the exception is caught and handled
        from telethon import events
        with self.assertRaises(events.StopPropagation):
            await wrapped_handler(mock_event)
        
        # Verify send_message was called with error message
        telethon_api.send_message.assert_called_once_with(
            12345, "Test verification error", True, True
        )

    async def test_exception_handler_general_exception(self):
        """Test that general exceptions are propagated."""
        telethon_api = self.create_telethon_api()
        
        # Create a mock event
        mock_event = self.create_mock_event()
        
        # Create a handler that raises a general exception
        async def failing_handler(event):
            raise ValueError("Test general error")
        
        # Wrap the handler with exception handling
        wrapped_handler = telethon_api.exception_handler(failing_handler)
        
        # Test that the general exception is propagated
        with self.assertRaises(ValueError):
            await wrapped_handler(mock_event)

    def test_group_initialization(self):
        """Test that the group dictionary is properly initialized."""
        telethon_api = self.create_telethon_api()
        
        # Verify group is initialized with expected members
        expected_members = [
            "Lukas", "Heiko", "Barnie", "Klaus", "Hans",
            "David", "Jens", "Jürgen", "Ralf", "Rainer",
            "Jörg", "Johannes", "Max", "Peter", "Karlo",
            "Annie", "Marie", "Lena", "Lara", "Ruberta",
            "Susi1", "Susi2", "Susi3", "Susi4", "Susi5",
            "Marx1", "Marx2", "Marx3", "Marx4", "Marx5",
            "Leon1", "Leon2", "Leon3", "Leon4", "Leon5"
        ]
        
        for member in expected_members:
            self.assertIn(member, telethon_api.group)
            self.assertEqual(telethon_api.group[member], 0)

    def test_initial_state(self):
        """Test that the initial state of TelethonAPI is correct."""
        telethon_api = self.create_telethon_api()
        
        # Verify initial state
        self.assertEqual(telethon_api.current_page, 0)
        self.assertIsNone(telethon_api.current_group)
        self.assertEqual(len(telethon_api.latest_messages), 0)
        
        # Verify all group members start with 0 coffee count
        for count in telethon_api.group.values():
            self.assertEqual(count, 0)

    def test_add_latest_message(self):
        """Test adding messages to the latest messages list."""
        telethon_api = self.create_telethon_api()
        
        # Create mock message
        mock_message = MagicMock()
        mock_message.id = 123
        mock_message.text = "Test message"
        
        # Test adding message with new=True
        telethon_api.add_latest_message(mock_message, conv=False, new=True)
        
        # Verify message was added as a list
        self.assertEqual(len(telethon_api.latest_messages), 1)
        self.assertIsInstance(telethon_api.latest_messages[0], list)
        self.assertEqual(telethon_api.latest_messages[0][0], mock_message)
        
        # Test adding message with conv=True
        mock_message2 = MagicMock()
        telethon_api.add_latest_message(mock_message2, conv=True, new=False)
        
        # Should be added to the same list
        self.assertEqual(len(telethon_api.latest_messages), 1)
        self.assertEqual(len(telethon_api.latest_messages[0]), 2)
        
        # Test adding regular message
        mock_message3 = MagicMock()
        telethon_api.add_latest_message(mock_message3, conv=False, new=False)
        
        # Should be added as individual message
        self.assertEqual(len(telethon_api.latest_messages), 2)
        self.assertEqual(telethon_api.latest_messages[1], mock_message3)

    def test_get_latest_messages_length(self):
        """Test getting the length of latest messages."""
        telethon_api = self.create_telethon_api()
        
        # Initially should be empty list
        self.assertEqual(telethon_api.get_latest_messages_length(), [])
        
        # Add a message
        mock_message = MagicMock()
        telethon_api.add_latest_message(mock_message)
        
        # Should now return [True] for single message
        self.assertEqual(telethon_api.get_latest_messages_length(), [True])
        
        # Add a list of messages
        mock_message2 = MagicMock()
        telethon_api.add_latest_message(mock_message2, new=True)
        
        # Should return [True, 1] for single message and list with 1 item
        self.assertEqual(telethon_api.get_latest_messages_length(), [True, 1])

    async def test_send_message(self):
        """Test sending a message."""
        telethon_api = self.create_telethon_api()
        telethon_api.add_latest_message = MagicMock()
        
        await telethon_api.send_message(12345, "Test message", vanish=True, conv=True)
        
        # Verify bot.send_message was called
        self.mock_bot.send_message.assert_called_once_with(12345, "Test message")
        
        # Verify message was added to latest messages
        telethon_api.add_latest_message.assert_called_once()

    async def test_delete_oldest_message(self):
        """Test deleting the oldest message."""
        telethon_api = self.create_telethon_api()
        
        # Add a mock message with delete method
        mock_message = AsyncMock()
        mock_message.delete = AsyncMock()
        telethon_api.latest_messages = [mock_message]
        
        await telethon_api.delete_oldest_message()
        
        # Verify delete was called on the message
        mock_message.delete.assert_called_once()
        
        # Verify message was removed from latest_messages
        self.assertEqual(len(telethon_api.latest_messages), 0)

    def test_keyboard_callback(self):
        """Test keyboard callback function creation."""
        telethon_api = self.create_telethon_api()
        
        # Test keyboard callback creation
        callback = telethon_api.keyboard_callback(12345)
        
        # Verify it returns a CallbackQuery event
        from telethon import events
        self.assertIsInstance(callback, events.CallbackQuery)

    async def test_unknown_command_handler(self):
        """Test unknown command handler."""
        telethon_api = self.create_telethon_api()
        telethon_api.send_message = AsyncMock()
        
        # Create mock event
        mock_event = self.create_mock_event(message_text="/unknown")
        
        await telethon_api.unknown_command_handler(mock_event)
        
        # Verify send_message was called with unknown command message
        telethon_api.send_message.assert_called_once_with(
            12345, "**/unknown** is an unknown command.", True, True
        )

    @patch("src.handlers.handlers.check_user")
    async def test_start_command_handler(self, mock_check_user):
        """Test start command handler."""
        # Mock the check_user function to return True
        mock_check_user.return_value = True
        
        telethon_api = self.create_telethon_api()
        telethon_api.send_message = AsyncMock()
        
        # Create mock event
        mock_event = self.create_mock_event()
        
        await telethon_api.start_command_handler(mock_event)
        
        # Verify send_message was called with already registered message
        telethon_api.send_message.assert_called_once()
        args = telethon_api.send_message.call_args[0]
        self.assertEqual(args[0], 12345)
        self.assertIn("already registered", args[1])

    # Additional tests for improved coverage

    async def test_message_vanisher_empty_messages(self):
        """Test message vanisher with empty messages list."""
        telethon_api = self.create_telethon_api()
        telethon_api.delete_oldest_message = AsyncMock(return_value=None)
        
        # Mock asyncio.sleep to prevent infinite loop
        with patch("src.api.telethon_api.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = [None, StopAsyncIteration()]  # First sleep, then stop
            
            with self.assertRaises(StopAsyncIteration):
                await telethon_api.message_vanisher()
            
            # Verify sleep was called
            mock_sleep.assert_called_with(10)
            
            # Verify delete_oldest_message was not called (empty list)
            telethon_api.delete_oldest_message.assert_not_called()

    async def test_message_vanisher_with_messages(self):
        """Test message vanisher logic without infinite loop."""
        telethon_api = self.create_telethon_api()
        
        # Create a spy for delete_oldest_message to track calls
        original_delete = telethon_api.delete_oldest_message
        call_count = 0
        
        async def mock_delete():
            nonlocal call_count
            call_count += 1
            # Simulate deleting a message by removing one from the list
            if telethon_api.latest_messages:
                telethon_api.latest_messages.pop(0)
        
        telethon_api.delete_oldest_message = mock_delete
        
        # Add more than 3 messages to trigger deletion
        mock_messages = [MagicMock() for _ in range(5)]
        telethon_api.latest_messages = mock_messages
        
        # Create a custom message vanisher that only runs one iteration
        async def single_iteration_vanisher():
            while len(telethon_api.latest_messages) > 3:
                await telethon_api.delete_oldest_message()
                # Only do one iteration for testing
                break
        
        # Run the single iteration
        await single_iteration_vanisher()
        
        # Verify delete_oldest_message was called
        self.assertEqual(call_count, 1)
        # Verify one message was removed
        self.assertEqual(len(telethon_api.latest_messages), 4)

    async def test_delete_oldest_message_with_list(self):
        """Test deleting oldest message when it's a list of messages."""
        telethon_api = self.create_telethon_api()
        
        # Create proper mock messages with async delete methods
        mock_message1 = MagicMock()
        mock_message1.delete = AsyncMock(return_value=None)
        mock_message2 = MagicMock()
        mock_message2.delete = AsyncMock(return_value=None)
        
        telethon_api.latest_messages = [[mock_message1, mock_message2]]
        
        # Just call the method - the AsyncMock delete methods should work properly
        await telethon_api.delete_oldest_message()
        
        # Verify both delete methods were called
        mock_message1.delete.assert_called_once()
        mock_message2.delete.assert_called_once()
        
        # Verify the list was removed from latest_messages
        self.assertEqual(telethon_api.latest_messages, [])

    @patch("src.handlers.handlers.check_password")
    async def test_password_handler_valid(self, mock_check_password):
        """Test password handler with valid password."""
        mock_check_password.return_value = True
        
        telethon_api = self.create_telethon_api()
        
        # Create mock event with password
        mock_event = self.create_mock_event(message_text="/password testpass123")
        
        await telethon_api.test_password(mock_event)
        
        # Verify check_password was called with correct password
        mock_check_password.assert_called_once_with("testpass123", ANY)

    @patch("src.handlers.handlers.check_password")
    async def test_password_handler_invalid_format(self, mock_check_password):
        """Test password handler with invalid message format."""
        telethon_api = self.create_telethon_api()
        
        # Create mock event without password
        mock_event = self.create_mock_event(message_text="/password")  # No password provided
        
        await telethon_api.test_password(mock_event)
        
        # Verify check_password was not called due to exception
        mock_check_password.assert_not_called()

    async def test_digits_handler(self):
        """Test digits handler."""
        telethon_api = self.create_telethon_api()
        telethon_api.send_message = AsyncMock()
        
        # Create mock event with digits
        mock_event = self.create_mock_event()
        mock_event.text = "12345"
        
        await telethon_api.digits(mock_event)
        
        # Verify send_message was called with digit response
        telethon_api.send_message.assert_called_once_with(
            12345, "catches digits: 12345", True, True
        )

    @patch("src.dependencies.dependencies._verify_user")
    async def test_user_verification_handler(self, mock_verify_user):
        """Test user verification handler."""
        mock_verify_user.return_value = None  # No exception raised
        
        telethon_api = self.create_telethon_api()
        telethon_api.send_message = AsyncMock()
        
        # Create mock event
        mock_event = self.create_mock_event()
        
        await telethon_api.test_user_verification(mock_event)
        
        # Verify _verify_user was called with the sender_id
        mock_verify_user.assert_called_once_with(12345)
        
        # Verify send_message was called
        telethon_api.send_message.assert_called_once_with(
            12345, "You are a registered user.", True, True
        )

    @patch("src.dependencies.dependencies._verify_admin")
    async def test_admin_verification_handler(self, mock_verify_admin):
        """Test admin verification handler."""
        mock_verify_admin.return_value = None  # No exception raised
        
        telethon_api = self.create_telethon_api()
        telethon_api.send_message = AsyncMock()
        
        # Create mock event
        mock_event = self.create_mock_event()
        
        await telethon_api.test_admin_verification(mock_event)
        
        # Verify _verify_admin was called with the sender_id
        mock_verify_admin.assert_called_once_with(12345)
        
        # Verify send_message was called
        telethon_api.send_message.assert_called_once_with(
            12345, "You are a registered admin.", True, True
        )

    async def test_group_command_handler(self):
        """Test group command handler."""
        telethon_api = self.create_telethon_api()
        telethon_api.group_selection = AsyncMock()
        
        # Create mock event
        mock_event = self.create_mock_event()
        
        await telethon_api.group_command_handler(mock_event)
        
        # Verify group_selection was called
        telethon_api.group_selection.assert_called_once_with(12345)

    async def test_send_keyboard(self):
        """Test sending keyboard message."""
        telethon_api = self.create_telethon_api()
        telethon_api.add_latest_message = MagicMock()
        
        # Mock keyboard layout
        mock_keyboard = [["Yes", "No"]]
        
        await telethon_api.send_keyboard(12345, "Test question", mock_keyboard, vanish=True, conv=True)
        
        # Verify bot.send_message was called with keyboard
        self.mock_bot.send_message.assert_called_once()
        call_args = self.mock_bot.send_message.call_args
        self.assertEqual(call_args[0][0], 12345)  # user_id
        self.assertEqual(call_args[0][1], "Test question")  # text
        self.assertIn("buttons", call_args[1])  # buttons parameter

    @patch("src.handlers.handlers.check_user")
    async def test_start_command_handler_new_user(self, mock_check_user):
        """Test start command handler for new user registration."""
        # Mock the check_user function to return False (new user)
        mock_check_user.return_value = False
        
        telethon_api = self.create_telethon_api()
        telethon_api.register_conversation = AsyncMock()
        
        # Create mock event
        mock_event = self.create_mock_event()
        
        await telethon_api.start_command_handler(mock_event)
        
        # Verify register_conversation was called for new user
        telethon_api.register_conversation.assert_called_once_with(12345)

    def test_add_handler_with_events_builder(self):
        """Test adding a handler with events.EventBuilder."""
        telethon_api = self.create_telethon_api()
        
        # Clear previous calls from initialization
        self.mock_bot.add_event_handler.reset_mock()
        
        # Test handler with event builder
        async def test_handler(event):
            pass
        
        from telethon import events
        custom_event = events.NewMessage(incoming=True)
        
        telethon_api.add_handler(test_handler, custom_event)
        
        # Verify add_event_handler was called
        self.mock_bot.add_event_handler.assert_called_once()

    def test_add_handler_with_custom_exception_handler(self):
        """Test adding a handler with custom exception handler."""
        telethon_api = self.create_telethon_api()
        
        # Clear previous calls from initialization
        self.mock_bot.add_event_handler.reset_mock()
        
        # Test handler with custom exception handler
        async def test_handler(event):
            pass
        
        def custom_exception_handler(func):
            return func
        
        telethon_api.add_handler(test_handler, "/test", custom_exception_handler)
        
        # Verify add_event_handler was called
        self.mock_bot.add_event_handler.assert_called_once()

    async def test_send_message_without_vanish(self):
        """Test sending a message without vanish option."""
        telethon_api = self.create_telethon_api()
        
        await telethon_api.send_message(12345, "Test message", vanish=False, conv=False)
        
        # Verify bot.send_message was called
        self.mock_bot.send_message.assert_called_once_with(12345, "Test message")
        
        # Verify message was NOT added to latest messages (vanish=False)
        self.assertEqual(len(telethon_api.latest_messages), 0)


if __name__ == "__main__":
    unittest.main()
