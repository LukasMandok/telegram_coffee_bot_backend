from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.debt_manager import DebtManager


@pytest.mark.asyncio
async def test_get_user_debts_resolves_by_stable_id_fallback(monkeypatch):
    api = SimpleNamespace()
    manager = DebtManager(api)

    telegram_doc = SimpleNamespace(id="telegram-doc-id")
    passive_doc = SimpleNamespace(id="passive-doc-id")

    stable_id = "stable-abc"
    user = SimpleNamespace(
        id="current-doc-id",
        user_id=123,
        display_name="Der Schredder",
        stable_id=stable_id,
    )

    fake_debt = SimpleNamespace(
        debtor=SimpleNamespace(display_name="Der Schredder"),
        creditor=SimpleNamespace(display_name="Lukas"),
        coffee_card=SimpleNamespace(name="Card 11"),
        total_amount=4.0,
        paid_amount=0.0,
        debt_correction=0.0,
        is_settled=False,
    )

    find_filter_captured = {}

    class FakeQuery:
        async def to_list(self):
            return [fake_debt]

    def fake_user_debt_find(query_filter, fetch_links=False):
        find_filter_captured["value"] = query_filter
        return FakeQuery()

    monkeypatch.setattr("src.bot.debt_manager.TelegramUser.find_one", AsyncMock(return_value=telegram_doc))
    monkeypatch.setattr("src.bot.debt_manager.PassiveUser.find_one", AsyncMock(return_value=passive_doc))
    monkeypatch.setattr("src.bot.debt_manager.UserDebt.find", fake_user_debt_find)

    debts = await manager.get_user_debts(user, include_settled=False)

    assert len(debts) == 1
    assert find_filter_captured["value"]["is_settled"] is False
    debtor_ids = find_filter_captured["value"]["debtor.$id"]["$in"]
    assert "current-doc-id" in debtor_ids
    assert "telegram-doc-id" in debtor_ids
    assert "passive-doc-id" in debtor_ids
