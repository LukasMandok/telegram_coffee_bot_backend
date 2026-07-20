# src/bot/conversation_flows/old_order_flow.py
from __future__ import annotations
import re
from datetime import datetime, date, time, timedelta
from typing import Any, List, Optional
from ..message_flow import ButtonCallback, MessageDefinition, MessageFlow, StateType
from ..message_flow_helpers import CommonCallbacks, NavigationButtons, make_state
from ...common.log import Logger

_logger = Logger("OldOrderFlow")

STATE_MAIN = "main"
STATE_EDIT_DATE = "edit_date"
STATE_EDIT_TIME = "edit_time"
STATE_EXIT_NEXT = "exit_next"

KEY_SELECTED_DATE = "old_order_date"
KEY_SELECTED_TIME = "old_order_time"
KEY_FINAL_DATETIME = "old_order_final_datetime"

# --- PARSERS & VALIDATORS ---

def parse_flexible_date(input_text: str, reference_date: date) -> Optional[date]:
    """
    Parses dates in formats: '17.07.2026', '17.07.', '17.07', '17.', '17'
    Omitting month/year defaults to reference_date's month/year.
    """
    text = input_text.strip().rstrip(".")
    parts = text.split(".")
    
    try:
        if len(parts) == 1 and parts[0].isdigit():
            day = int(parts[0])
            parsed = date(reference_date.year, reference_date.month, day)
        elif len(parts) == 2 and all(p.isdigit() for p in parts):
            day, month = int(parts[0]), int(parts[1])
            parsed = date(reference_date.year, month, day)
        elif len(parts) == 3 and all(p.isdigit() for p in parts):
            day, month = int(parts[0]), int(parts[1])
            year = int(parts[2])
            if year < 100:
                year += 2000
            parsed = date(year, month, day)
        else:
            return None
            
        # Ensure date is not in the future
        if parsed > reference_date:
            return None
        return parsed
    except ValueError:
        return None

def parse_flexible_time(input_text: str) -> Optional[time]:
    """
    Parses times: '14:10', '1410', '14h10min', '14', '2pm', '2:10pm', '2 pm', '2:10 pm', '2h10min pm'
    """
    text = input_text.strip().lower()
    is_pm = "pm" in text
    is_am = "am" in text
    text = re.sub(r"\s*(am|pm)\s*", "", text).strip()
    
    hour = 0
    minute = 0
    
    try:
        if ":" in text:
            parts = text.split(":")
            hour, minute = int(parts[0]), int(parts[1])
        elif "h" in text:
            parts = re.split(r"h|min", text)
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        elif text.isdigit():
            if len(text) <= 2:
                hour = int(text)
                minute = 0
            elif len(text) == 3:
                hour = int(text[:1])
                minute = int(text[1:])
            elif len(text) == 4:
                hour = int(text[:2])
                minute = int(text[2:])
            else:
                return None
        else:
            return None

        if is_pm and hour < 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0
            
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
        return None
    except ValueError:
        return None

# --- TEXT & KEYBOARD BUILDERS ---

async def build_main_text(flow_state, api: Any, user_id: int) -> str:
    return "please specify date and time of the order:"

async def build_main_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    current_date: date = flow_state.get(KEY_SELECTED_DATE, datetime.now().date())
    current_time: time = flow_state.get(KEY_SELECTED_TIME, datetime.now().time())
    
    date_btn_text = current_date.strftime("%d.%m.%Y")
    time_btn_text = current_time.strftime("%H:%M")
    
    return [
        [
            ButtonCallback(f"📅 {date_btn_text}", STATE_EDIT_DATE),
            ButtonCallback(f"⏰ {time_btn_text}", STATE_EDIT_TIME),
        ],
        [
            ButtonCallback("cancel", CommonCallbacks.CANCEL),
            ButtonCallback("next ➡️", STATE_EXIT_NEXT),
        ]
    ]

async def build_edit_date_text(flow_state, api: Any, user_id: int) -> str:
    current_date: date = flow_state.get(KEY_SELECTED_DATE, datetime.now().date())
    return f"the specified date is: {current_date.strftime('%d.%m.%Y')}\nplease enter a new valid date:"

async def build_edit_date_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    today = datetime.now().date()
    # Generate buttons for the last 3 days
    last_3_days = [today - timedelta(days=i) for i in range(1, 4)]
    
    row = []
    for d in reversed(last_3_days):
        label = d.strftime("%d.%m.")
        row.append(ButtonCallback(label, f"set_date:{d.isoformat()}"))
        
    return [
        row,
        [ButtonCallback("back", STATE_MAIN)]
    ]

async def handle_edit_date_buttons(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    if data.startswith("set_date:"):
        iso_str = data.split(":", 1)[1]
        flow_state.set(KEY_SELECTED_DATE, date.fromisoformat(iso_str))
        return STATE_MAIN
    # ADD THIS: Handle going back to main menu
    if data in (STATE_MAIN, CommonCallbacks.BACK, "back"):
        return STATE_MAIN
    return None

async def handle_edit_date_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    today = datetime.now().date()
    parsed = parse_flexible_date(input_text, reference_date=today)
    
    if parsed is None:
        await api.message_manager.send_text(
            user_id, "❌ Invalid or future date! Try formats like 17.07. or 17", vanish=True, conv=True, delete_after=2
        )
        return STATE_EDIT_DATE
        
    flow_state.set(KEY_SELECTED_DATE, parsed)
    return STATE_MAIN

async def build_edit_time_text(flow_state, api: Any, user_id: int) -> str:
    current_time: time = flow_state.get(KEY_SELECTED_TIME, datetime.now().time())
    return f"current specified time is: {current_time.strftime('%H:%M')}\nplease enter a new valid time:"

async def build_edit_time_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    return [
        [
            ButtonCallback("12:00", "set_time:12:00"),
            ButtonCallback("13:00", "set_time:13:00"),
            ButtonCallback("14:00", "set_time:14:00"),
        ],
        [ButtonCallback("back", STATE_MAIN)]
    ]

async def handle_edit_time_buttons(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    if data.startswith("set_time:"):
        time_str = data.split(":", 1)[1]
        parts = time_str.split(":")
        flow_state.set(KEY_SELECTED_TIME, time(int(parts[0]), int(parts[1])))
        return STATE_MAIN
    # ADD THIS: Handle going back to main menu
    if data in (STATE_MAIN, CommonCallbacks.BACK, "back"):
        return STATE_MAIN
    return None

async def handle_edit_time_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    parsed = parse_flexible_time(input_text)
    
    if parsed is None:
        await api.message_manager.send_text(
            user_id, "❌ Invalid time format! Try 14:10, 1410, 2pm, or 14h10min", vanish=True, conv=True, delete_after=2
        )
        return STATE_EDIT_TIME
        
    flow_state.set(KEY_SELECTED_TIME, parsed)
    return STATE_MAIN

async def on_exit_next(flow_state, api: Any, user_id: int) -> None:
    """Combines date and time into a final datetime object stored in flow_state and shared context."""
    d: date = flow_state.get(KEY_SELECTED_DATE, datetime.now().date())
    t: time = flow_state.get(KEY_SELECTED_TIME, datetime.now().time())
    final_dt = datetime.combine(d, t)
    
    flow_state.set(KEY_FINAL_DATETIME, final_dt)
    
    # Write back to the shared dictionary passed by old_order_conversation
    shared = flow_state.get("shared")
    if isinstance(shared, dict):
        shared["final_dt"] = final_dt

# --- FLOW DEFINITION ---

def create_old_order_flow() -> MessageFlow:
    flow = MessageFlow()
    
    flow.add_state(make_state(
        STATE_MAIN,
        text_builder=build_main_text,
        keyboard_builder=build_main_keyboard,
        state_type=StateType.BUTTON,
        defaults={
            KEY_SELECTED_DATE: datetime.now().date(),
            KEY_SELECTED_TIME: datetime.now().time(),
        },
        # <-- ADD THESE TWO LINES:
        route_callback_to_state_id=True,
        route_callback_allowlist=[STATE_EDIT_DATE, STATE_EDIT_TIME, STATE_EXIT_NEXT],
    ))
    
    flow.add_state(make_state(
        STATE_EDIT_DATE,
        text_builder=build_edit_date_text,
        keyboard_builder=build_edit_date_keyboard,
        state_type=StateType.MIXED,
        on_button_press=handle_edit_date_buttons,
        on_input_received=handle_edit_date_input,
        route_callback_to_state_id=True,
        route_callback_allowlist=[STATE_MAIN],
    ))
    
    flow.add_state(make_state(
        STATE_EDIT_TIME,
        text_builder=build_edit_time_text,
        keyboard_builder=build_edit_time_keyboard,
        state_type=StateType.MIXED,
        on_button_press=handle_edit_time_buttons,
        on_input_received=handle_edit_time_input,
        route_callback_to_state_id=True,
        route_callback_allowlist=[STATE_MAIN],
    ))

    flow.add_state(MessageDefinition(
        state_id=STATE_EXIT_NEXT,
        state_type=StateType.BUTTON,
        text="⏳ Proceeding to order...",
        auto_exit_after_render=True,
        on_enter=on_exit_next
    ))
    
    return flow