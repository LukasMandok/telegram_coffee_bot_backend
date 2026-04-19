"""Session manager handling the coffee session lifecycle.

This implementation keeps *active* sessions fully in memory.

Only when a session is successfully submitted (committed) do we write a
`CoffeeSession` document to the database (and link created orders to it).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple

from ..common.log import (
    INFO,
    Logger,
    log_coffee_session_cancelled,
    log_coffee_session_participant_added,
    log_coffee_session_participant_removed,
    log_coffee_session_started,
    log_unexpected_error,
    logger as root_logger,
)
from ..database.snapshot_manager import pending_snapshot
from ..exceptions.coffee_exceptions import (
    CoffeeSessionError,
    InsufficientCoffeeError,
    NoActiveCoffeeCardsError,
    SessionNotActiveError,
    UserNotFoundError,
)
from ..models.beanie_models import TelegramUser
from ..models.coffee_models import CoffeeCard, CoffeeOrder, CoffeeSession
from .group_state_helpers import initialize_group_state_from_db
from .telethon_models import GroupState

if TYPE_CHECKING:
    from ..api.telethon_api import TelethonAPI


@dataclass
class InMemoryCoffeeSession:
    """Runtime-only session state.

    The shape intentionally mirrors the `CoffeeSession` document where practical
    so existing UI code (GroupKeyboardManager, MessageFlow) can keep using
    `session.id`, `session.is_active`, `session.group_state`, etc.
    """

    id: str
    initiator: TelegramUser
    coffee_cards: List[CoffeeCard]
    group_state: GroupState
    participants: List[TelegramUser] = field(default_factory=list)
    submitted_by: Optional[TelegramUser] = None
    session_date: datetime = field(default_factory=datetime.now)
    completed_date: Optional[datetime] = None
    is_active: bool = True

    # Orders are only created when committed. We keep this for API parity.
    orders: List[CoffeeOrder] = field(default_factory=list)

    # Reference to the card manager so availability stays accurate after new cards.
    # Typed as Any to avoid import cycles.
    coffee_card_manager: Any = field(default=None, repr=False, compare=False)

    async def save(self) -> None:
        return

    async def fetch_link(self, *_args: Any, **_kwargs: Any) -> None:
        return

    async def get_available_coffees(self) -> int:
        try:
            if self.coffee_card_manager is not None:
                cards = self.coffee_card_manager.cards
                return sum(max(0, int(card.remaining_coffees)) for card in cards)
        except Exception:
            pass

        return sum(max(0, int(card.remaining_coffees)) for card in self.coffee_cards)

    async def get_total_coffees(self) -> int:
        return int(self.group_state.get_total_coffees())


class SessionManager:
    """Manages the in-memory coffee session lifecycle."""

    def __init__(self, api: "TelethonAPI"):
        self.api = api
        self.session: Optional[InMemoryCoffeeSession] = None

        # Keep track of notification messages sent per session so we can delete them
        # when the session finishes or is cancelled.
        self.session_notifications: Dict[str, List[Any]] = {}

        self.logger = Logger("SessionManager")

        # In-memory delayed-cancel timers for sessions that have no active users.
        self._delayed_cancel_tasks: Dict[str, asyncio.Task] = {}

        # Simple monotonic session IDs for this runtime.
        self._next_session_number = 1

    async def cancel_leftover_active_sessions_on_startup(self) -> None:
        """Best-effort cleanup of legacy persisted "active" sessions.

        With in-memory sessions, nothing should be active in the DB. This is only
        here to clean up leftovers from older versions.
        """

        try:
            active_sessions = await CoffeeSession.find(CoffeeSession.is_active == True).to_list()
        except Exception as exc:
            self.logger.error("Failed querying leftover active sessions on startup", exc=exc)
            return

        for persisted in active_sessions:
            try:
                # If there are no orders, delete it. Otherwise mark inactive for history.
                if len(persisted.orders) == 0:
                    await persisted.delete()
                else:
                    persisted.is_active = False
                    await persisted.save()
            except Exception as exc:
                self.logger.error("Failed cleaning leftover active session on startup", exc=exc)

    def get_session_by_id(self, session_id: str) -> Optional[InMemoryCoffeeSession]:
        current = self.session
        if current is None:
            return None
        if str(current.id) != str(session_id):
            return None
        return current

    async def get_active_session(self) -> Optional[InMemoryCoffeeSession]:
        return self.session if self.session is not None and bool(self.session.is_active) else None

    async def get_active_session_for_user(self, user_id: int) -> Optional[InMemoryCoffeeSession]:
        current = await self.get_active_session()
        if current is None:
            return None
        if int(current.initiator.user_id) != int(user_id):
            return None
        return current

    async def get_user_active_session(self, user_id: int) -> Optional[InMemoryCoffeeSession]:
        current = await self.get_active_session()
        if current is None:
            return None
        if any(int(p.user_id) == int(user_id) for p in current.participants if p.user_id is not None):
            return current
        return None

    # Session lifecycle

    async def start_coffee_session(
        self,
        initiator_id: int,
        coffee_cards: List[CoffeeCard],
        group_state: GroupState,
    ) -> InMemoryCoffeeSession:
        """Start a new in-memory coffee ordering session."""

        initiator = await TelegramUser.find_one(TelegramUser.user_id == initiator_id)
        if not initiator:
            raise UserNotFoundError(initiator_id, message=f"Initiator {initiator_id} not found")

        cards = coffee_cards or []
        if not cards:
            raise NoActiveCoffeeCardsError()

        session_id = f"session_{self._next_session_number}"
        self._next_session_number += 1

        session = InMemoryCoffeeSession(
            id=session_id,
            initiator=initiator,
            coffee_cards=cards,
            group_state=group_state,
            participants=[initiator],
            coffee_card_manager=self.api.coffee_card_manager,
        )

        initiator_display_name = initiator.display_name or initiator.first_name or str(initiator.user_id)

        # Notify all group members that a new session has been started.
        initiator_user_id: Optional[int] = initiator.user_id
        try:
            self.session_notifications.setdefault(session_id, [])
            for _name, member in group_state.members.items():
                if member.user_id is None:
                    continue
                if initiator_user_id is not None and int(member.user_id) == int(initiator_user_id):
                    continue

                msg = await self.api.message_manager.send_user_notification(
                    int(member.user_id),
                    f"{initiator_display_name} started a new coffee session and is entering coffees. You can join with /order.",
                    force_silent=True,
                )
                if msg is not None:
                    self.session_notifications[session_id].append(msg)
        except Exception as exc:
            log_unexpected_error(
                operation="notify_members_new_session",
                error=str(exc),
                context={
                    "initiator_id": initiator_user_id,
                    "session_id": session_id,
                    "member_count": len(group_state.members),
                },
            )

        return session

    async def add_participant(
        self,
        user: TelegramUser,
        session: Optional[InMemoryCoffeeSession] = None,
    ) -> None:
        target_session = session or self.session
        if not target_session or not bool(target_session.is_active):
            raise SessionNotActiveError()

        if any(int(p.user_id) == int(user.user_id) for p in target_session.participants if p.user_id is not None):
            log_coffee_session_participant_added(str(target_session.id), int(user.user_id), False)
            return

        target_session.participants.append(user)
        log_coffee_session_participant_added(str(target_session.id), int(user.user_id), True)
        self._cancel_delayed_cancel_task(str(target_session.id))

    
    async def remove_participant(
        self,
        user_id: int,
        session: Optional[InMemoryCoffeeSession] = None,
    ) -> bool:
        user = await TelegramUser.find_one(TelegramUser.user_id == int(user_id))
        if not user:
            raise UserNotFoundError(user_id, message="User not found in database.")

        target_session = session or self.session
        if not target_session or not bool(target_session.is_active):
            raise SessionNotActiveError()

        before = len(target_session.participants)
        target_session.participants = [
            p for p in target_session.participants if p.user_id is not None and int(p.user_id) != int(user_id)
        ]
        removed = len(target_session.participants) != before

        if removed:
            log_coffee_session_participant_removed(str(target_session.id), int(user.user_id), True)
            if len(target_session.participants) == 0:
                try:
                    await self.cancel_or_delay_cancel_if_inactive(target_session)
                except Exception:
                    pass
        else:
            log_coffee_session_participant_removed(str(target_session.id), int(user.user_id), False)

        return bool(removed)
        
            
    async def start_or_join_session(self, user_id: int) -> Tuple[InMemoryCoffeeSession, bool]:
        """
        Start a new session or join existing one.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            tuple: (CoffeeSession, is_new_session)
            
        Raises:
            ValueError: If user not found or no coffee cards available
        """
        user = await TelegramUser.find_one(TelegramUser.user_id == int(user_id))
        if not user:
            raise UserNotFoundError(user_id, message=f"User {user_id} not found in database")

        active_session = await self.get_active_session()
        self.session = active_session

        if active_session is not None and bool(active_session.is_active):
            try:
                if len(active_session.participants) == 0 and active_session.group_state.get_total_coffees() > 0:
                    root_logger.log(
                        INFO,
                        "[SESSION] Re-opened suspended session %s: user_id=%s",
                        str(active_session.id),
                        user_id,
                    )
            except Exception:
                pass

            await self.add_participant(user, session=active_session)
            self.session = active_session
            return active_session, False

        active_cards = await self.api.coffee_card_manager.get_active_coffee_cards()
        if not active_cards:
            raise NoActiveCoffeeCardsError()

        group_state = await initialize_group_state_from_db()
        new_session = await self.start_coffee_session(
            int(user_id),
            active_cards,
            group_state=group_state,
        )

        try:
            card_names = [card.name for card in active_cards]
            log_coffee_session_started(str(new_session.id), int(user_id), card_names, level=INFO)
        except Exception:
            pass

        self.session = new_session
        return new_session, True

    async def mark_session_active(self, session: Optional[InMemoryCoffeeSession] = None) -> None:
        """Marks a session as active by cancelling any pending delayed-cancel timer."""
        target_session = session or self.session
        if not target_session or not bool(target_session.is_active):
            return
        self._cancel_delayed_cancel_task(str(target_session.id))

    def _cancel_delayed_cancel_task(self, session_id: str) -> None:
        task = self._delayed_cancel_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    def _schedule_delayed_cancel_if_still_inactive(self, session_id: str) -> None:
        """Schedules an in-memory cancel after 1 minute if the session stays inactive."""
        if session_id in self._delayed_cancel_tasks and not self._delayed_cancel_tasks[session_id].done():
            return

        self.logger.debug(
            "Session suspended: scheduling auto-cancel in 60s",
            extra_tag="SESSION",
        )

        async def _cancel_if_still_inactive() -> None:
            try:
                await asyncio.sleep(60)

                session = self.get_session_by_id(session_id)
                if session is None:
                    return

                if len(session.participants) > 0:
                    return

                await self.cancel_session(
                    session=session,
                    notify_participants=False,
                    reason="Auto-cancelled after 60s grace period",
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.logger.error(f"Failed delayed session cancel: session_id={session_id}", exc=e)
            finally:
                self._delayed_cancel_tasks.pop(session_id, None)

        self._delayed_cancel_tasks[session_id] = asyncio.create_task(_cancel_if_still_inactive())

    async def cancel_or_delay_cancel_if_inactive(self, session: InMemoryCoffeeSession) -> str:
        """If no one is interacting, cancel now (no changes) or keep suspended briefly then cancel."""
        session_id = str(session.id)

        if not bool(session.is_active):
            self._cancel_delayed_cancel_task(session_id)
            return "cancelled"

        # Participants are authoritative for deciding whether the session is still alive.
        if len(session.participants) > 0:
            self._cancel_delayed_cancel_task(session_id)
            return "still_active"

        modified = session.group_state.get_total_coffees() > 0
        if not modified:
            self.logger.debug(
                "Session cancelled immediately (inactive + empty)",
                extra_tag="SESSION",
            )
            await self.cancel_session(session=session, notify_participants=False)
            return "cancelled"

        # If a suspended-session timer is already running, do nothing (avoid duplicate logs).
        existing = self._delayed_cancel_tasks.get(session_id)
        if existing is not None and not existing.done():
            return "suspended"

        self.logger.debug(
            f"Session suspended due to inactivity: total_coffees={session.group_state.get_total_coffees()}",
            extra_tag="SESSION",
        )
        self._schedule_delayed_cancel_if_still_inactive(session_id)
        return "suspended"
    
    async def update_session_member_coffee(
        self,
        member_name: str,
        change: str,
        session: Optional[InMemoryCoffeeSession] = None,
    ) -> None:
        """
        Update coffee count for a session member.
        
        Args:
            member_name: Name of the member
            change: 'add' or 'remove'
        """
        target_session = session or self.session
        if not target_session or not bool(target_session.is_active):
            raise SessionNotActiveError()
            
        if change == 'add':
            changed = target_session.group_state.add_coffee(member_name)
        elif change == 'remove':
            changed = target_session.group_state.remove_coffee(member_name)
        else:
            changed = False

        # If nothing changed (e.g., removing when count is 0), do nothing
        if not changed:
            return
        await target_session.save()

        # Sync all keyboards for this session
        await self.api.group_keyboard_manager.sync_all_keyboards_for_session(target_session)

    async def add_member_to_group_state(self, member_name: str, stable_id: str, user_id: Optional[int] = None) -> None:
        if not self.session or not bool(self.session.is_active):
            raise SessionNotActiveError()

        if member_name not in self.session.group_state.members:
            self.session.group_state.add_member(member_name, stable_id, user_id=user_id, is_archived=False)
            await self.session.save()

    async def get_member_stable_id(self, member_name: str) -> Optional[str]:
        """Get the stable_id for a member by name."""
        if not self.session or member_name not in self.session.group_state.members:
            return None
        return self.session.group_state.members[member_name].stable_id
    
    async def _auto_complete_filled_cards(self, notifier_user_id: int) -> None:
        """
        Check for fully filled cards and auto-complete them.
        
        Args:
            notifier_user_id: User ID to notify about completed cards
        """
        try:
            # Check cards from manager's cache (already updated with latest remaining_coffees)
            # instead of querying database which might have stale data
            cards_to_complete = []
            for card in self.api.coffee_card_manager.cards:
                if card.remaining_coffees == 0:
                    cards_to_complete.append(card)
            
            # Complete each filled card
            for card in cards_to_complete:
                self.logger.info(f"Auto-completing fully filled card: {card.name}")
                # Use the shared completion function (no confirmation needed for auto-complete)
                await self.api.coffee_card_manager.close_card(
                    card,
                    requesting_user_id=notifier_user_id,
                    closed_by_session=True,
                )
                    
        except Exception as e:
            # Centralized stacktrace logging for unexpected errors in auto-complete
            log_unexpected_error(
                operation="auto_complete_filled_cards",
                error=str(e)
            )
            # Don't fail the session completion if auto-complete fails
    
    @pending_snapshot(
        lambda self, submitted_by_user_id, **_: f"session_completed:{str(self.session.id) if self.session else 'unknown'}",
        reason="Submit Session",
        collections=("coffee_sessions", "coffee_orders", "coffee_cards", "user_debts", "payments"),
    )
    async def complete_session(self, submitted_by_user_id: int) -> bool:
        """
        Complete a session when a user submits their order.
        
        Args:
            submitted_by_user_id: User ID who submitted the order
        """
        if not self.session or not bool(self.session.is_active):
            self.logger.warning(f"Attempted to complete an inactive session: user_id={submitted_by_user_id}")
            await self.api.message_manager.send_text(submitted_by_user_id, "❌ Failed to complete session.", True, True)
            raise SessionNotActiveError()

        session_id = str(self.session.id)

        # Make session undiscoverable during commit (prevents concurrent joins/submits).
        self.session.is_active = False

        # TODO: as a last check, check if there are enough coffees available in the CoffeeCardManager

        # Resolve the submitting user (for persisted history + notifications).
        submitted_by = await TelegramUser.find_one(TelegramUser.user_id == int(submitted_by_user_id))

        # Get total coffees
        total_coffees = await self.session.get_total_coffees()

        participant_user_ids = {
            int(p.user_id)
            for p in self.session.participants
            if p.user_id is not None
        }

        # Build allocation plan (no DB writes). This may raise on insufficient coffees.
        try:
            allocations = self.api.coffee_card_manager.allocate_session_orders(self.session)
        except InsufficientCoffeeError as e:
            self.session.is_active = True
            self.logger.warning(
                f"Insufficient coffee capacity: requested={e.requested}, available={e.available}, session_id={session_id}",
                extra_tag="ORDER"
            )

            # Cancel all active conversations for this session to prevent timeout errors
            if session_id in self.api.group_keyboard_manager.active_keyboards:
                for participant_user_id in list(self.api.group_keyboard_manager.active_keyboards[session_id].keys()):
                    self.api.conversation_manager.cancel_conversation(participant_user_id)

            await self.api.message_manager.send_text(
                submitted_by_user_id,
                f"⚠️ **Session Suspended!**\n\n"
                f"❌ Not enough coffees remaining on your cards.\n"
                f"• Requested: {e.requested}\n"
                f"• Available: {e.available}\n\n"
                f"💡 Someone needs to buy and open a new coffee card!\n"
                f"Use /new_card to add a new card.\n\n"
                f"Your session is still active and you can submit again once a new card is available.",
                vanish=False,
                conv=False
            )

            self.logger.debug(
                f"Session suspended on submit (insufficient coffees): requested={e.requested}, available={e.available}, total_coffees={total_coffees}",
                extra_tag="SESSION",
            )

            # Suspension is runtime-only. Remove stale UI/participants and auto-cancel after a short grace period
            # if nobody re-joins. This keeps DB clean (cancel will delete empty sessions).
            try:
                self.session.participants = []
            except Exception:
                pass

            try:
                await self.api.group_keyboard_manager.cleanup_session_keyboards(session_id)
            except Exception:
                pass

            try:
                self._schedule_delayed_cancel_if_still_inactive(session_id)
            except Exception:
                pass

            raise CoffeeSessionError()
        
        except Exception as exc:
            self.session.is_active = True
            # Only truly unexpected errors get a stacktrace
            log_unexpected_error(
                operation="create_orders",
                error=str(exc),
                context={"session_id": session_id}
            )

            # Cancel all active conversations for this session to prevent timeout errors
            if session_id in self.api.group_keyboard_manager.active_keyboards:
                for participant_user_id in list(self.api.group_keyboard_manager.active_keyboards[session_id].keys()):
                    self.api.conversation_manager.cancel_conversation(participant_user_id)

            await self.api.message_manager.send_text(
                submitted_by_user_id,
                f"⚠️ **Session Suspended!**\n\n"
                f"❌ Error: {str(exc)}\n\n"
                f"Your session is still active. Please try again.",
                vanish=False,
                conv=False
            )

            self.logger.debug(
                f"Session suspended on submit (unexpected error): total_coffees={total_coffees}",
                extra_tag="SESSION",
            )

            # Same suspension semantics as insufficient capacity.
            try:
                self.session.participants = []
            except Exception:
                pass

            try:
                await self.api.group_keyboard_manager.cleanup_session_keyboards(session_id)
            except Exception:
                pass

            try:
                self._schedule_delayed_cancel_if_still_inactive(session_id)
            except Exception:
                pass

            raise CoffeeSessionError()

        # DB commit: create a persistent CoffeeSession doc and link orders to it.
        persisted_session: Optional[CoffeeSession] = None
        try:
            persisted_session = CoffeeSession(
                initiator=self.session.initiator,
                submitted_by=submitted_by,
                coffee_cards=list(self.api.coffee_card_manager.cards),  # type: ignore[list-item]
                participants=list(self.session.participants),  # type: ignore[list-item]
                session_date=self.session.session_date,
                completed_date=datetime.now(),
                is_active=False,
                group_state=self.session.group_state,
            )
            await persisted_session.insert()

            initiator_id = int(submitted_by_user_id)
            await self.api.coffee_card_manager.create_orders_from_allocations(
                allocations,
                initiator_id,
                persisted_session,
            )
        except InsufficientCoffeeError as e:
            self.session.is_active = True
            # Capacity can change between plan and commit (race with other orders).
            self.logger.warning(
                f"Insufficient coffee capacity during commit: requested={e.requested}, available={e.available}, session_id={session_id}",
                extra_tag="ORDER",
            )
            try:
                if persisted_session is not None:
                    await persisted_session.delete()
            except Exception:
                pass

            await self.api.message_manager.send_text(
                submitted_by_user_id,
                f"⚠️ **Session Suspended!**\n\n"
                f"❌ Not enough coffees remaining on your cards.\n"
                f"• Requested: {e.requested}\n"
                f"• Available: {e.available}\n\n"
                f"💡 Someone needs to buy and open a new coffee card!\n"
                f"Use /new_card to add a new card.",
                vanish=False,
                conv=False,
            )

            try:
                self.session.participants = []
            except Exception:
                pass
            try:
                await self.api.group_keyboard_manager.cleanup_session_keyboards(session_id)
            except Exception:
                pass
            try:
                self._schedule_delayed_cancel_if_still_inactive(session_id)
            except Exception:
                pass
            raise CoffeeSessionError()
        except Exception as exc:
            self.session.is_active = True
            log_unexpected_error(
                operation="commit_session",
                error=str(exc),
                context={"session_id": session_id},
            )
            try:
                if persisted_session is not None:
                    await persisted_session.delete()
            except Exception:
                pass
            await self.api.message_manager.send_text(
                submitted_by_user_id,
                "⚠️ **Session Suspended!**\n\n❌ Commit failed. Please try again.",
                vanish=False,
                conv=False,
            )
            try:
                self.session.participants = []
            except Exception:
                pass
            try:
                await self.api.group_keyboard_manager.cleanup_session_keyboards(session_id)
            except Exception:
                pass
            try:
                self._schedule_delayed_cancel_if_still_inactive(session_id)
            except Exception:
                pass
            raise CoffeeSessionError()

        # Notify Telegram Users about their orders (but not session participants).
        # Do this only after commit succeeds to avoid false notifications on suspended submits.
        try:
            initiator_display_name = self.session.initiator.display_name
            for _name, group_member_data in self.session.group_state.members.items():
                if group_member_data.coffee_count <= 0:
                    continue
                if group_member_data.user_id is None:
                    continue
                if int(group_member_data.user_id) in participant_user_ids:
                    continue

                coffee_word = "coffee" if group_member_data.coffee_count == 1 else "coffees"
                await self.api.message_manager.send_user_notification(
                    int(group_member_data.user_id),
                    f"**{initiator_display_name}** has ordered **{group_member_data.coffee_count}** {coffee_word} for you.\n",
                )
        except Exception as exc:
            log_unexpected_error(
                operation="notify_non_participants",
                error=str(exc),
                context={"session_id": session_id},
            )

        # Orders created successfully - session completion is final now.
        await self.api.group_keyboard_manager.cleanup_session_keyboards(session_id)

        # Send completion messages (submitter gets a direct message, others a notification).
        for participant_user_id in participant_user_ids:
            if participant_user_id == submitted_by_user_id:
                await self.api.message_manager.send_text(
                    participant_user_id,
                    "✅ **Session Completed!**\n",
                    vanish=True,
                    conv=True,
                    silent=False,
                )
            else:
                await self.api.message_manager.send_user_notification(
                    participant_user_id,
                    "🔒 **Session Completed by Another User**\n",
                    vanish=True,
                    conv=True,
                )

        # Remove conversation state for all participants so they are unblocked
        for participant_user_id in participant_user_ids:
            self.api.conversation_manager.cancel_conversation(participant_user_id)

        # Delete initial session notifications (best-effort)
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

        # Build summary using central helper and send to all TelegramUsers
        try:
            summary_text = await self.get_session_summary()
            await self.api.message_manager.send_notification_to_all_users(
                text=summary_text,
                silent=True,
                link_preview=False,
                exclude_user_ids=None,
                exclude_archived=True,
                exclude_disabled=True,
            )
        except Exception as exc:
            log_unexpected_error(
                operation="build_or_send_session_summary",
                error=str(exc),
                context={"session_id": session_id}
            )

        # Auto-complete cards after session completion + summary notifications.
        await self._auto_complete_filled_cards(submitted_by_user_id)

        if self.session and str(self.session.id) == session_id:
            self.session = None

        return True
    
    async def cancel_session(
        self,
        session: Optional[InMemoryCoffeeSession] = None,
        *,
        notify_participants: bool = True,
        reason: Optional[str] = None,
    ) -> None:
        """
        Cancel an active session.
        """
        target_session = session or self.session
        if target_session is None:
            return

        self._cancel_delayed_cancel_task(str(target_session.id))
        session_id = str(target_session.id)

        # Mark inactive so `/order` won't re-join while we clean up.
        target_session.is_active = False

        if reason is not None:
            log_coffee_session_cancelled(session_id=session_id, reason=reason, level=INFO)

        # Notify all participants with active keyboards
        if notify_participants and session_id in self.api.group_keyboard_manager.active_keyboards:
            for participant_user_id in self.api.group_keyboard_manager.active_keyboards[session_id].keys():
                await self.api.message_manager.send_user_notification(
                    participant_user_id,
                    "❌ **Session Cancelled**\nThe coffee session has been cancelled.\n",
                    delete_after=15,
                )

        # Delete initial session notifications on cancel as well
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

        # Cancel conversation state for any users that still have it.
        participant_user_ids_to_cancel: List[int] = []
        if session_id in self.api.group_keyboard_manager.active_keyboards:
            participant_user_ids_to_cancel = list(self.api.group_keyboard_manager.active_keyboards[session_id].keys())

        # Clean up keyboards
        await self.api.group_keyboard_manager.cleanup_session_keyboards(session_id)

        for participant_user_id in participant_user_ids_to_cancel:
            self.api.conversation_manager.cancel_conversation(participant_user_id)

        self.logger.debug(
            "Session cancelled (in-memory)",
            extra_tag="SESSION",
        )

        if self.session and str(self.session.id) == session_id:
            self.session = None


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