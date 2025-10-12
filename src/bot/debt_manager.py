"""
Debt and Payment Management.

Handles debt creation when coffee cards are completed, and payment processing.
Debts are created once per card completion, not on individual orders.
"""

from typing import List, Dict, Optional, Any
from datetime import datetime

from ..models.coffee_models import CoffeeCard, Payment, UserDebt, PaymentMethod
from ..models.beanie_models import TelegramUser, PassiveUser
from ..dependencies.dependencies import get_repo


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
    
    async def complete_card_and_create_debts(self, card: CoffeeCard) -> List[UserDebt]:
        """
        Mark a coffee card as completed and create debt records for all consumers.
        
        This is the PRIMARY method for debt creation. Debts are only created here,
        when a card is marked as completed.
        
        Args:
            card: Coffee card to complete
            
        Returns:
            List of created UserDebt documents
            
        Raises:
            ValueError: If card is already completed
        """
        if not card.is_active:
            raise ValueError(f"Card '{card.name}' is already completed")
        
        # Fetch the purchaser (creditor)
        await card.fetch_link("purchaser")
        creditor: TelegramUser = card.purchaser  # type: ignore
        creditor_stable_id = creditor.stable_id
        
        # Mark card as completed
        card.is_active = False
        card.completed_at = datetime.now()
        await card.save()
        
        print(f"✅ Card '{card.name}' marked as completed")
        
        # Create debts for each consumer (except purchaser)
        created_debts = []
        
        for stable_id, stats in card.consumer_stats.items():
            # Skip purchaser - they don't owe themselves
            if stable_id == creditor_stable_id:
                continue
            
            # Skip if no coffees consumed
            if stats.total_coffees == 0:
                continue
            
            # Find the consumer
            consumer = await PassiveUser.find_one(PassiveUser.stable_id == stable_id)
            if not consumer:
                print(f"⚠️  Warning: Consumer with stable_id '{stable_id}' not found, skipping debt creation")
                continue
            
            # Calculate debt amount
            total_amount = stats.total_coffees * card.cost_per_coffee
            
            # Create debt record
            debt = UserDebt(
                debtor=consumer,  # type: ignore
                creditor=creditor,  # type: ignore
                coffee_card=card,  # type: ignore
                total_coffees=stats.total_coffees,
                cost_per_coffee=card.cost_per_coffee,
                total_amount=total_amount,
                paid_amount=0.0,
                is_settled=False
            )
            await debt.insert()
            created_debts.append(debt)
            
            print(f"💰 Created debt: {stats.display_name} owes {creditor.display_name} €{total_amount:.2f} ({stats.total_coffees} coffees)")
        
        return created_debts
    
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
        query = UserDebt.find(UserDebt.debtor == user)  # type: ignore
        
        if not include_settled:
            query = query.find(UserDebt.is_settled == False)
        
        debts = await query.to_list()
        
        # Fetch related data
        for debt in debts:
            await debt.fetch_link("creditor")
            await debt.fetch_link("coffee_card")
        
        return debts
    
    async def get_user_credits(
        self,
        user: TelegramUser,
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
        query = UserDebt.find(UserDebt.creditor == user)  # type: ignore
        
        if not include_settled:
            query = query.find(UserDebt.is_settled == False)
        
        credits = await query.to_list()
        
        # Fetch related data
        for credit in credits:
            await credit.fetch_link("debtor")
            await credit.fetch_link("coffee_card")
        
        return credits
    
    async def get_debts_for_card(self, card: CoffeeCard) -> List[UserDebt]:
        """
        Get all debt records for a specific coffee card.
        
        Args:
            card: Coffee card to get debts for
            
        Returns:
            List of UserDebt documents with fetched links
        """
        debts = await UserDebt.find(UserDebt.coffee_card == card).to_list()  # type: ignore
        
        # Fetch related data
        for debt in debts:
            await debt.fetch_link("debtor")
            await debt.fetch_link("creditor")
        
        return debts
    
    async def record_payment(
        self,
        payer_id: int,
        recipient_id: int,
        amount: float,
        payment_method: PaymentMethod = PaymentMethod.MANUAL,
        description: Optional[str] = None,
        specific_debt: Optional[UserDebt] = None
    ) -> Payment:
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
        
        payer = await repo.find_user_by_id(payer_id)
        recipient = await repo.find_user_by_id(recipient_id)
        
        if not payer or not recipient:
            raise ValueError("Payer or recipient not found")
        
        # Create payment record
        payment = Payment(
            payer=payer,  # type: ignore
            recipient=recipient,  # type: ignore
            amount=amount,
            payment_method=payment_method,
            description=description
        )
        await payment.insert()
        
        print(f"💳 Payment recorded: {payer.display_name} → {recipient.display_name}: €{amount:.2f}")
        
        # Apply payment to debts
        if specific_debt:
            # Apply to specific debt
            await self._apply_payment_to_debt(specific_debt, amount)
        else:
            # Apply to all unsettled debts from payer to recipient
            debts = await UserDebt.find(
                UserDebt.debtor == payer,  # type: ignore
                UserDebt.creditor == recipient,  # type: ignore
                UserDebt.is_settled == False
            ).to_list()
            
            # Sort by creation date (oldest first)
            debts.sort(key=lambda d: d.created_at)
            
            remaining = amount
            for debt in debts:
                if remaining <= 0:
                    break
                remaining = await self._apply_payment_to_debt(debt, remaining)
        
        return payment
    
    async def _apply_payment_to_debt(self, debt: UserDebt, amount: float) -> float:
        """
        Apply a payment amount to a debt.
        
        Args:
            debt: UserDebt to apply payment to
            amount: Amount to apply
            
        Returns:
            Remaining amount after application
        """
        unpaid = debt.total_amount - debt.paid_amount
        
        if unpaid <= 0:
            return amount  # Debt already paid, return full amount
        
        # Apply as much as possible
        to_apply = min(amount, unpaid)
        debt.paid_amount += to_apply
        
        # Check if debt is now fully settled
        if debt.paid_amount >= debt.total_amount:
            debt.is_settled = True
            debt.settled_at = datetime.now()
            await debt.fetch_link("debtor")
            await debt.fetch_link("creditor")
            await debt.fetch_link("coffee_card")
            print(f"✅ Debt settled: {debt.debtor.display_name} → {debt.creditor.display_name} for card '{debt.coffee_card.name}'")  # type: ignore
        
        await debt.save()
        
        return amount - to_apply
    
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
        active_cards = await CoffeeCard.find(CoffeeCard.is_active == True).to_list()
        estimated_debt_on_active = 0.0
        
        user_stable_id = user.stable_id
        for card in active_cards:
            await card.fetch_link("purchaser")
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
                    "paid_amount": d.paid_amount,
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
                    "paid_amount": d.paid_amount,
                    "remaining": d.total_amount - d.paid_amount,
                    "created_at": d.created_at
                }
                for d in debts_owed_to_me
            ],
            "total_i_owe": total_i_owe,
            "total_owed_to_me": total_owed_to_me,
            "estimated_debt_on_active_cards": estimated_debt_on_active
        }
