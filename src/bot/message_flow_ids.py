"""Shared identifiers (callbacks, flow-data keys, state ids) for MessageFlow-based conversations.

This module is intentionally dependency-free so it can be imported from both
`message_flow.py` and helper/flow modules without circular imports.
"""


class CommonCallbacks:
    """Common callback_data constants used across message flows."""

    CANCEL = "cancel"
    CLOSE = "close"
    DONE = "done"
    BACK = "back"

    CONFIRM = "confirm"
    YES = "yes"
    NO = "no"

    NEXT = "next"
    PREV = "prev"
    UNDO = "undo"
    SAVE = "save"

    PAGE_NEXT = "page_next"
    PAGE_PREV = "page_prev"
    PAGE_INFO = "page_info"


class CommonFlowKeys:
    """Common flow_state.flow_data keys used across message flows."""

    AFTER_CANCEL = "after_cancel"
    SELECTED_CARD_ID = "selected_card_id"


class CommonStateIds:
    """Common state_id constants used across message flows."""

    MENU = "menu"
    CLOSE_CONFIRM = "close_confirm"
    CLOSE_EXECUTE = "close_execute"

    EXIT_CANCELLED = "exit_cancelled"
    EXIT_SUCCESS = "exit_success"


class DebtQuickConfirmCallbacks:
    """Callback IDs for debtor-side 'already paid?' confirmation messages."""

    PREFIX = "debt_quick_confirm:"
    YES_PREFIX = "debt_quick_confirm:yes:"
    NO_PREFIX = "debt_quick_confirm:no:"

    QUESTION_TEXT = "Did you already pay?"
    YES_TEXT = "Yes"
    NO_TEXT = "No"
