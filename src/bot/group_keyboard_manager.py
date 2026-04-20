"""
Group keyboard manager for real-time session synchronization.

This module manages the active group keyboards for each session participant
and handles keyboard creation, real-time updates, and pagination.
"""

from typing import Any, Dict, List, Optional, Protocol, TYPE_CHECKING
from datetime import datetime
from ..common.log import Logger
from ..dependencies.dependencies import get_repo
from .message_flow import PaginationConfig, build_telethon_pagination_nav_keyboard
from .message_flow_ids import CommonCallbacks
from .telethon_models import GroupState
from telethon import Button

if TYPE_CHECKING:
    from ..api.telethon_api import TelethonAPI


class _SessionLike(Protocol):
    id: Any
    group_state: GroupState

    async def save(self) -> None: ...

    async def get_available_coffees(self) -> int: ...

    async def get_total_coffees(self) -> int: ...


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
        self.logger = Logger("GroupKeyboardManager")
        # session_id -> {user_id -> ActiveKeyboard}
        self.active_keyboards: Dict[str, Dict[int, ActiveKeyboard]] = {}

    @staticmethod
    def _compute_total_pages(group_state: GroupState, *, page_size: int) -> int:
        members = list(group_state.members.values()) if group_state.members else []

        archived_count = sum(1 for member in members if member.is_archived)
        non_archived_count = len(members) - archived_count

        has_archived = archived_count > 0
        show_more_button_needed = has_archived and not group_state.show_archived

        total_items = non_archived_count + (archived_count if group_state.show_archived else 0)
        member_total_pages = (total_items + page_size - 1) // page_size if total_items > 0 else 1
        last_page_count = (total_items - page_size * (member_total_pages - 1)) if total_items > 0 else 0
        show_more_on_last_page = show_more_button_needed and last_page_count < page_size

        return member_total_pages + (1 if (show_more_button_needed and not show_more_on_last_page) else 0)
    
    async def _determine_flags_and_message(self, session: _SessionLike) -> tuple[bool, bool, str]:
        """
        Determine warning flags and build complete message text based on coffee availability.
        
        Args:
            session: The coffee session to check
            
        Returns:
            Tuple of (is_insufficient, is_multi_card, message_text)
        """
        # Get available coffees and selected total
        available_coffees = await session.get_available_coffees()
        total_coffees = await session.get_total_coffees()
        
        is_insufficient = False
        is_multi_card = False
        warning_message = None
        
        # Get active cards for availability + display
        active_cards = await self.api.coffee_card_manager.get_active_coffee_cards()

        if total_coffees > 0:
            total_available = sum(card.remaining_coffees for card in active_cards)

            if total_coffees > total_available:
                is_insufficient = True
                warning_message = "⚠️ Not enough coffees remaining on your cards!"
            elif len(active_cards) > 1 and total_coffees > active_cards[0].remaining_coffees:
                is_multi_card = True
                warning_message = "🔄 Coffee orders will be split between multiple cards"
        
        # Build complete message text with optional warning
        card_lines = [
            f"• **{card.name}** (purchased by: {card.purchaser.display_name})"  # type: ignore[union-attr]
            for card in active_cards
        ]

        lines: List[str] = ["☕ **Group Coffee Order**", ""]

        if card_lines:
            lines.extend(card_lines)
            lines.append("")

        lines.append(f"Selected: {total_coffees} coffees  ({available_coffees} available)")

        if warning_message:
            lines.append(warning_message)

        # lines.extend(["", "Select coffee quantities for each person:"])
        message_text = "\n".join(lines)
        
        return is_insufficient, is_multi_card, message_text
    
    async def create_group_keyboard(
        self, 
        group_state: GroupState, 
        user_id: Optional[int] = None,
        current_page: int = 0, 
        is_insufficient: bool = False,
        is_multi_card: bool = False
    ) -> Any:
        """
        Generate a paginated group coffee ordering keyboard.
        
        Creates an inline keyboard for coffee ordering with:
        - Member names with +/- buttons for coffee counts
        - Pagination controls with user-configurable page size (5-20, default 10)
        - Submit button (when orders > 0) and Cancel button
        - Warning icons based on coffee availability
        - 'Show More' button to reveal archived users (if any exist and not shown)
        - User-configurable sorting (alphabetical or by coffee count)
        
        Args:
            group_state: Current state of group coffee ordering
            user_id: Optional Telegram user ID to fetch personalized settings
            current_page: Current page for pagination
            is_insufficient: Whether there are insufficient coffees
            is_multi_card: Whether orders will be split across multiple cards
            
        Returns:
            List of button rows for the inline keyboard
        """
        keyboard_group = []
        total = group_state.get_total_coffees()
        
        # Get user settings for page size and sorting
        page_size = 10  # Default
        sort_by = "alphabetical"  # Default
        if user_id:
            repo = get_repo()
            settings = await repo.get_user_settings(user_id)
            if settings:
                page_size = settings.group_page_size
                sort_by = settings.group_sort_by
        
        # Show only non-archived members, sorted by user preference
        items = [(name, member) for name, member in group_state.members.items() if not member.is_archived]
        if sort_by == "coffee_count":
            # Sort by coffee count (descending), then alphabetically for ties
            items = sorted(items, key=lambda x: (-x[1].coffee_count, x[0].lower()))
        else:
            # Sort alphabetically
            items = sorted(items, key=lambda x: x[0].lower())
        
        # Filter and sort members based on show_archived flag and user preferences
        if group_state.show_archived:
            archived_members = sorted(
                ((name, member) for name, member in group_state.members.items() if member.is_archived),
                key=lambda x: x[0].lower()
            )
            items = items + archived_members
        
        # Check if there are any archived users not currently shown
        has_archived = any(member.is_archived for member in group_state.members.values())
        show_more_button_needed = has_archived and not group_state.show_archived

        # Calculate pagination with potential placement of Show More on the last member page
        total_items = len(items)
        member_total_pages = (total_items + page_size - 1) // page_size if total_items > 0 else 1
        last_page_count = (total_items - page_size * (member_total_pages - 1)) if total_items > 0 else 0
        show_more_on_last_page = show_more_button_needed and last_page_count < page_size

        # Render current page
        if current_page < member_total_pages:
            # Render member rows for this page
            i_start = current_page * page_size
            i_end = min(i_start + page_size, total_items)
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
        if total_pages > 1:
            keyboard_group.extend(
                build_telethon_pagination_nav_keyboard(
                    current_page=current_page + 1,
                    total_pages=total_pages,
                    config=PaginationConfig(),
                    prev_callback=CommonCallbacks.PAGE_PREV,
                    info_callback=CommonCallbacks.PAGE_INFO,
                    next_callback=CommonCallbacks.PAGE_NEXT,
                )
            )

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

    async def handle_member_reset(self, session: _SessionLike, member_name: str) -> None:
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
            await session.save()

            # Propagate update to all active keyboards for this session
            await self.sync_all_keyboards_for_session(session)
        except Exception as e:
            self.logger.error(f"Failed to reset count for '{member_name}': {e}", extra_tag="KEYBOARD", exc_info=e)
    
    async def handle_show_archived(self, session: _SessionLike, user_id: int) -> None:
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
            
            self.logger.debug(f"Showing archived users for session {session.id}", extra_tag="KEYBOARD")
        except Exception as e:
            self.logger.error(f"Failed to show archived users: {e}", extra_tag="KEYBOARD", exc_info=e)
    
    async def create_and_send_keyboard(self, user_id: int, session: _SessionLike, initial_page: int = 0) -> Optional[Any]:
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
        
        # Generate keyboard for this user's page with flags and their personal settings
        keyboard = await self.create_group_keyboard(group_state, user_id, initial_page, is_insufficient, is_multi_card)
        
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
        
        self.logger.debug(f"Registered keyboard for user {user_id} in session {session_id}", extra_tag="KEYBOARD")
    
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
                self.logger.debug(f"Unregistered keyboard for user {user_id} in session {session_id}", extra_tag="KEYBOARD")
            
            # Clean up empty session
            if not self.active_keyboards[session_id]:
                del self.active_keyboards[session_id]
    

    # TODO: this is sus
    async def sync_all_keyboards_for_session(self, session: _SessionLike) -> None:
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
                # Generate updated keyboard for this participant using their current page with flags and personal settings
                keyboard = await self.create_group_keyboard(group_state, participant_user_id, active_keyboard.current_page, is_insufficient, is_multi_card)
                
                # Update the message
                await self.api.bot.edit_message(
                    participant_user_id,
                    active_keyboard.message_id,
                    message_text,
                    buttons=keyboard
                )
                
            except Exception as e:
                self.logger.error(f"Failed to update keyboard for user {participant_user_id}: {e}", extra_tag="KEYBOARD", exc_info=e)
                # Remove invalid keyboard
                self.unregister_keyboard(participant_user_id, session_id)
        
        self.logger.debug(f"Synced {len(keyboards_to_update)} keyboards for session {session_id}", extra_tag="KEYBOARD")
    
    async def handle_pagination(self, session: _SessionLike, user_id: int, direction: str) -> None:
        """
        Handle pagination changes for a specific participant.
        
        Args:
            session: The coffee session
            user_id: Telegram user ID of the participant
            direction: 'next'|'prev'
        """
        session_id = str(session.id)
        if session_id not in self.active_keyboards:
            return
        
        if user_id not in self.active_keyboards[session_id]:
            return
        
        active_keyboard = self.active_keyboards[session_id][user_id]
        
        # Get user's page size setting
        page_size = 10  # Default
        repo = get_repo()
        settings = await repo.get_user_settings(user_id)
        if settings:
            page_size = settings.group_page_size

        total_pages = self._compute_total_pages(session.group_state, page_size=page_size)
        max_page = max(0, total_pages - 1)
        
        if direction == 'next':
            new_page = min(active_keyboard.current_page + 1, max_page)
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
    
    async def sync_single_keyboard(self, session: _SessionLike, user_id: int) -> None:
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
            
            # Generate updated keyboard with this participant's current page and personal settings
            keyboard = await self.create_group_keyboard(group_state, user_id, active_keyboard.current_page, is_insufficient, is_multi_card)
            
            # Update the message
            await self.api.bot.edit_message(
                user_id,
                active_keyboard.message_id,
                message_text,
                buttons=keyboard
            )
            
        except Exception as e:
            self.logger.error(f"Failed to sync keyboard for user {user_id}: {e}", extra_tag="KEYBOARD", exc_info=e)
            self.unregister_keyboard(user_id, session_id)
    
    async def cleanup_session_keyboards(self, session_id: str) -> None:
        """Clean up all keyboards for a completed or cancelled session.

        We delete the keyboard messages (best-effort). If deletion fails, we at least
        remove the inline keyboard markup so users can't keep clicking.
        """

        keyboards = self.active_keyboards.pop(session_id, None)
        if not keyboards:
            return

        for user_id, active_keyboard in list(keyboards.items()):
            try:
                await self.api.bot.delete_messages(user_id, [active_keyboard.message_id])
            except Exception:
                try:
                    msg = await self.api.bot.get_messages(user_id, ids=active_keyboard.message_id)
                    if not msg:
                        continue

                    message_obj = msg[0] if isinstance(msg, list) else msg
                    if not message_obj:
                        continue

                    text = message_obj.message or ""
                    await self.api.bot.edit_message(
                        user_id,
                        active_keyboard.message_id,
                        text,
                        buttons=None,
                    )
                except Exception:
                    pass

        self.logger.debug(f"Cleaned up {len(keyboards)} session keyboards", extra_tag="KEYBOARD")
    
    def get_active_sessions(self) -> List[str]:
        """Get list of session IDs with active keyboards."""
        return list(self.active_keyboards.keys())
    
    def get_session_participant_count(self, session_id: str) -> int:
        """Get number of participants with active keyboards in a session."""
        return len(self.active_keyboards.get(session_id, {}))
