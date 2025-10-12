"""
Group keyboard manager for real-time session synchronization.

This module manages the active group keyboards for each session participant
and handles keyboard creation, real-time updates, and pagination.
"""

from typing import Dict, List, Optional, TYPE_CHECKING, Any
from ..models.coffee_models import CoffeeSession
from .telethon_models import GroupState
from telethon import Button
# from .keyboards import KeyboardManager

if TYPE_CHECKING:
    from ..api.telethon_api import TelethonAPI


class ActiveKeyboard:
    """Represents an active group keyboard for a participant."""
    
    def __init__(self, user_id: int, message_id: int, session_id: str, current_page: int = 0):
        self.user_id = user_id
        self.message_id = message_id
        self.session_id = session_id
        self.current_page = current_page
        self.last_update = None


class GroupKeyboardManager:
    """
    Manages active group keyboards for coffee sessions.
    
    This class handles:
    - Creating group keyboards with pagination
    - Tracking active keyboards for each session participant
    - Real-time synchronization when participants make changes
    - Managing per-participant pagination state
    """
    
    def __init__(self, api: "TelethonAPI"):
        self.api = api
        # session_id -> {user_id -> ActiveKeyboard}
        self.active_keyboards: Dict[str, Dict[int, ActiveKeyboard]] = {}
    
    def create_group_keyboard(self, group_state: GroupState, current_page: int = 0) -> Any:
        """
        Generate a paginated group coffee ordering keyboard.
        
        Creates an inline keyboard for coffee ordering with:
        - Member names with +/- buttons for coffee counts
        - Pagination controls for large groups (>15 members)
        - Submit button (when orders > 0) and Cancel button
        
        Args:
            group_state: Current state of group coffee ordering
            current_page: Current page for pagination
            
        Returns:
            List of button rows for the inline keyboard
        """
        keyboard_group = []
        total = group_state.get_total_coffees()
        
        items = list(group_state.members.items())
        pages = len(items) // 15
        
        i_start = current_page * 15
        i_end = ((current_page + 1) * 15) if (current_page < pages) else None
        
        for name, value in items[i_start : i_end]:
            # value is a GroupMember; display only the coffee_count
            coffee_count = getattr(value, 'coffee_count', 0)
            keyboard_group.append([
                Button.inline(str(name), "group_name"),
                Button.inline(str(coffee_count), "group_value"),
                Button.inline("+", f"group_plus_{name}"),
                Button.inline("-", f"group_minus_{name}")
            ])
            
        if pages > 0:
            navigation_buttons = []
            if current_page > 0:
                navigation_buttons.append(
                    Button.inline("prev", "group_prev")
                )
                
            if current_page < pages:
                navigation_buttons.append(
                    Button.inline("next", "group_next")
                )
    
            if navigation_buttons:
                keyboard_group.append(navigation_buttons)
            
        keyboard_group.append([
            Button.inline("Cancel", "group_cancel")
        ])
        
        if total > 0:
            keyboard_group[-1].append(Button.inline(f"Submit ({total})", "group_submit"))
        
        return keyboard_group
    
    async def create_and_send_keyboard(self, user_id: int, session: CoffeeSession, initial_page: int = 0) -> Optional[Any]:
        """
        Create and send a group keyboard to a user, registering it for sync.
        
        Args:
            user_id: Telegram user ID
            session: The coffee session
            initial_page: Initial page for this user
            
        Returns:
            The sent message object, or None if failed
        """
        # Use the group state directly from the session
        group_state = session.group_state
        
        # Generate keyboard for this user's page
        keyboard = self.create_group_keyboard(group_state, initial_page)
        
        # Send the keyboard message
        message = await self.api.message_manager.send_keyboard(
            user_id, 
            f"☕ **Group Coffee Order**\n"
            f"Total: {await session.get_total_coffees()} coffees\n"
            "Select coffee quantities for each person:", 
            keyboard, 
            True, 
            True
        )
        
        # Register the keyboard if message was sent successfully
        if message and message.id is not None:
            await self.register_keyboard(user_id, message.id, str(session.id), initial_page)
        
        return message
    
    async def register_keyboard(self, user_id: int, message_id: int, session_id: str, current_page: int = 0) -> None:
        """
        Register an active keyboard for a session participant.
        
        Args:
            user_id: Telegram user ID
            message_id: ID of the keyboard message
            session_id: Coffee session ID
            current_page: Initial page for this participant
        """
        if session_id not in self.active_keyboards:
            self.active_keyboards[session_id] = {}
        
        self.active_keyboards[session_id][user_id] = ActiveKeyboard(
            user_id, message_id, session_id, current_page
        )
        
        print(f"📋 [KEYBOARD] Registered keyboard for user {user_id} in session {session_id}")
    
    def unregister_keyboard(self, user_id: int, session_id: str) -> None:
        """
        Unregister a keyboard when participant leaves or completes ordering.
        
        Args:
            user_id: Telegram user ID
            session_id: Coffee session ID
        """
        if session_id in self.active_keyboards:
            if user_id in self.active_keyboards[session_id]:
                del self.active_keyboards[session_id][user_id]
                print(f"📋 [KEYBOARD] Unregistered keyboard for user {user_id} in session {session_id}")
            
            # Clean up empty session
            if not self.active_keyboards[session_id]:
                del self.active_keyboards[session_id]
    

    # TODO: this is sus
    async def sync_all_keyboards_for_session(self, session_id: str) -> None:
        """
        Synchronize all active keyboards for a specific session.
        
        Args:
            session_id: Coffee session ID
        """
        if session_id not in self.active_keyboards:
            return
        
        # Get the updated session
        session = await CoffeeSession.get(session_id)
        if not session or not session.is_active:
            return
        
        # Use the group state directly from the session
        group_state = session.group_state
        
        # Update each participant's keyboard
        keyboards_to_update = list(self.active_keyboards[session_id].items())
        
        for participant_user_id, active_keyboard in keyboards_to_update:
            try:
                # Generate updated keyboard for this participant using their current page
                keyboard = self.create_group_keyboard(group_state, active_keyboard.current_page)
                
                # Update the message
                await self.api.bot.edit_message(
                    participant_user_id,
                    active_keyboard.message_id,
                    f"☕ **Group Coffee Order**\n"
                    f"Total: {await session.get_total_coffees()} coffees\n"
                    "Select coffee quantities for each person:",
                    buttons=keyboard
                )
                
            except Exception as e:
                print(f"❌ [KEYBOARD] Failed to update keyboard for user {participant_user_id}: {e}")
                # Remove invalid keyboard
                self.unregister_keyboard(participant_user_id, session_id)
        
        print(f"🔄 [KEYBOARD] Synced {len(keyboards_to_update)} keyboards for session {session_id}")
    
    async def handle_pagination(self, session: CoffeeSession, user_id: int, direction: str) -> None:
        """
        Handle pagination changes for a specific participant.
        
        Args:
            session: The coffee session
            user_id: Telegram user ID of the participant
            direction: 'next' or 'prev'
        """
        session_id = str(session.id)
        if session_id not in self.active_keyboards:
            return
        
        if user_id not in self.active_keyboards[session_id]:
            return
        
        active_keyboard = self.active_keyboards[session_id][user_id]
        total_pages = (len(session.group_state.members) - 1) // 15 + 1 if session.group_state.members else 1
        
        if direction == 'next':
            new_page = min(active_keyboard.current_page + 1, total_pages - 1)
        elif direction == 'prev':
            new_page = max(active_keyboard.current_page - 1, 0)
        else:
            return
        
        # Update the participant's current page
        active_keyboard.current_page = new_page
        
        # Sync only this participant's keyboard with the new page
        if str(session.id) in self.active_keyboards:
            if user_id in self.active_keyboards[str(session.id)]:
                await self.sync_single_keyboard(session, user_id)
    
    async def sync_single_keyboard(self, session: CoffeeSession, user_id: int) -> None:
        """
        Synchronize a single participant's keyboard.
        
        Args:
            session: The coffee session
            user_id: Telegram user ID of the participant
        """
        session_id = str(session.id)
        if session_id not in self.active_keyboards:
            return
        
        if user_id not in self.active_keyboards[session_id]:
            return
        
        active_keyboard = self.active_keyboards[session_id][user_id]
        
        try:
            # Use the group state directly from the session
            group_state = session.group_state
            
            # Generate updated keyboard with this participant's current page
            keyboard = self.create_group_keyboard(group_state, active_keyboard.current_page)
            
            # Update the message
            await self.api.bot.edit_message(
                user_id,
                active_keyboard.message_id,
                f"☕ **Group Coffee Order**\n"
                f"Total: {await session.get_total_coffees()} coffees\n"
                "Select coffee quantities for each person:",
                buttons=keyboard
            )
            
        except Exception as e:
            print(f"❌ [KEYBOARD] Failed to sync keyboard for user {user_id}: {e}")
            self.unregister_keyboard(user_id, session_id)
    
    async def cleanup_session_keyboards(self, session_id: str) -> None:
        """
        Clean up all keyboards for a completed or cancelled session.
        
        Args:
            session_id: Coffee session ID
        """
        if session_id in self.active_keyboards:
            participant_count = len(self.active_keyboards[session_id])
            del self.active_keyboards[session_id]
            print(f"🧹 [KEYBOARD] Cleaned up {participant_count} keyboards for session {session_id}")
    
    def get_active_sessions(self) -> List[str]:
        """Get list of session IDs with active keyboards."""
        return list(self.active_keyboards.keys())
    
    def get_session_participant_count(self, session_id: str) -> int:
        """Get number of participants with active keyboards in a session."""
        return len(self.active_keyboards.get(session_id, {}))
