"""
Debt and Payment Management.

Handles debt creation when coffee cards are completed, and payment processing.
Debts are created once per card completion, not on individual orders.
"""

from typing import List, Dict, Optional, Any, Protocol, Callable, Tuple, Sequence, cast
from datetime import datetime

from beanie import Link as BeanieLink

from ..common.log import Logger
from ..models.coffee_models import CoffeeCard, Payment, UserDebt, PaymentReason
from ..models.beanie_models import TelegramUser, PassiveUser
from ..dependencies.dependencies import get_repo
from ..services.gsheet_sync import LocalPaidAmountChange, request_gsheet_sync_after_action
from .message_flow_helpers import format_money


class DebtManager:
    """
    Manages debt tracking and payment processing for coffee consumption.

    Key Concept:
    - Debts are created when a coffee card is marked as COMPLETED
    - Each debt represents total consumption for one user on one completed card
    - Ongoing/active cards do NOT have debts yet
    """

    def __init__(self, api):
        self.api = api
        self.logger = Logger("DebtManager")

    @staticmethod
    def calculate_debt_amount(total_coffees: int, cost_per_coffee: float) -> float:
        """
        Calculate the total debt amount.

        Args:
            total_coffees: Number of coffees consumed
            cost_per_coffee: Cost per coffee

        Returns:
            Total debt amount
        """
        return total_coffees * cost_per_coffee

    @staticmethod
    def _calculate_missing_coffee_corrections(
        *,
        card: CoffeeCard,
        correction_method: str,
        correction_threshold: int,
    ) -> Dict[str, float]:
        """Return mapping stable_id -> correction amount for this card."""
        remaining_coffees = max(0, card.remaining_coffees)
        if remaining_coffees <= 0:
            return {}

        remaining_cost = remaining_coffees * card.cost_per_coffee

        correction_method = (correction_method or "absolute").strip().lower()

        eligible_coffees: Dict[str, int] = {}
        for stable_id, stats in card.consumer_stats.items():
            if stats.total_coffees <= 0:
                continue
            if stats.total_coffees >= correction_threshold:
                eligible_coffees[stable_id] = stats.total_coffees

        if not eligible_coffees:
            return {}

        if correction_method == "absolute":
            per_user = remaining_cost / len(eligible_coffees)
            return {stable_id: per_user for stable_id in eligible_coffees.keys()}

        # proportional
        elif correction_method == "proportional":
            total_eligible_coffees = sum(eligible_coffees.values())
            if total_eligible_coffees <= 0:
                return {}

            return {
                stable_id: remaining_cost * (coffees / total_eligible_coffees)
                for stable_id, coffees in eligible_coffees.items()
            }
        else:
            return {}

    async def create_or_update_debts_for_card(self, card: CoffeeCard) -> List[UserDebt]:
        """
        Create or update debt records for all consumers of a coffee card.

        If a debt already exists for a debtor on this card, it will be updated
        with the current total_coffees from consumer_stats. This allows for
        corrections if coffees are missing or need adjustment.

        This method can be called on both active and inactive cards to create
        or update debts as needed.

        Args:
            card: Coffee card to create/update debts for

        Returns:
            List of created or updated UserDebt documents
        """
        creditor: TelegramUser = card.purchaser  # type: ignore
        creditor_stable_id = creditor.stable_id

        repo = get_repo()
        debt_settings = await repo.get_debt_settings()
        if not debt_settings:
            self.logger.warning("Debt settings missing; using defaults")
            correction_method = "absolute"
            correction_threshold = 5
        else:
            correction_method = debt_settings.correction_method
            correction_threshold = int(debt_settings.correction_threshold)
        corrections_by_user = self._calculate_missing_coffee_corrections(
            card=card,
            correction_method=correction_method,
            correction_threshold=correction_threshold,
        )

        # Create or update debts for each consumer (except purchaser)
        processed_debts = []

        for stable_id, stats in card.consumer_stats.items():
            # Skip purchaser - they don't owe themselves
            if stable_id == creditor_stable_id:
                continue

            # Skip if no coffees consumed
            if stats.total_coffees == 0:
                continue

            # Find the consumer: TelegramUser first, then PassiveUser
            consumer = await TelegramUser.find_one(TelegramUser.stable_id == stable_id)
            if not consumer:
                consumer = await PassiveUser.find_one(PassiveUser.stable_id == stable_id)
            if not consumer:
                self.logger.warning(f"Consumer with stable_id '{stable_id}' not found, skipping debt creation")
                continue

            # Check if debt already exists for this debtor and card
            # Query using MongoDB $oid comparison for Link fields
            existing_debt = await UserDebt.find_one(
                {"debtor.$id": consumer.id, "coffee_card.$id": card.id}
            )

            # Calculate amounts
            base_amount = self.calculate_debt_amount(stats.total_coffees, card.cost_per_coffee)
            debt_correction = corrections_by_user.get(stable_id, 0.0)
            total_amount = base_amount + debt_correction

            now = datetime.now()
            if existing_debt:
                # Update existing debt
                existing_debt.total_coffees = stats.total_coffees
                existing_debt.cost_per_coffee = card.cost_per_coffee
                existing_debt.total_amount = total_amount
                existing_debt.base_amount = base_amount
                existing_debt.debt_correction = debt_correction
                existing_debt.updated_at = now
                self._update_debt_settlement_state(existing_debt, now=now)
                # Note: Don't reset paid_amount - keep existing payments
                await existing_debt.save()
                processed_debts.append(existing_debt)

                await self._offset_mutual_debts_between_users(debtor_id=consumer.id, creditor_id=creditor.id)
                self.logger.info(
                    f"Updated debt: {stats.display_name} owes {creditor.display_name} €{total_amount:.2f} "
                    f"({stats.total_coffees} coffees, base=€{base_amount:.2f}, correction=€{debt_correction:.2f})"
                )
            else:
                # Create new debt record
                debt = UserDebt(
                    debtor=consumer,  # type: ignore
                    creditor=creditor,  # type: ignore
                    coffee_card=card,  # type: ignore
                    total_coffees=stats.total_coffees,
                    cost_per_coffee=card.cost_per_coffee,
                    base_amount=base_amount,
                    total_amount=total_amount,
                    debt_correction=debt_correction,
                    paid_amount=0.0,
                    is_settled=False,
                    updated_at=now
                )
                await debt.insert()
                processed_debts.append(debt)

                await self._offset_mutual_debts_between_users(debtor_id=consumer.id, creditor_id=creditor.id)
                self.logger.info(
                    f"Created debt: {stats.display_name} owes {creditor.display_name} €{total_amount:.2f} "
                    f"({stats.total_coffees} coffees, base=€{base_amount:.2f}, correction=€{debt_correction:.2f})"
                )

        return processed_debts

    class _DebtLike(Protocol):
        total_amount: float
        paid_amount: float
        created_at: datetime
        updated_at: datetime
        is_settled: bool
        settled_at: Optional[datetime]

    @staticmethod
    def _update_debt_settlement_state(
        debt: "DebtManager._DebtLike",
        *,
        now: datetime,
        epsilon: float = 1e-9,
    ) -> None:
        unpaid = debt.total_amount - debt.paid_amount
        if unpaid <= epsilon:
            debt.paid_amount = debt.total_amount
            debt.is_settled = True
            if debt.settled_at is None:
                debt.settled_at = now
        else:
            debt.is_settled = False
            debt.settled_at = None

    async def _create_payment(
        self,
        *,
        debt: UserDebt,
        amount: float,
        reason: PaymentReason,
        source_pair_debt: Optional[UserDebt] = None,
        description: Optional[str] = None,
    ) -> Payment:
        if amount <= 0:
            raise ValueError("Payment amount must be greater than zero")

        if isinstance(debt.debtor, BeanieLink):
            await debt.fetch_link("debtor")
        if isinstance(debt.creditor, BeanieLink):
            await debt.fetch_link("creditor")

        payment = Payment(
            payer=debt.debtor,  # type: ignore[arg-type]
            recipient=debt.creditor,  # type: ignore[arg-type]
            amount=amount,
            reason=reason,
            target_debt=debt,
            source_pair_debt=source_pair_debt,
            description=description,
        )
        await payment.insert()
        return payment

    @staticmethod
    def _format_debt_for_offset_log(debt: "DebtManager._DebtLike") -> str:
        debt_id = getattr(debt, "id", None)
        debt_id_str = str(debt_id) if debt_id is not None else f"mem:{id(debt)}"

        created_at = getattr(debt, "created_at", None)
        created_str = created_at.isoformat() if isinstance(created_at, datetime) else "?"

        return (
            f"debt={debt_id_str} created_at={created_str} "
            f"paid=€{debt.paid_amount:.2f}/€{debt.total_amount:.2f}"
        )

    @staticmethod
    def _apply_offset_amount(debt: "DebtManager._DebtLike", amount: float, *, now: datetime) -> float:
        """Apply an internal offset (non-cash) payment to a debt.

        This mirrors `_apply_payment_to_debt` behavior, but avoids any DB fetches/log spam.

        Returns remaining amount not applied.
        """
        epsilon = 1e-9

        unpaid = debt.total_amount - debt.paid_amount
        if unpaid <= epsilon:
            return amount

        to_apply = min(amount, unpaid)
        debt.paid_amount += to_apply

        DebtManager._update_debt_settlement_state(debt, now=now, epsilon=epsilon)

        debt.updated_at = now
        return amount - to_apply

    @classmethod
    def _offset_mutual_debts_in_memory(
        cls,
        *,
        debts_ab: Sequence["DebtManager._DebtLike"],
        debts_ba: Sequence["DebtManager._DebtLike"],
        now: datetime,
        log_line: Optional[Callable[[str], None]] = None,
    ) -> List[Tuple["DebtManager._DebtLike", "DebtManager._DebtLike", float]]:
        """Offset mutual debts between two users (A->B and B->A), oldest first.

        The algorithm walks both debt lists (sorted by `created_at`) and applies the same
        offset amount to *both* sides, reducing unnecessary cash transfers.

        Returns number of debts that were modified.
        """
        epsilon = 1e-9

        debts_ab_sorted = sorted(debts_ab, key=lambda d: d.created_at)
        debts_ba_sorted = sorted(debts_ba, key=lambda d: d.created_at)

        modified_ids = set()
        offset_events: List[Tuple["DebtManager._DebtLike", "DebtManager._DebtLike", float]] = []

        idx_ab = 0
        idx_ba = 0

        while idx_ab < len(debts_ab_sorted) and idx_ba < len(debts_ba_sorted):
            debt_ab = debts_ab_sorted[idx_ab]
            debt_ba = debts_ba_sorted[idx_ba]

            remaining_ab = debt_ab.total_amount - debt_ab.paid_amount
            remaining_ba = debt_ba.total_amount - debt_ba.paid_amount

            # make sure these are not already fully paid but were not marked as settled (should not happen)
            if remaining_ab <= epsilon:
                idx_ab += 1
                continue
            if remaining_ba <= epsilon:
                idx_ba += 1
                continue

            offset = min(remaining_ab, remaining_ba)

            paid_amount_before_ab = debt_ab.paid_amount
            paid_amount_before_ba = debt_ba.paid_amount

            cls._apply_offset_amount(debt_ab, offset, now=now)
            cls._apply_offset_amount(debt_ba, offset, now=now)
            offset_events.append((debt_ab, debt_ba, offset))

            if log_line is not None:
                log_line(
                    "Netting offset applied: "
                    f"offset=€{offset:.2f} | "
                    f"A->B {cls._format_debt_for_offset_log(debt_ab)} (was €{paid_amount_before_ab:.2f}) | "
                    f"B->A {cls._format_debt_for_offset_log(debt_ba)} (was €{paid_amount_before_ba:.2f})"
                )

            if debt_ab.paid_amount != paid_amount_before_ab:
                modified_ids.add(id(debt_ab))
            if debt_ba.paid_amount != paid_amount_before_ba:
                modified_ids.add(id(debt_ba))

            # Advance past fully settled debts
            if debt_ab.total_amount - debt_ab.paid_amount <= epsilon:
                idx_ab += 1
            if debt_ba.total_amount - debt_ba.paid_amount <= epsilon:
                idx_ba += 1

        if len(modified_ids) <= 0:
            return []

        return offset_events

    async def _offset_mutual_debts_between_users(self, *, debtor_id: Any, creditor_id: Any) -> None:
        """Offset unsettled debts between two users in both directions.

        Triggered after creating/updating a debt, so mutual debts are netted immediately.
        """
        if debtor_id == creditor_id:
            return

        # Unsettled debts: debtor -> creditor
        debts_ab: List[UserDebt] = await UserDebt.find(
            {
                "debtor.$id": debtor_id,
                "creditor.$id": creditor_id,
                "is_settled": False,
            }
        ).to_list()

        # Unsettled debts: creditor -> debtor (opposite direction)
        debts_ba: List[UserDebt] = await UserDebt.find(
            {
                "debtor.$id": creditor_id,
                "creditor.$id": debtor_id,
                "is_settled": False,
            }
        ).to_list()

        if not debts_ab or not debts_ba:
            return

        now = datetime.now()

        debtor_user: Optional[PassiveUser] = await TelegramUser.get(debtor_id) or await PassiveUser.get(debtor_id)
        creditor_user: Optional[TelegramUser] = await TelegramUser.get(creditor_id)
        debtor_name = getattr(debtor_user, "display_name", str(debtor_id))
        creditor_name = getattr(creditor_user, "display_name", str(creditor_id))

        # Snapshot before for notification/debugging
        before_state: Dict[str, Tuple[float, bool]] = {}
        for debt in debts_ab + debts_ba:
            debt_id = getattr(debt, "id", None)
            key = str(debt_id) if debt_id is not None else f"mem:{id(debt)}"
            before_state[key] = (float(debt.paid_amount), bool(debt.is_settled))

        def _log_line(message: str) -> None:
            self.logger.info(message, extra_tag="DEBT_OFFSET")

        outstanding_ab = sum(max(0.0, d.total_amount - d.paid_amount) for d in debts_ab)
        outstanding_ba = sum(max(0.0, d.total_amount - d.paid_amount) for d in debts_ba)
        _log_line(
            f"Netting start: {debtor_name} ↔ {creditor_name} "
            f"count_ab={len(debts_ab)} outstanding_ab=€{outstanding_ab:.2f} "
            f"count_ba={len(debts_ba)} outstanding_ba=€{outstanding_ba:.2f}"
        )

        offset_events = self._offset_mutual_debts_in_memory(
            debts_ab=debts_ab,
            debts_ba=debts_ba,
            now=now,
            log_line=_log_line,
        )
        if not offset_events:
            return

        # Persist changes
        for debt in debts_ab:
            await debt.save()
        for debt in debts_ba:
            await debt.save()

        paid_changes: List[LocalPaidAmountChange] = []
        for debt in debts_ab + debts_ba:
            debt_id = getattr(debt, "id", None)
            key = str(debt_id) if debt_id is not None else f"mem:{id(debt)}"
            before_paid, _before_settled = before_state.get(key, (0.0, False))
            after_paid = float(getattr(debt, "paid_amount", 0.0) or 0.0)
            if abs(after_paid - float(before_paid)) <= 1e-9:
                continue

            card_link = getattr(debt, "coffee_card", None)
            debtor_link = getattr(debt, "debtor", None)
            if not isinstance(card_link, BeanieLink) or not isinstance(debtor_link, BeanieLink):
                continue

            paid_changes.append(
                LocalPaidAmountChange(
                    card_id=str(card_link.ref.id),
                    debtor_id=str(debtor_link.ref.id),
                    value_before=float(before_paid),
                    value_after=after_paid,
                )
            )

        # Create auditable payment events for each offset step in both directions.
        for debt_ab, debt_ba, offset in offset_events:
            if offset <= 0:
                continue

            await self._create_payment(
                debt=debt_ab,  # type: ignore[arg-type]
                amount=offset,
                reason=PaymentReason.COMPENSATION_OFFSET,
                source_pair_debt=debt_ba,  # type: ignore[arg-type]
                description="Compensatory offset against reciprocal debt",
            )
            await self._create_payment(
                debt=debt_ba,  # type: ignore[arg-type]
                amount=offset,
                reason=PaymentReason.COMPENSATION_OFFSET,
                source_pair_debt=debt_ab,  # type: ignore[arg-type]
                description="Compensatory offset against reciprocal debt",
            )

        # Build user-facing summary (only changed debts), grouped by direction
        changes_by_direction: Dict[str, List[Tuple[datetime, str]]] = {}
        for debt in debts_ab + debts_ba:
            debt_id = getattr(debt, "id", None)
            key = str(debt_id) if debt_id is not None else f"mem:{id(debt)}"
            before_paid, before_settled = before_state.get(key, (0.0, False))
            after_paid = float(debt.paid_amount)
            after_settled = bool(debt.is_settled)

            if abs(after_paid - before_paid) <= 1e-9 and after_settled == before_settled:
                continue

            direction = f"{debtor_name} ↔ {creditor_name}"
            try:
                debtor_link = getattr(debt, "debtor", None)
                creditor_link = getattr(debt, "creditor", None)
                debtor_ref_id = getattr(getattr(debtor_link, "ref", None), "id", None)
                creditor_ref_id = getattr(getattr(creditor_link, "ref", None), "id", None)
                if debtor_ref_id == debtor_id and creditor_ref_id == creditor_id:
                    direction = f"{debtor_name} → {creditor_name}"
                elif debtor_ref_id == creditor_id and creditor_ref_id == debtor_id:
                    direction = f"{creditor_name} → {debtor_name}"
            except Exception:
                direction = f"{debtor_name} ↔ {creditor_name}"

            card_name = "(unknown card)"
            try:
                if isinstance(getattr(debt, "coffee_card", None), BeanieLink):
                    await debt.fetch_link("coffee_card")
                if getattr(debt, "coffee_card", None) is not None:
                    card_name = f"'{debt.coffee_card.name}'"  # type: ignore
            except Exception:
                card_name = "(card lookup failed)"

            became_settled = after_settled and not before_settled
            status_label = "Settled" if became_settled or after_settled else "Partial"
            status_icon = "✅" if status_label == "Settled" else "🟡"

            created_at = getattr(debt, "created_at", now)
            if not isinstance(created_at, datetime):
                created_at = now

            entry = (
                f"- {card_name}: {format_money(before_paid)} → {format_money(after_paid)} / {format_money(debt.total_amount)} ({status_icon} {status_label})"
            )
            changes_by_direction.setdefault(direction, []).append((created_at, entry))

        outstanding_ab_after = sum(max(0.0, d.total_amount - d.paid_amount) for d in debts_ab)
        outstanding_ba_after = sum(max(0.0, d.total_amount - d.paid_amount) for d in debts_ba)
        _log_line(
            f"Netting done: {debtor_name} ↔ {creditor_name} modified={len(offset_events)} "
            f"outstanding_ab=€{outstanding_ab:.2f}->€{outstanding_ab_after:.2f} "
            f"outstanding_ba=€{outstanding_ba:.2f}->€{outstanding_ba_after:.2f}"
        )

        # Notify both parties (Telegram users only) about the compensatory offset.
        if changes_by_direction:
            directions_sorted = sorted(changes_by_direction.keys())
            blocks: List[str] = []
            for direction in directions_sorted:
                entries = changes_by_direction[direction]
                entries_sorted = sorted(entries, key=lambda t: t[0])
                block = "\n".join([direction] + [f"  {line}" for _, line in entries_sorted])
                blocks.append(block)

            notification_text = (
                "🔄 Compensatory offset applied.\n\n"
                "**Updated payments**:\n" + "\n".join(blocks)
            )

            try:
                # TODO: Maybe you want to use conv=True, vanish=True here?
                if isinstance(debtor_user, TelegramUser):
                    await self.api.message_manager.send_user_notification(
                        debtor_user.user_id,
                        notification_text,
                    )
                if isinstance(creditor_user, TelegramUser):
                    await self.api.message_manager.send_user_notification(
                        creditor_user.user_id,
                        notification_text,
                    )
            except Exception as e:
                self.logger.warning(
                    f"Failed to notify users about debt offset: {debtor_name} ↔ {creditor_name}",
                    extra_tag="DEBT_OFFSET",
                    exc=e,
                )

        request_gsheet_sync_after_action(reason="debt_offset", paid_changes=paid_changes)

    async def get_user_debts(
        self,
        user: PassiveUser,
        include_settled: bool = False
    ) -> List[UserDebt]:
        """
        Get all debts where this user is the debtor (owes money).

        Args:
            user: User to get debts for
            include_settled: Whether to include already settled debts

        Returns:
            List of UserDebt documents with fetched links
        """
        user_id_for_log = user.user_id if isinstance(user, TelegramUser) else None
        self.logger.trace(
            f"get_user_debts start: user={user.display_name}, user_id={user_id_for_log}, doc_id={user.id}, stable_id={user.stable_id}, include_settled={include_settled}"
        )

        # Support both user document variants that may exist for the same stable identity
        # (PassiveUser <-> TelegramUser conversion keeps stable_id but changes document id).
        debtor_ids: List[Any] = [user.id]

        telegram_user = await TelegramUser.find_one(TelegramUser.stable_id == user.stable_id)
        passive_user = await PassiveUser.find_one(PassiveUser.stable_id == user.stable_id)

        for candidate in (telegram_user, passive_user):
            if candidate and candidate.id not in debtor_ids:
                debtor_ids.append(candidate.id)

        self.logger.trace(f"get_user_debts resolved debtor_ids={debtor_ids}")

        query_filter: Dict[str, Any] = {"debtor.$id": {"$in": debtor_ids}}
        if not include_settled:
            query_filter["is_settled"] = False

        results = await UserDebt.find(query_filter, fetch_links=True).to_list()
        self.logger.trace(f"get_user_debts query returned {len(results)} debts")

        for debt in results:
            debtor_name = debt.debtor.display_name if not isinstance(debt.debtor, BeanieLink) else "?"
            creditor_name = debt.creditor.display_name if not isinstance(debt.creditor, BeanieLink) else "?"
            self.logger.trace(
                f"get_user_debts debt: debtor={debtor_name}, creditor={creditor_name}, total=€{debt.total_amount:.2f}, paid=€{debt.paid_amount:.2f}, correction=€{debt.debt_correction:.2f}, settled={debt.is_settled}"
            )

        return results

    async def get_debt_summary_by_creditor(self, user: PassiveUser) -> Dict[str, Dict[str, Any]]:
        """
        Get a summary of all outstanding debts grouped by creditor.

        Only includes debts where paid_amount < total_amount (not fully paid).

        Args:
            user: User to get debt summary for (the debtor)

        Returns:
            Dict mapping creditor display_name to:
                - creditor_id: creditor's user_id
                - creditor_name: creditor's display_name
                - total_owed: total amount owed to this creditor
                - paypal_link: creditor's PayPal link (if available)
                - debts: list of individual debt objects
        """
        # Get all unsettled debts for this user
        all_debts = await self.get_user_debts(user, include_settled=False)

        # Group by creditor and calculate totals
        creditor_summary = {}

        for debt in all_debts:
            # Skip if fully paid
            if debt.paid_amount >= debt.total_amount:
                continue

            creditor = debt.creditor  # type: ignore
            creditor_name = creditor.display_name  # type: ignore

            # Initialize creditor entry if not exists
            if creditor_name not in creditor_summary:
                creditor_summary[creditor_name] = {
                    "creditor_id": creditor.user_id,  # type: ignore
                    "creditor_name": creditor_name,
                    "total_owed": 0.0,
                    "paypal_link": creditor.paypal_link,  # type: ignore
                    "debts": []
                }

            # Add outstanding amount to total
            outstanding = debt.total_amount - debt.paid_amount
            creditor_summary[creditor_name]["total_owed"] += outstanding
            creditor_summary[creditor_name]["debts"].append(debt)

        return creditor_summary

    async def get_user_credits(
        self,
        user: PassiveUser,
        include_settled: bool = False
    ) -> List[UserDebt]:
        """
        Get all debts where this user is the creditor (owed money).

        Args:
            user: User to get credits for
            include_settled: Whether to include already settled debts

        Returns:
            List of UserDebt documents with fetched links
        """
        # Resolve TelegramUser (creditor links point to `telegram_users`)
        telegram_user = user if isinstance(user, TelegramUser) else await TelegramUser.find_one(TelegramUser.stable_id == user.stable_id)
        if not telegram_user:
            return []

        # Query by DBRef id and manually resolve links afterwards (working pattern)
        if include_settled:
            query = UserDebt.find({"creditor.$id": telegram_user.id})
        else:
            query = UserDebt.find({"creditor.$id": telegram_user.id, "is_settled": False})

        # TODO: check, if we can also just fetch the query links
        results = await query.to_list()

        # Manually resolve Link placeholders to concrete documents
        for debt in results:
            if isinstance(debt.debtor, BeanieLink):
                debt.debtor = cast(Any, await PassiveUser.get(debt.debtor.ref.id) or await TelegramUser.get(debt.debtor.ref.id))
            if isinstance(debt.creditor, BeanieLink):
                debt.creditor = cast(Any, await TelegramUser.get(debt.creditor.ref.id))
            if isinstance(debt.coffee_card, BeanieLink):
                debt.coffee_card = cast(Any, await CoffeeCard.get(debt.coffee_card.ref.id))

        return results

    async def get_debts_for_card(self, card: CoffeeCard) -> List[UserDebt]:
        """
        Get all debt records for a specific coffee card.

        Args:
            card: Coffee card to get debts for

        Returns:
            List of UserDebt documents with fetched links
        """
        # Fetch linked debtor and creditor together
        return await UserDebt.find(UserDebt.coffee_card == card, fetch_links=True).to_list()  # type: ignore

        # return await UserDebt.find({"coffee_card.$id": card.id}, fetch_links=True).to_list()  # type: ignore

    async def record_payment(
        self,
        payer_id: int,
        recipient_id: int,
        amount: float,
        description: Optional[str] = None,
        specific_debt: Optional[UserDebt] = None
    ) -> List[Payment]:
        """
        Record a payment and apply it to debt(s).

        Args:
            payer_id: Telegram user ID of person paying
            recipient_id: Telegram user ID of person receiving payment
            amount: Payment amount in EUR
            payment_method: Method of payment
            description: Optional payment description
            specific_debt: Optional specific debt to apply payment to

        Returns:
            Created Payment document
        """
        repo = get_repo()

        if amount > 0:
            snapshot_manager = self.api.get_snapshot_manager()
            await snapshot_manager.create_snapshot(
                reason="Record Payment",
                context=f"record_payment:{payer_id}->{recipient_id}",
                collections=("user_debts", "payments"),
                save_in_background=True,
            )

        payer = await repo.find_user_by_id(payer_id)
        recipient = await repo.find_user_by_id(recipient_id)

        if not payer or not recipient:
            raise ValueError("Payer or recipient not found")

        self.logger.info(f"Payment recorded: {payer.display_name} → {recipient.display_name}: €{amount:.2f}")

        payment_events: List[Payment] = []

        # Apply payment to debts
        if specific_debt:
            # Apply to specific debt
            _, event = await self._apply_payment_to_debt(
                specific_debt,
                amount,
                reason=PaymentReason.DIRECT_PAYMENT,
                description=description,
            )
            if event is not None:
                payment_events.append(event)
        else:
            # Apply to all unsettled debts from payer to recipient
            debts = await UserDebt.find(
                {"debtor.$id": payer.id, "creditor.$id": recipient.id, "is_settled": False}
            ).to_list()
            
            # debts = await UserDebt.find({
            #     UserDebt.debtor.id: payer.id, 
            #     UserDebt.creditor.id: recipient.id,
            #     UserDebt.is_settled: False
            # }).to_list()

            # Sort by creation date (oldest first)
            debts.sort(key=lambda d: d.created_at)

            remaining = amount
            for debt in debts:
                if remaining <= 0:
                    break
                remaining, event = await self._apply_payment_to_debt(
                    debt,
                    remaining,
                    reason=PaymentReason.DIRECT_PAYMENT,
                    description=description,
                )
                if event is not None:
                    payment_events.append(event)

        request_gsheet_sync_after_action(reason="debt_paid")

        return payment_events

    async def apply_debtor_quick_confirm_payment(
        self,
        *,
        sender_user_id: int,
        expected_debtor_user_id: int | None = None,
        debt_id: str,
    ) -> Tuple[UserDebt, str, float]:
        epsilon = 1e-9

        debt_id = (debt_id or "").strip()
        if not debt_id:
            raise ValueError("Invalid debt reference")

        if expected_debtor_user_id is not None and sender_user_id != expected_debtor_user_id:
            raise ValueError("This payment confirmation isn't for you")

        repo = get_repo()
        sender = await repo.find_user_by_id(sender_user_id)
        if sender is None:
            raise ValueError("User not found")

        debt = await UserDebt.get(debt_id)
        if debt is None:
            raise ValueError("Debt not found")

        if isinstance(debt.creditor, BeanieLink):
            try:
                await debt.fetch_link("creditor")
            except Exception:
                pass

        if isinstance(debt.debtor, BeanieLink):
            debtor_ref_id = debt.debtor.ref.id
            debtor_user = await TelegramUser.get(debtor_ref_id) or await PassiveUser.get(debtor_ref_id)

            if debtor_user is None:
                if expected_debtor_user_id is None:
                    raise ValueError("Debt is missing debtor")
                debtor_user = sender

            debt.debtor = debtor_user

        if isinstance(debt.debtor, BeanieLink):
            raise ValueError("Debt is missing debtor")

        debtor_user = cast(PassiveUser, debt.debtor)
        if debtor_user.stable_id != sender.stable_id:
            raise ValueError("This payment confirmation isn't for you")

        card_name = "this card"
        if isinstance(debt.coffee_card, BeanieLink):
            try:
                await debt.fetch_link("coffee_card")
            except Exception:
                pass
        if not isinstance(debt.coffee_card, BeanieLink):
            card_name = debt.coffee_card.name

        outstanding = max(0.0, float(debt.total_amount) - float(debt.paid_amount))
        if outstanding <= epsilon:
            return debt, card_name, 0.0

        if float(debt.paid_amount) > epsilon:
            # The debt was already partially paid via another mechanism; don't auto-complete it.
            return debt, card_name, 0.0

        snapshot_manager = self.api.get_snapshot_manager()
        await snapshot_manager.create_snapshot(
            reason="Apply Payment (debtor quick confirm)",
            context="apply_payment_debtor_quick_confirm",
            collections=("user_debts", "payments"),
            save_in_background=True,
        )

        await self._apply_payment_to_debt(
            debt,
            outstanding,
            reason=PaymentReason.DEBTOR_MARKED_PAID,
            description="Debtor quick confirm",
        )

        request_gsheet_sync_after_action(reason="debt_paid")

        return debt, card_name, outstanding

    async def _apply_payment_to_debt(
        self,
        debt: UserDebt,
        amount: float,
        *,
        reason: PaymentReason = PaymentReason.MANUAL_ADJUSTMENT,
        description: Optional[str] = None,
    ) -> Tuple[float, Optional[Payment]]:
        """
        Apply a payment amount to a debt.

        Args:
            debt: UserDebt to apply payment to
            amount: Amount to apply

        Returns:
            Remaining amount after application
        """
        epsilon = 1e-9
        now = datetime.now()
        unpaid = debt.total_amount - debt.paid_amount

        card_id = ""
        debtor_id = ""

        try:
            if isinstance(debt.coffee_card, BeanieLink):
                card_id = str(debt.coffee_card.ref.id)
            else:
                card_id = str(debt.coffee_card.id) if debt.coffee_card is not None else ""  # type: ignore[union-attr]
        except Exception:
            card_id = ""

        try:
            if isinstance(debt.debtor, BeanieLink):
                debtor_id = str(debt.debtor.ref.id)
            else:
                debtor_id = str(debt.debtor.id) if debt.debtor is not None else ""  # type: ignore[union-attr]
        except Exception:
            debtor_id = ""

        paid_before = float(getattr(debt, "paid_amount", 0.0) or 0.0)

        if unpaid <= epsilon:
            return amount, None  # Debt already paid, return full amount

        # Apply as much as possible
        to_apply = min(amount, unpaid)
        debt.paid_amount += to_apply

        # Check if debt is now fully settled
        self._update_debt_settlement_state(debt, now=now, epsilon=epsilon)
        if debt.is_settled:
            # TODO: improve this and make it less bloated (maybe dont fetch at all)

            # Only fetch links if they haven't been loaded yet
            if isinstance(debt.debtor, BeanieLink):
                await debt.fetch_link("debtor")
            if isinstance(debt.creditor, BeanieLink):
                await debt.fetch_link("creditor")
            if isinstance(debt.coffee_card, BeanieLink):
                await debt.fetch_link("coffee_card")

            self.logger.info(f"Debt settled: {debt.debtor.display_name} → {debt.creditor.display_name} for card '{debt.coffee_card.name}'")  # type: ignore

        debt.updated_at = now
        await debt.save()

        if card_id and debtor_id:
            request_gsheet_sync_after_action(
                reason="debt_paid",
                paid_changes=[
                    LocalPaidAmountChange(
                        card_id=card_id,
                        debtor_id=debtor_id,
                        value_before=paid_before,
                        value_after=float(getattr(debt, "paid_amount", 0.0) or 0.0),
                    )
                ],
            )

        payment_event = await self._create_payment(
            debt=debt,
            amount=to_apply,
            reason=reason,
            description=description,
        )

        return amount - to_apply, payment_event

    async def get_debt_summary(self, user: TelegramUser) -> Dict[str, Any]:
        """
        Get comprehensive debt summary for a user.

        Returns dict with:
        - debts_i_owe: List of debts where user owes money
        - debts_owed_to_me: List of debts where others owe user
        - total_i_owe: Total amount user owes
        - total_owed_to_me: Total amount owed to user
        - active_cards_debt_estimate: Estimated debt on active (not yet completed) cards
        """
        # Get actual debts from completed cards
        debts_i_owe = await self.get_user_debts(user, include_settled=False)
        debts_owed_to_me = await self.get_user_credits(user, include_settled=False)

        total_i_owe = sum(d.total_amount - d.paid_amount for d in debts_i_owe)
        total_owed_to_me = sum(d.total_amount - d.paid_amount for d in debts_owed_to_me)

        # Calculate estimated debt on active cards (not yet completed)
        # Fetch purchaser links up-front to avoid per-card fetch_link
        active_cards = await CoffeeCard.find(CoffeeCard.is_active == True, fetch_links=True).to_list()
        estimated_debt_on_active = 0.0

        user_stable_id = user.stable_id
        for card in active_cards:
            purchaser: TelegramUser = card.purchaser  # type: ignore

            # If I consumed from someone else's active card
            if purchaser.stable_id != user_stable_id and user_stable_id in card.consumer_stats:
                stats = card.consumer_stats[user_stable_id]
                estimated_debt_on_active += stats.total_coffees * card.cost_per_coffee

        return {
            "debts_i_owe": [
                {
                    "creditor": d.creditor.display_name,  # type: ignore
                    "card_name": d.coffee_card.name,  # type: ignore
                    "total_coffees": d.total_coffees,
                    "total_amount": d.total_amount,
                    "base_amount": d.base_amount,
                    "paid_amount": d.paid_amount,
                    "debt_correction": d.debt_correction,
                    "remaining": d.total_amount - d.paid_amount,
                    "created_at": d.created_at
                }
                for d in debts_i_owe
            ],
            "debts_owed_to_me": [
                {
                    "debtor": d.debtor.display_name,  # type: ignore
                    "card_name": d.coffee_card.name,  # type: ignore
                    "total_coffees": d.total_coffees,
                    "total_amount": d.total_amount,
                    "base_amount": d.base_amount,
                    "paid_amount": d.paid_amount,
                    "debt_correction": d.debt_correction,
                    "remaining": d.total_amount - d.paid_amount,
                    "created_at": d.created_at
                }
                for d in debts_owed_to_me
            ],
            "total_i_owe": total_i_owe,
            "total_owed_to_me": total_owed_to_me,
            "estimated_debt_on_active_cards": estimated_debt_on_active
        }
