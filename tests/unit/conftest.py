"""
Shared test fixtures and utilities for unit tests.

Sets up mock modules for src.bot.message_flow and src.bot.message_flow_helpers
to avoid circular imports during test collection.
"""

import types
import sys
from dataclasses import dataclass
from enum import Enum


class MessageAction(Enum):
    EDIT = "edit"
    AUTO = "auto"


class StateType(Enum):
    TEXT_INPUT = "text_input"


@dataclass
class PaginationConfig:
    page_size: int = 10
    items_per_row: int = 1
    show_page_numbers: bool = True
    show_close_button: bool = True
    extras_position: str = "below"


@dataclass
class ButtonCallback:
    text: str
    callback_data: str


class MessageDefinition:
    """Flexible MessageDefinition that accepts arbitrary kwargs."""
    def __init__(self, state_id: str, **kwargs):
        self.state_id = state_id
        for k, v in kwargs.items():
            setattr(self, k, v)
        # Ensure common attributes exist
        self.state_type = getattr(self, 'state_type', StateType.TEXT_INPUT)
        self.on_button_press = getattr(self, 'on_button_press', None)
        self.input_storage_key = getattr(self, 'input_storage_key', None)
        self.on_input_received = getattr(self, 'on_input_received', None)


class MessageFlow:
    def __init__(self):
        self.states = {}

    def add_state(self, message_def):
        self.states[message_def.state_id] = message_def

    def get_state(self, state_id):
        return self.states.get(state_id)


# Setup message_flow fake module
fake_mf = types.ModuleType("src.bot.message_flow")
fake_mf.ButtonCallback = ButtonCallback
fake_mf.MessageAction = MessageAction
fake_mf.StateType = StateType
fake_mf.MessageDefinition = MessageDefinition
fake_mf.MessageFlow = MessageFlow
fake_mf.PaginationConfig = PaginationConfig
sys.modules["src.bot.message_flow"] = fake_mf


# Setup message_flow_helpers fake module
fake_helpers = types.ModuleType("src.bot.message_flow_helpers")


class CommonCallbacks:
    BACK = "back"
    CLOSE = "close"
    CANCEL = "cancel"


class CommonStateIds:
    EXIT = "exit"


class ExitStateBuilder:
    @staticmethod
    def create(state_id: str, text: str, timeout: int = 1, delete_after: int = 0):
        return MessageDefinition(state_id=state_id, text_builder=lambda *_: text)


class NavigationButtons:
    @staticmethod
    def back():
        return ButtonCallback("◁ Back", CommonCallbacks.BACK)

    @staticmethod
    def cancel():
        return ButtonCallback("✖ Cancel", CommonCallbacks.CANCEL)


class StagingManager:
    def __init__(self, flow_state, key: str):
        self.flow_state = flow_state
        self.key = key

    def get_staged(self):
        return self.flow_state.get(self.key, {})

    def stage(self, item_key, value):
        staged = dict(self.get_staged() or {})
        staged[item_key] = value
        self.flow_state.set(self.key, staged)

    def unstage(self, item_key):
        staged = dict(self.get_staged() or {})
        staged.pop(item_key, None)
        self.flow_state.set(self.key, staged)

    def clear(self):
        self.flow_state.set(self.key, {})


class IntegerParser:
    def parse(self, text: str):
        try:
            return int(text)
        except Exception:
            return None


def make_state(state_id, **kwargs):
    md = MessageDefinition(state_id=state_id, **kwargs)
    md.state_type = kwargs.get("state_type", MessageAction)
    return md


def toggle_button(val, label, cb_data):
    return ButtonCallback(label, cb_data)


async def apply_update_or_notify(flow_state, api, user_id, coro, **kwargs):
    if coro is None:
        return False
    await coro
    return True


fake_helpers.CommonCallbacks = CommonCallbacks
fake_helpers.CommonStateIds = CommonStateIds
fake_helpers.ExitStateBuilder = ExitStateBuilder
fake_helpers.NavigationButtons = NavigationButtons
fake_helpers.StagingManager = StagingManager
fake_helpers.apply_update_or_notify = apply_update_or_notify
fake_helpers.make_state = make_state
fake_helpers.toggle_button = toggle_button
fake_helpers.IntegerParser = IntegerParser
sys.modules["src.bot.message_flow_helpers"] = fake_helpers
