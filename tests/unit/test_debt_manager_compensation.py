from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import sys
import types

# Isolate pure DebtManager logic tests from dependency import cycles.
dependencies_stub = types.ModuleType("src.dependencies.dependencies")
dependencies_stub.get_repo = lambda: None
sys.modules.setdefault("src.dependencies.dependencies", dependencies_stub)

from src.bot.debt_manager import DebtManager


@dataclass
class FakeDebt:
    total_amount: float
    paid_amount: float
    created_at: datetime
    updated_at: datetime
    is_settled: bool = False
    settled_at: Optional[datetime] = None


def test_update_debt_settlement_state_marks_settled_when_fully_paid():
    now = datetime.now()
    debt = FakeDebt(
        total_amount=4.0,
        paid_amount=4.0,
        created_at=now,
        updated_at=now,
        is_settled=False,
        settled_at=None,
    )

    DebtManager._update_debt_settlement_state(debt, now=now)

    assert debt.is_settled is True
    assert debt.paid_amount == 4.0
    assert debt.settled_at == now


def test_update_debt_settlement_state_marks_unsettled_when_partial():
    now = datetime.now()
    debt = FakeDebt(
        total_amount=4.0,
        paid_amount=1.5,
        created_at=now,
        updated_at=now,
        is_settled=True,
        settled_at=now,
    )

    DebtManager._update_debt_settlement_state(debt, now=now)

    assert debt.is_settled is False
    assert debt.paid_amount == 1.5
    assert debt.settled_at is None


def test_offset_mutual_debts_oldest_first_with_partial_last_pair():
    base = datetime.now()

    debt_ab_old = FakeDebt(
        total_amount=3.0,
        paid_amount=0.0,
        created_at=base,
        updated_at=base,
    )
    debt_ab_new = FakeDebt(
        total_amount=5.0,
        paid_amount=0.0,
        created_at=base + timedelta(seconds=10),
        updated_at=base + timedelta(seconds=10),
    )

    debt_ba_old = FakeDebt(
        total_amount=4.0,
        paid_amount=0.0,
        created_at=base + timedelta(seconds=1),
        updated_at=base + timedelta(seconds=1),
    )

    events = DebtManager._offset_mutual_debts_in_memory(
        debts_ab=[debt_ab_new, debt_ab_old],
        debts_ba=[debt_ba_old],
        now=base + timedelta(minutes=1),
    )

    # two offset steps: 3.0 (oldest AB vs BA), then 1.0 (newer AB vs remaining BA)
    assert len(events) == 2
    assert events[0][0] is debt_ab_old
    assert events[0][1] is debt_ba_old
    assert events[0][2] == 3.0
    assert events[1][0] is debt_ab_new
    assert events[1][1] is debt_ba_old
    assert events[1][2] == 1.0

    # settled states after compensation
    assert debt_ab_old.paid_amount == 3.0
    assert debt_ab_old.is_settled is True

    assert debt_ba_old.paid_amount == 4.0
    assert debt_ba_old.is_settled is True

    # partial compensation on the larger debt remains unsettled
    assert debt_ab_new.paid_amount == 1.0
    assert debt_ab_new.is_settled is False
