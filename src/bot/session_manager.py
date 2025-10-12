"""
Session manager for handling coffee session lifecycle and coordination.

This module manages the overall coffee session workflow, coordinating between
the GroupKeyboardManager and database operations.
"""

from typing import Dict, List, Optional, TYPE_CHECKING, Tuple, Any, cast
from datetime import datetime
from ..models.coffee_models import CoffeeSession, CoffeeCard, CoffeeOrder
from ..models.beanie_models import TelegramUser
from ..exceptions.coffee_exceptions import (
    SessionNotActiveError, InvalidCoffeeCountError, InsufficientCoffeeError
)
from ..common.log import (
    log_coffee_session_started, log_coffee_session_participant_added,
    log_coffee_session_participant_removed
)
from ..bot.group_state_helpers import initialize_group_state_from_db
from .telethon_models import GroupState

if TYPE_CHECKING:
    from ..api.telethon_api import TelethonAPI


class SessionManager:
    """
    Manages coffee session lifecycle and coordination.
    
    This class handles:
    - Session creation and participant management
    - Coordination with GroupKeyboardManager
    - Session completion and notification
    - Integration with database operations
    """
    
    def __init__(self, api: "TelethonAPI"):
        self.api = api
        self.session: Optional[CoffeeSession] = None
        # Keep track of notification messages sent per session so we can
        # delete them when the session finishes or is cancelled.
        self.session_notifications = {}

    # DB access

    async def get_active_session(self) -> Optional[CoffeeSession]:
        """Get the currently active coffee session."""
        return await CoffeeSession.find_one(CoffeeSession.is_active == True)

    async def get_active_session_for_user(self, user_id: int) -> Optional[CoffeeSession]:
        """Get the active session initiated by a user."""
        user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
        if not user:
            return None
        
        return await CoffeeSession.find_one(
            CoffeeSession.initiator == user,
            CoffeeSession.is_active == True
        )
    
    async def get_user_active_session(self, user_id: int) -> Optional[CoffeeSession]:
        """
        Get the active coffee session where user is a participant.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Active CoffeeSession if found, None otherwise
        """
        try:
            user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
            if not user:
                return None
                
            session = await CoffeeSession.find_one(
                CoffeeSession.participants == user,
                CoffeeSession.is_active == True
            )
            return session
        except Exception as e:
            print(f"Error getting user active session: {e}")
            return None

    # async def get_active_coffee_cards(self) -> List[CoffeeCard]:
    #     """Get all active coffee cards."""
    #     return await CoffeeCard.find(CoffeeCard.is_active == True).to_list()

    
    # Based on the current Session

    async def start_coffee_session(
        self,
        initiator_id: int,
        coffee_cards: List[CoffeeCard],
        group_state: GroupState
    ) -> CoffeeSession:
        """Start a new coffee ordering session.

        Args:
            initiator_id: Telegram user id of the initiator
            coffee_cards: list of CoffeeCard documents (preferably already fetched)
            group_state: pre-initialized GroupState
        """

        # Look up the initiator: registered Telegram user
        initiator = await TelegramUser.find_one(TelegramUser.user_id == initiator_id)
        if not initiator:
            raise ValueError(f"Initiator {initiator_id} not found")

        # Accept a list of CoffeeCard documents (recommended) or an empty list
        cards = coffee_cards or []
        
        if not cards:
            raise ValueError("No coffee cards available to start a session")

        # Derive display name for notifications when display_name missing
        initiator_display_name = initiator.display_name or initiator.first_name or str(initiator.user_id)

        session = CoffeeSession(
            initiator=initiator,
            coffee_cards=cards,  # type: ignore
            group_state=group_state
        )

        # TODO: notify telegram users about the new session, and that they can join.

        await session.insert()

        # Notify all group members that a new session has been started and
        # that someone is entering coffees; send silently where possible.
        try:
            initiator_user_id = initiator.user_id
            # session.id should exist after insert(); use string key
            session_key = str(session.id)
            self.session_notifications.setdefault(session_key, [])

            for name, member in group_state.members.items():
                if member.user_id is None:
                    continue
                # don't notify the initiator about their own session
                if initiator_user_id is not None and member.user_id == initiator_user_id:
                    continue
                # send silently and persist until session end (vanish=False)
                msg = await self.api.message_manager.send_text(
                    member.user_id,
                    f"{initiator_display_name} started a new coffee session and is entering coffees. You can join with /group.",
                    vanish=False,
                    conv=False,
                    silent=True
                )
                if session_key and msg is not None:
                    self.session_notifications[session_key].append(msg)
        except Exception as e:
            print(f"❌ Failed to notify members about new session: {e}")

        return session

    async def add_participant(
        self,
        user: TelegramUser,
    ) -> None:
        """Add a participant to an existing coffee session."""
        if not self.session or not self.session.is_active:
            raise SessionNotActiveError()

        if user in self.session.participants:
            log_coffee_session_participant_added(str(self.session.id), user.user_id, False)
            return

        self.session.participants.append(user)
        await self.session.save()
        log_coffee_session_participant_added(str(self.session.id), user.user_id, True)

    
    async def remove_participant( self, user_id: int ) -> bool:
        user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
        if not user:
            raise ValueError(f"User {user_id} not found in database")
        
        if not self.session or not self.session.is_active:
            raise SessionNotActiveError()
        
        if user not in self.session.participants:
            log_coffee_session_participant_removed(str(self.session.id), user.user_id, False)
            return False
        else:
            self.session.participants.remove(user)
            log_coffee_session_participant_removed(str(self.session.id), user.user_id, True)
            return True
        
            
    async def start_or_join_session(self, user_id: int) -> Tuple[CoffeeSession, bool]:
        """
        Start a new session or join existing one.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            tuple: (CoffeeSession, is_new_session)
            
        Raises:
            ValueError: If user not found or no coffee cards available
        """
        # Get the user from database
        user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
        if not user:
            raise ValueError(f"User {user_id} not found in database")
        
        
        if self.session:
            # Add user to existing session if not already participant
            await self.add_participant(user)
            
            return self.session, False
        
        else:
            # Create new session
            active_cards = await self.api.coffee_card_manager.get_active_coffee_cards()
            if not active_cards:
                raise ValueError("No active coffee cards available for session")
            
            group_state = await initialize_group_state_from_db()
            
            # Start new session with available card documents
            new_session = await self.start_coffee_session(
                user_id,
                active_cards,
                group_state=group_state
            )
            
            await new_session.save()
            
            # Update current session reference
            self.session = new_session
            
            return new_session, True
    
    async def update_session_member_coffee(self, member_name: str, change: str) -> None:
        """
        Update coffee count for a session member.
        
        Args:
            member_name: Name of the member
            change: 'add' or 'remove'
        """
        if not self.session or not self.session.is_active:
            raise SessionNotActiveError()
            
        if change == 'add':
            changed = self.session.group_state.add_coffee(member_name)
        elif change == 'remove':
            changed = self.session.group_state.remove_coffee(member_name)
        else:
            changed = False

        # If nothing changed (e.g., removing when count is 0), do nothing
        if not changed:
            return

        await self.session.save()

        # Sync all keyboards for this session
        await self.api.group_keyboard_manager.sync_all_keyboards_for_session(str(self.session.id))

    # async def update_session_coffee_counts(self, group_members: Dict[str, int]) -> None:
    #     """Update coffee counts in the session's group state."""
        
    #     if not self.session or not self.session.is_active:
    #         raise SessionNotActiveError()
        
    #     try:
    #         await self.session.validate_coffee_counts(group_members)
    #     except InvalidCoffeeCountError:
    #         # Re-raise with more context if needed
    #         raise
    #     except InsufficientCoffeeError as e:
    #         # The exception already has all the details we need
    #         await self._handle_insufficient_coffee_capacity(e.requested, e.available)
    #         raise e
        
    #     # Update the group state members directly, preserving user_ids
    #     for name, coffee_count in group_members.items():
    #         if name in self.session.group_state.members:
    #             self.session.group_state.members[name].coffee = coffee_count
    #         else:
    #             # Create new member data without user_id for now
    #             from .telethon_models import GroupMemberData
    #             self.session.group_state.members[name] = GroupMemberData(coffee=coffee_count, user_id=None)
                
    #     await self.session.save()

    async def add_member_to_group_state(self, member_name: str, user_id: Optional[int] = None) -> None:
        """Add a member to the session's group state."""
        if not self.session:
            raise SessionNotActiveError()
            
        if member_name not in self.session.group_state.members:
            from .telethon_models import GroupMember
            self.session.group_state.members[member_name] = GroupMember(name=member_name, user_id=user_id, coffee_count=0)
            await self.session.save()

    async def get_member_user_id(self, member_name: str) -> Optional[int]:
        """Get the user_id for a member by name."""
        if not self.session or member_name not in self.session.group_state.members:
            return None
        return self.session.group_state.members[member_name].user_id

    async def _handle_insufficient_coffee_capacity(self, requested: int, available: int) -> None:
        """Handle insufficient coffee capacity by notifying participants."""
        if not self.session:
            return
        # This is a helper method that can be expanded later
        pass
    
    async def complete_session(self, submitted_by_user_id: int) -> bool:
        """
        Complete a session when a user submits their order.
        
        Args:
            submitted_by_user_id: User ID who submitted the order
        """
        if not self.session or not self.session.is_active:
            return False
        
        # TODO: as a last check, check if there are enough coffees available in the CoffeeCardManager

        # Mark session as completed
        self.session.is_active = False
        self.session.completed_date = datetime.now()
        submitted_by = await TelegramUser.find_one(TelegramUser.user_id == submitted_by_user_id)
        self.session.submitted_by = submitted_by
        await self.session.save()
        
        # Get total coffees
        total_coffees = await self.session.get_total_coffees()
        
        # Get participant user IDs from session
        await self.session.fetch_link("participants")
        participant_user_ids = {p.user_id for p in self.session.participants if hasattr(p, 'user_id')}
        
        # Notify Telegram Users about their orders (but not session participants - they get the full summary)
        for name, group_member_data in self.session.group_state.members.items():
            if group_member_data.coffee_count > 0 and group_member_data.user_id is not None:
                # Skip users who were active participants in the session
                if group_member_data.user_id in participant_user_ids:
                    continue
                    
                await self.session.fetch_link("initiator")
                initiator_display_name = self.session.initiator.display_name # type: ignore
                
                # Use singular/plural correctly and make key info bold
                coffee_word = "coffee" if group_member_data.coffee_count == 1 else "coffees"
                await self.api.message_manager.send_text(
                    group_member_data.user_id,
                    f"**{initiator_display_name}** has ordered **{group_member_data.coffee_count}** {coffee_word} for you.\n",
                    True, True
                )
        
        # Close all keyboards immediately for this session so UI is consistent
        session_id = str(self.session.id)
        await self.api.group_keyboard_manager.cleanup_session_keyboards(session_id)

        # Send notifications to participants captured earlier
        for participant_user_id in participant_user_ids:
            if participant_user_id == submitted_by_user_id:
                # Send completion message to submitter (persistent)
                await self.api.message_manager.send_text(
                    participant_user_id,
                    f"✅ **Session Completed!**\n"
                    f"Total: {total_coffees} coffees\n",
                    vanish=True,
                    conv=True,
                    silent=False
                )
            else:
                # Send notification to other participants (silent)
                await self.api.message_manager.send_text(
                    participant_user_id,
                    f"🔒 **Session Completed by Another User**\n",
                    vanish=True,
                    conv=False,
                    silent=True
                )

        # Delete persisted initial session notifications (they were sent with vanish=False)
        try:
            notif_key = session_id
            if notif_key in self.session_notifications:
                for msg in self.session_notifications[notif_key]:
                    try:
                        await msg.delete()
                    except Exception:
                        # best-effort: ignore deletion failures
                        pass
                del self.session_notifications[notif_key]
        except Exception:
            pass

        # Build summary using central helper and send to all TelegramUsers
        try:
            summary_text = await self.get_session_summary()
            # Send summary to all registered TelegramUsers
            full_users = await TelegramUser.find().to_list()
            for user in full_users:
                user_id_to_notify = user.user_id
                try:
                    # send summary persistently so it doesn't vanish and silently
                    await self.api.message_manager.send_text(
                        user_id_to_notify,
                        summary_text,
                        vanish=False,
                        conv=False,
                        silent=True
                    )
                except Exception as e:
                    print(f"❌ Failed to send session summary to TelegramUser {user_id_to_notify}: {e}")
        except Exception as e:
            print(f"❌ Failed to build/send session summary: {e}")
        
        await self.create_orders()
        
        # Clean up keyboards
        await self.api.group_keyboard_manager.cleanup_session_keyboards(session_id)
        
        # Clear current session reference
        if self.session and str(self.session.id) == session_id:
            self.session = None
            
        return True
    
    async def cancel_session(self) -> None:
        """
        Cancel an active session.
        """
        if not self.session or not self.session.is_active:
            return
        
        # Mark session as cancelled
        self.session.is_active = False
        await self.session.save()
        
        # Notify all participants with active keyboards
        session_id = str(self.session.id)
        if session_id in self.api.group_keyboard_manager.active_keyboards:
            for participant_user_id in self.api.group_keyboard_manager.active_keyboards[session_id].keys():
                await self.api.message_manager.send_text(
                    participant_user_id,
                    f"❌ **Session Cancelled**\n"
                    f"The coffee session has been cancelled.\n",
                    vanish=True,
                    conv=False,
                    silent=True
                )

        # Delete persisted initial session notifications on cancel as well
        try:
            notif_key = session_id
            if notif_key in self.session_notifications:
                for msg in self.session_notifications[notif_key]:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                del self.session_notifications[notif_key]
        except Exception:
            pass
        
        # Clean up keyboards
        await self.api.group_keyboard_manager.cleanup_session_keyboards(session_id)
        
        # Clear current session reference
        if self.session and str(self.session.id) == session_id:
            self.session = None


    async def create_orders(self):
        """
        Complete a coffee session and process orders.
        
        Returns:
            The completed CoffeeSession if successful, None if no active session
        """
        if not self.session:
            return []

        # Delegate allocation to a CoffeeCardManager instance

        # Build allocation plan (may raise on insufficient capacity)
        allocations = self.api.coffee_card_manager.allocate_session_orders(self.session)

        # determine initiator id
        initiator_user = self.session.submitted_by or self.session.initiator
        if not initiator_user or not getattr(initiator_user, 'user_id', None):
            return []
        initiator_id = int(getattr(initiator_user, 'user_id'))

        # Materialize allocations -> orders (pass session so relationships are updated)
        orders_created = await self.api.coffee_card_manager.create_orders_from_allocations(
            allocations, 
            initiator_id,
            self.session
        )

        # Session orders are already updated in create_orders_from_allocations
        await self.session.save()

        return orders_created

    async def get_session_summary(self) -> str:
        """
        Get a readable summary of the session.
        
        Returns:
            Formatted session summary string
        """
        if not self.session:
            return "No active session"
            
        total_coffees = await self.session.get_total_coffees()
        participant_count = len(self.session.participants)
        
        summary = (
            f"📊 **Coffee Order:**\n"
            f"• Participants: {participant_count}\n"
            f"• Total Coffees: {total_coffees}\n\n"
        )
        
        if self.session.group_state.members:
            summary += "**Individual Orders:**\n"
            for name, member_data in self.session.group_state.members.items():
                if member_data.coffee_count > 0:
                    summary += f"• {name}: {member_data.coffee_count}\n"
        
        return summary