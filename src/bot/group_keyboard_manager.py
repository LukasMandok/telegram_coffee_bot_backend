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
    
    async def _determine_flags_and_message(self, session: CoffeeSession) -> tuple[bool, bool, str]:
        """
        Determine warning flags and build complete message text based on coffee availability.
        
        Args:
            session: The coffee session to check
            
        Returns:
            Tuple of (is_insufficient, is_multi_card, message_text)
        """
        # Get available coffees and total
        available_coffees = await session.get_available_coffees()
        total_coffees = await session.get_total_coffees()
        
        is_insufficient = False
        is_multi_card = False
        warning_message = None
        
        if total_coffees > 0:
            # Get active cards to check availability
            active_cards = await self.api.coffee_card_manager.get_active_coffee_cards()
            total_available = sum(card.remaining_coffees for card in active_cards)
            
            if total_coffees > total_available:
                # Not enough coffees available
                is_insufficient = True
                warning_message = f"⚠️ Not enough coffees remaining on your cards!"
            elif len(active_cards) > 1 and total_coffees > active_cards[0].remaining_coffees:
                # Will use multiple cards
                is_multi_card = True
                warning_message = "🔄 Coffee orders will be split between multiple cards"
        
        # Build complete message text with optional warning
        message_text = (
            f"☕ **Group Coffee Order**\n"
            f"Total: {total_coffees} coffees  ({available_coffees} available)\n"
        )
        if warning_message:
            message_text += f"{warning_message}\n"
        message_text += "\nSelect coffee quantities for each person:"
        
        return is_insufficient, is_multi_card, message_text
    
    async def create_group_keyboard(
        self, 
        group_state: GroupState, 
        current_page: int = 0, 
        is_insufficient: bool = False,
        is_multi_card: bool = False
    ) -> Any:
        """
        Generate a paginated group coffee ordering keyboard.
        
        Creates an inline keyboard for coffee ordering with:
        - Member names with +/- buttons for coffee counts
        - Pagination controls for large groups (>5 members)
        - Submit button (when orders > 0) and Cancel button
        - Warning icons based on coffee availability
        - 'Show More' button to reveal archived users (if any exist and not shown)
        
        Args:
            group_state: Current state of group coffee ordering
            current_page: Current page for pagination
            is_insufficient: Whether there are insufficient coffees
            is_multi_card: Whether orders will be split across multiple cards
            
        Returns:
            List of button rows for the inline keyboard
        """
        keyboard_group = []
        total = group_state.get_total_coffees()
        
        # Filter members based on show_archived flag
        if group_state.show_archived:
            # Show active users first (alphabetically), then archived users (alphabetically)
            active_members = sorted(
                ((name, member) for name, member in group_state.members.items() if not member.is_archived),
                key=lambda x: x[0].lower()
            )
            archived_members = sorted(
                ((name, member) for name, member in group_state.members.items() if member.is_archived),
                key=lambda x: x[0].lower()
            )
            items = active_members + archived_members
        else:
            # Show only non-archived members, sorted alphabetically
            items = sorted(
                ((name, member) for name, member in group_state.members.items() if not member.is_archived),
                key=lambda x: x[0].lower()
            )
        
        # Check if there are any archived users not currently shown
        has_archived = any(member.is_archived for member in group_state.members.values())
        show_more_button_needed = has_archived and not group_state.show_archived

        # Calculate pagination with potential placement of Show More on the last member page
        total_items = len(items)
        member_total_pages = (total_items + 4) // 5 if total_items > 0 else 1
        last_page_count = (total_items - 5 * (member_total_pages - 1)) if total_items > 0 else 0
        show_more_on_last_page = show_more_button_needed and last_page_count < 5

        # Render current page
        if current_page < member_total_pages:
            # Render member rows for this page
            i_start = current_page * 5
            i_end = min(i_start + 5, total_items)
            for name, member in items[i_start : i_end]:
                keyboard_group.append([
                    Button.inline(str(name), "group_info"),
                    Button.inline(str(member.coffee_count), f"group_reset_{name}"),
                    Button.inline("-", f"group_minus_{name}"),
                    Button.inline("+", f"group_plus_{name}")
                ])

            # If this is the last member page and there's space left, append Show More here
            if (current_page == member_total_pages - 1) and show_more_on_last_page:
                keyboard_group.append([Button.inline("Show More …", "group_show_archived")])
        else:
            # If beyond member pages, only show the Show More page when it needs its own page
            if show_more_button_needed and not show_more_on_last_page and current_page == member_total_pages:
                keyboard_group.append([Button.inline("Show More …", "group_show_archived")])

        # Pagination navigation
        # Total pages = member pages + 1 if Show More needs its own page
        total_pages = member_total_pages + (1 if (show_more_button_needed and not show_more_on_last_page) else 0)
        max_page = max(0, total_pages - 1)
        
        if total_pages > 1:
            navigation_buttons = []
            if current_page > 0:
                # U+1F8A0 (LEFTWARDS ARROW WITH TAIL FROM BAR) from Supplemental Arrows-C
                navigation_buttons.append(Button.inline("◁  Prev", "group_prev"))

            navigation_buttons.append(
                Button.inline(f"{current_page + 1}/{total_pages}", "group_info")
            )

            if current_page < max_page:
                # U+1F8A1 (RIGHTWARDS ARROW WITH TAIL FROM BAR) from Supplemental Arrows-C
                navigation_buttons.append(Button.inline("Next  ▷", "group_next"))

            if navigation_buttons:
                keyboard_group.append(navigation_buttons)

        keyboard_group.append([
            Button.inline("Cancel", "group_cancel")
        ])

        if total > 0:
            submit_text = f"Submit ({total})"
            if is_insufficient:
                submit_text = f"⚠️ Submit ({total})"
            elif is_multi_card:
                submit_text = f"🔄 Submit ({total})"
            keyboard_group[-1].append(Button.inline(submit_text, "group_submit"))
        
        return keyboard_group

    async def handle_member_reset(self, session: CoffeeSession, member_name: str) -> None:
        """
        Reset the specified member's coffee count to 0 and sync all keyboards.

        Args:
            session: The coffee session
            member_name: The member whose count should be reset
        """
        try:
            group_state = session.group_state
            if not group_state or not group_state.members:
                return

            member = group_state.members.get(member_name)
            if member is None:
                return

            # Support attribute-style or dict-style storage
            if hasattr(member, 'coffee_count'):
                setattr(member, 'coffee_count', 0)
            elif isinstance(member, dict):
                member['coffee_count'] = 0
            else:
                # Fallback: attempt attribute set; if it fails, stop silently
                try:
                    group_state.members[member_name].coffee_count = 0  # type: ignore[attr-defined]
                except Exception:
                    return

            # Propagate update to all active keyboards for this session
            await self.sync_all_keyboards_for_session(session)
        except Exception as e:
            print(f"❌ [KEYBOARD] Failed to reset count for '{member_name}': {e}")
    
    async def handle_show_archived(self, session: CoffeeSession, user_id: int) -> None:
        """
        Toggle the show_archived flag to reveal archived users.
        Keeps the user on their current page.

        Args:
            session: The coffee session
            user_id: The user who pressed the button
        """
        try:
            group_state = session.group_state
            if not group_state:
                return
            
            group_state.show_archived = True
            
            # Save the updated session state
            await session.save()
            
            # Sync all keyboards to show the archived members
            await self.sync_all_keyboards_for_session(session)
            
            print(f"📂 [KEYBOARD] Showing archived users for session {session.id}")
        except Exception as e:
            print(f"❌ [KEYBOARD] Failed to show archived users: {e}")
    
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
        
        # Determine flags and build complete message text
        is_insufficient, is_multi_card, message_text = await self._determine_flags_and_message(session)
        
        # Generate keyboard for this user's page with flags
        keyboard = await self.create_group_keyboard(group_state, initial_page, is_insufficient, is_multi_card)
        
        # Send the keyboard message
        message = await self.api.message_manager.send_keyboard(
            user_id, 
            message_text,
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
    async def sync_all_keyboards_for_session(self, session: CoffeeSession) -> None:
        """
        Synchronize all active keyboards for a specific session.
        
        Args:
            session: Coffee session object
        """
        if not session:
            raise ValueError("Session cannot be None")
        
        session_id = str(session.id)
        if session_id not in self.active_keyboards:
            return
                
        # Use the group state directly from the session
        group_state = session.group_state
        
        # Determine flags and build complete message text
        is_insufficient, is_multi_card, message_text = await self._determine_flags_and_message(session)
        
        # Update each participant's keyboard
        keyboards_to_update = list(self.active_keyboards[session_id].items())
        
        for participant_user_id, active_keyboard in keyboards_to_update:
            try:
                # Generate updated keyboard for this participant using their current page with flags
                keyboard = await self.create_group_keyboard(group_state, active_keyboard.current_page, is_insufficient, is_multi_card)
                
                # Update the message
                await self.api.bot.edit_message(
                    participant_user_id,
                    active_keyboard.message_id,
                    message_text,
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
            direction: 'next'|'prev' or 'group_next'|'group_prev'
        """
        session_id = str(session.id)
        if session_id not in self.active_keyboards:
            return
        
        if user_id not in self.active_keyboards[session_id]:
            return
        
        active_keyboard = self.active_keyboards[session_id][user_id]
        total_pages = (len(session.group_state.members) - 1) // 5 + 1 if session.group_state.members else 1
        
        if direction in ('next', 'group_next'):
            new_page = min(active_keyboard.current_page + 1, total_pages - 1)
        elif direction in ('prev', 'group_prev'):
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
            
            # Determine flags and build complete message text
            is_insufficient, is_multi_card, message_text = await self._determine_flags_and_message(session)
            
            # Generate updated keyboard with this participant's current page
            keyboard = await self.create_group_keyboard(group_state, active_keyboard.current_page, is_insufficient, is_multi_card)
            
            # Update the message
            await self.api.bot.edit_message(
                user_id,
                active_keyboard.message_id,
                message_text,
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
