from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from src.bot.debt_manager import DebtManager


@dataclass
class DebtStub:
    total_amount: float
    paid_amount: float
    created_at: datetime
    updated_at: datetime
    is_settled: bool = False
    settled_at: Optional[datetime] = None


def test_offset_mutual_debts_simple_full_settle_one_side() -> None:
    now = datetime.now()

    # A owes B 6 (new debt)
    ab = DebtStub(
        total_amount=6.0,
        paid_amount=0.0,
        created_at=now,
        updated_at=now,
    )

    # B owes A 10 (older debt)
    ba = DebtStub(
        total_amount=10.0,
        paid_amount=0.0,
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=10),
    )

    modified = DebtManager._offset_mutual_debts_in_memory(debts_ab=[ab], debts_ba=[ba], now=now)

    assert modified == 2

    assert ab.paid_amount == 6.0
    assert ab.is_settled is True
    assert ab.settled_at == now

    assert ba.paid_amount == 6.0
    assert ba.is_settled is False
    assert ba.settled_at is None


def test_offset_mutual_debts_oldest_first_across_lists() -> None:
    now = datetime.now()

    # A -> B debts (oldest first)
    ab1 = DebtStub(
        total_amount=5.0,
        paid_amount=0.0,
        created_at=now - timedelta(days=4),
        updated_at=now - timedelta(days=4),
    )
    ab2 = DebtStub(
        total_amount=5.0,
        paid_amount=0.0,
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2),
    )

    # B -> A debts (oldest first)
    ba1 = DebtStub(
        total_amount=3.0,
        paid_amount=0.0,
        created_at=now - timedelta(days=5),
        updated_at=now - timedelta(days=5),
    )
    ba2 = DebtStub(
        total_amount=10.0,
        paid_amount=0.0,
        created_at=now - timedelta(days=3),
        updated_at=now - timedelta(days=3),
    )

    modified = DebtManager._offset_mutual_debts_in_memory(
        debts_ab=[ab2, ab1],  # intentionally unsorted
        debts_ba=[ba2, ba1],  # intentionally unsorted
        now=now,
    )

    assert modified == 4

    # ba1 (3) offsets ab1 (5): ab1 remaining 2, ba1 settled
    assert ba1.paid_amount == 3.0
    assert ba1.is_settled is True

    # ab1 then offsets 2 against ba2
    assert ab1.paid_amount == 5.0
    assert ab1.is_settled is True

    # ab2 offsets 5 more against ba2 => ba2 paid 7, remaining 3
    assert ab2.paid_amount == 5.0
    assert ab2.is_settled is True

    assert ba2.paid_amount == 7.0
    assert ba2.is_settled is False


def test_offset_mutual_debts_no_op_if_one_side_empty() -> None:
    now = datetime.now()

    ab = DebtStub(
        total_amount=5.0,
        paid_amount=0.0,
        created_at=now,
        updated_at=now,
    )

    modified = DebtManager._offset_mutual_debts_in_memory(debts_ab=[ab], debts_ba=[], now=now)
    assert modified == 0
    assert ab.paid_amount == 0.0
