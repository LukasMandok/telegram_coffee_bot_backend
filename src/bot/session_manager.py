"""
Session manager for handling coffee session lifecycle and coordination.

This module manages the overall coffee session workflow, coordinating between
the GroupKeyboardManager and database operations.
"""

from typing import Dict, List, Optional, TYPE_CHECKING, Tuple
from datetime import datetime
from ..models.coffee_models import CoffeeSession, CoffeeCard, CoffeeOrder
from ..models.beanie_models import TelegramUser, FullUser
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

    # DB access

    async def get_active_session(self) -> Optional[CoffeeSession]:
        """Get the currently active coffee session."""
        return await CoffeeSession.find_one(CoffeeSession.is_active == True)

    async def get_active_session_for_user(self, user_id: int) -> Optional[CoffeeSession]:
        """Get the active session initiated by a user."""
        # Try FullUser first, then TelegramUser
        user = await FullUser.find_one(FullUser.user_id == user_id)
        if not user:
            user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
        if not user:
            return None
        
        return await CoffeeSession.find_one(
            CoffeeSession.initiator == user,
            CoffeeSession.is_active == True
        )

    async def get_active_coffee_cards(self) -> List[CoffeeCard]:
        """Get all active coffee cards."""
        return await CoffeeCard.find(CoffeeCard.is_active == True).to_list()

    
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

        # Look up the initiator: try FullUser first (registered), then TelegramUser
        initiator = await FullUser.find_one(FullUser.user_id == initiator_id)
        if not initiator:
            initiator = await TelegramUser.find_one(TelegramUser.user_id == initiator_id)

        # Accept a list of CoffeeCard documents (recommended) or an empty list
        cards = coffee_cards or []
        
        if not cards:
            raise ValueError("No coffee cards available to start a session")

        # Derive display name for notifications when display_name missing
        initiator_display_name = getattr(initiator, 'display_name', None) or getattr(initiator, 'first_name', None) or str(getattr(initiator, 'user_id', ''))
        
        session = CoffeeSession(
            initiator=initiator,
            coffee_cards=cards,  # type: ignore
            group_state=group_state
        )
        
        # TODO: notify telegram users about the new session, and that they can join.
        
        await session.insert()
        
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
        user = await FullUser.find_one(FullUser.user_id == user_id)
        if not user:
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
        # Get the user from database - try FullUser first, then TelegramUser
        user = await FullUser.find_one(FullUser.user_id == user_id)
        if not user:
            user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
        if not user:
            raise ValueError(f"User {user_id} not found in database")
        
        
        if self.session:
            # Add user to existing session if not already participant
            await self.add_participant(user)
            
            return self.session, False
        
        else:
            # Create new session
            active_cards = await self.get_active_coffee_cards()
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
        
        # Mark session as completed
        self.session.is_active = False
        self.session.completed_date = datetime.now()
        # Try FullUser first, then TelegramUser
        submitted_by = await FullUser.find_one(FullUser.user_id == submitted_by_user_id)
        if not submitted_by:
            submitted_by = await TelegramUser.find_one(TelegramUser.user_id == submitted_by_user_id)
        self.session.submitted_by = submitted_by
        await self.session.save()
        
        # Get total coffees
        total_coffees = await self.session.get_total_coffees()
        
        # Notify all Telegram Users about their orders:
        for name, group_member_data in self.session.group_state.members.items():
            # todo: also check, that they were not part of the session (as a participant)
            if group_member_data.coffee_count > 0 and group_member_data.user_id is not None:
                await self.session.fetch_link("initiator")
                initiator_display_name = self.session.initiator.display_name # type: ignore

                await self.api.message_manager.send_text(
                    group_member_data.user_id,
                    f"{initiator_display_name} has ordered {group_member_data.coffee_count} coffees for you.\n",
                    True, True
                )
        
        # Notify all participants with active keyboards
        session_id = str(self.session.id)
        if session_id in self.api.group_keyboard_manager.active_keyboards:
            for participant_user_id in self.api.group_keyboard_manager.active_keyboards[session_id].keys():
                if participant_user_id == submitted_by_user_id:
                    # Send completion message to submitter
                    await self.api.message_manager.send_text(
                        participant_user_id,
                        f"✅ **Session Completed!**\n"
                        f"Your order has been submitted and the session is now closed.\n"
                        f"Total: {total_coffees} coffees\n"
                        f"Session ID: `{self.session.id}`",
                        True, True
                    )
                else:
                    # Send notification to other participants
                    await self.api.message_manager.send_text(
                        participant_user_id,
                        f"🔒 **Session Completed by Another User**\n"
                        f"The coffee session has been finalized.\n"
                        f"Total: {total_coffees} coffees\n"
                        f"Session ID: `{self.session.id}`",
                        True, True
                    )

        # Build summary using central helper and send to all FullUsers
        try:
            summary_text = await self.get_session_summary()
            # Send summary to all registered FullUsers
            full_users = await FullUser.find().to_list()
            for user in full_users:
                user_id_to_notify = user.user_id
                try:
                    await self.api.message_manager.send_text(
                        user_id_to_notify,
                        summary_text,
                        True, True
                    )
                except Exception as e:
                    print(f"❌ Failed to send session summary to FullUser {user_id_to_notify}: {e}")
        except Exception as e:
            print(f"❌ Failed to build/send session summary: {e}")
        
        await self.create_orders()
        await self.update_coffee_card()
        
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
                    f"The coffee session has been cancelled.\n"
                    f"Session ID: `{self.session.id}`",
                    True, True
                )
        
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
            return None

        # Create CoffeeOrder objects
        for name, member_data in self.session.group_state.members.items():
            if member_data.coffee_count > 0:
                pass
                # order = CoffeeOrder(
                #     user_id=member_data.user_id,
                #     coffee_count=member_data.coffee_count,
                #     session_id=self.session.id
                # )
                # await order.save()

        # TODO: Process actual coffee orders here
        # This would involve:
        # 1. Creating CoffeeOrder objects
        # 2. Updating coffee card counts
        # 3. Processing payments/debts
        
        await self.session.save()

    # TODO: implement
    async def update_coffee_card(self) -> None:
        """
        Update coffee card counts based on the current session's group state.
        
        This method will:
        - Update the coffee counts in the CoffeeCard objects
        - Notify participants about their updated coffee counts
        """
        if not self.session:
            raise SessionNotActiveError()


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
            f"📊 **Session Summary:**\n"
            f"• Participants: {participant_count}\n"
            f"• Total Coffees: {total_coffees}\n\n"
        )
        
        if self.session.group_state.members:
            summary += "**Individual Orders:**\n"
            for name, member_data in self.session.group_state.members.items():
                if member_data.coffee_count > 0:
                    summary += f"• {name}: {member_data.coffee_count}\n"
        
        return summary