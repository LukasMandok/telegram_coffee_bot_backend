from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.conversations import ConversationManager, ConversationState


def _make_logger():
    return SimpleNamespace(
        info=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        trace=lambda *args, **kwargs: None,
    )


@pytest.mark.asyncio
async def test_timeout_abort_includes_restart_command_for_registration():
    user_id = 123

    message_manager = SimpleNamespace(
        send_text=AsyncMock(),
        clear_user_messages=AsyncMock(),
    )
    api = SimpleNamespace(message_manager=message_manager)

    # Avoid running ConversationManager.__init__ (it touches global repo state)
    manager = ConversationManager.__new__(ConversationManager)
    manager.api = api
    manager.active_conversations = {user_id: ConversationState(user_id=user_id, step="registration_start")}
    manager.settings_manager = SimpleNamespace()
    manager.logger = _make_logger()
    manager.repo = SimpleNamespace()

    await manager.handle_timeout_abort(user_id, "registration")

    message_manager.send_text.assert_awaited_once()
    sent_text = message_manager.send_text.await_args.args[1]
    assert "Conversation aborted due to inactivity" in sent_text
    assert "/start" in sent_text


@pytest.mark.asyncio
async def test_timeout_abort_has_no_restart_command_for_unknown_context():
    user_id = 456

    message_manager = SimpleNamespace(
        send_text=AsyncMock(),
        clear_user_messages=AsyncMock(),
    )
    api = SimpleNamespace(message_manager=message_manager)

    manager = ConversationManager.__new__(ConversationManager)
    manager.api = api
    manager.active_conversations = {user_id: ConversationState(user_id=user_id, step="unknown_start")}
    manager.settings_manager = SimpleNamespace()
    manager.logger = _make_logger()
    manager.repo = SimpleNamespace()

    await manager.handle_timeout_abort(user_id, "some_new_flow")

    message_manager.send_text.assert_awaited_once()
    sent_text = message_manager.send_text.await_args.args[1]
    assert "Conversation aborted due to inactivity" in sent_text
    assert "Use /" not in sent_text
