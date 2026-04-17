"""Admin users management flow (MessageFlow-based).

Implements the `/users` command:
- Overview counts (total/passive/telegram/archived/disabled)
- Status submenu: 2-column keyboard, tap user to cycle status
- Create passive user (first + last name, display_name auto-generated)
- Manage submenu: list users -> details view with actions
"""

from __future__ import annotations

from typing import Any, List, Optional

from pymongo.errors import DuplicateKeyError

from ..message_flow import ButtonCallback, MessageFlow, TextLengthValidator
from ..message_flow_helpers import CommonCallbacks, GridLayout, ListBuilder, NavigationButtons, make_state
from ...models.beanie_models import PassiveUser, TelegramUser
from ...models.coffee_models import CoffeeOrder, UserDebt


STATE_MAIN = "main"
STATE_STATUS_MENU = "status_menu"
STATE_CREATE_FIRST_NAME = "create_first_name"
STATE_CREATE_LAST_NAME = "create_last_name"
STATE_MANAGE_MENU = "manage_menu"
STATE_MANAGE_DETAILS = "manage_details"
STATE_MANAGE_RENAME = "manage_rename"
STATE_DELETE_CONFIRM = "delete_confirm"


CB_OPEN_STATUS_MENU = "open_status_menu"
CB_OPEN_CREATE_PASSIVE = "open_create_passive"
CB_OPEN_MANAGE_MENU = "open_manage_menu"

CB_TOGGLE_STATUS_PREFIX = "toggle_status:"
CB_MANAGE_SELECT_PREFIX = "manage_select:"

CB_DETAILS_TOGGLE_STATUS = "details_toggle_status"
CB_DETAILS_PROMOTE_ADMIN = "details_promote_admin"
CB_DETAILS_REVOKE_ADMIN = "details_revoke_admin"
CB_DETAILS_RENAME = "details_rename"
CB_DETAILS_DELETE = "details_delete"


KEY_USERS_CACHE = "users_flow_users_cache"
KEY_SELECTED_USER_ID = "users_flow_selected_user_id"

KEY_CREATE_FIRST_NAME = "users_flow_create_first_name"


def _callback_id(data: str, prefix: str) -> Optional[str]:
    if not data.startswith(prefix):
        return None
    raw_id = data[len(prefix) :].strip()
    return raw_id or None


async def _notify_user_not_found(api: Any, user_id: int) -> None:
    await api.message_manager.send_text(
        user_id,
        "❌ User not found.",
        vanish=True,
        conv=True,
        delete_after=2,
    )


async def _get_selected_user(flow_state, api: Any) -> Optional[PassiveUser]:
    raw_id = str(flow_state.get(KEY_SELECTED_USER_ID, "")).strip()
    if not raw_id:
        return None
    return await api.conversation_manager.repo.find_user_by_id_string(raw_id)


def _invalidate_users_cache(flow_state) -> None:
    flow_state.pop(KEY_USERS_CACHE, None)


async def _get_all_users(flow_state, api: Any) -> List[PassiveUser]:
    cached = flow_state.get(KEY_USERS_CACHE)
    if isinstance(cached, list):
        return cached

    users = await api.conversation_manager.repo.find_all_users() or []
    users_sorted = sorted(users, key=lambda u: (u.display_name or "").casefold())
    flow_state.set(KEY_USERS_CACHE, users_sorted)
    return users_sorted


def _status_dot(user: PassiveUser) -> str:
    if user.is_disabled:
        return "🔴"
    if user.is_archived:
        return "🟡"
    return "🟢"


def _status_label(user: PassiveUser) -> str:
    if user.is_disabled:
        return "disabled"
    if user.is_archived:
        return "archived"
    return "active"


async def _cycle_status(user: PassiveUser) -> None:
    current = _status_label(user)

    if current == "active":
        user.is_archived = True
        user.is_disabled = False
        if int(user.inactive_card_count) < 2:
            user.inactive_card_count = 2
    elif current == "archived":
        user.is_archived = True
        user.is_disabled = True
        if int(user.inactive_card_count) < 10:
            user.inactive_card_count = 10
    else:
        user.is_archived = False
        user.is_disabled = False
        user.inactive_card_count = 0

    await user.save()


async def _has_orders_or_debts(user: PassiveUser) -> bool:
    if user.id is None:
        return True

    has_order = await CoffeeOrder.find_one(
        {"$or": [{"consumer.$id": user.id}, {"initiator.$id": user.id}]},
        fetch_links=False,
    )
    if has_order:
        return True

    has_debt = await UserDebt.find_one(
        {"$or": [{"debtor.$id": user.id}, {"creditor.$id": user.id}]},
        fetch_links=False,
    )
    return bool(has_debt)


# ============================================================================
# MAIN MENU
# ============================================================================


async def build_main_text(flow_state, api: Any, user_id: int) -> str:
    users = await _get_all_users(flow_state, api)

    total = len(users)
    telegram_count = sum(1 for u in users if isinstance(u, TelegramUser))
    passive_count = total - telegram_count

    archived_count = sum(1 for u in users if u.is_archived and not u.is_disabled)
    disabled_count = sum(1 for u in users if u.is_disabled)

    builder = ListBuilder()
    counts = builder.build(
        title="👥 Users",
        items=[
            ("Total", str(total)),
            ("Telegram", str(telegram_count)),
            ("Passive", str(passive_count)),
            ("Archived", str(archived_count)),
            ("Disabled", str(disabled_count)),
        ],
        align_values=True,
    )

    return (
        f"{counts}\n\n"
        "Choose what to manage:"
    )


async def build_main_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    return [
        [ButtonCallback("🟢🟡🔴 Modify status", CB_OPEN_STATUS_MENU)],
        [
            ButtonCallback("➕ Create passive user", CB_OPEN_CREATE_PASSIVE),
            ButtonCallback("🗂 Manage", CB_OPEN_MANAGE_MENU),
        ],
        NavigationButtons.close(),
    ]


# ============================================================================
# STATUS MENU (2-column)
# ============================================================================


async def build_status_menu_text(flow_state, api: Any, user_id: int) -> str:
    return (
        "🟢🟡🔴 **User status**\n\n"
        "Tap a user to cycle: **active → archived → disabled → active**\n\n"
        "Legend: 🟢 active, 🟡 archived, 🔴 disabled"
    )


async def build_status_menu_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    users = await _get_all_users(flow_state, api)

    items: List[tuple[str, str]] = []
    for user in users:
        user_doc_id = str(user.id) if user.id is not None else ""
        if not user_doc_id:
            continue
        text = f"{_status_dot(user)} {user.display_name}"
        items.append((text, f"{CB_TOGGLE_STATUS_PREFIX}{user_doc_id}"))

    grid = GridLayout(items_per_row=2)
    return grid.build(
        items=items,
        footer_buttons=[[ButtonCallback("◁ Back", CommonCallbacks.BACK)]],
    )


async def handle_status_menu_button(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    if data == CommonCallbacks.BACK:
        return STATE_MAIN

    raw_user_doc_id = _callback_id(data, CB_TOGGLE_STATUS_PREFIX)
    if not raw_user_doc_id:
        return None

    user = await api.conversation_manager.repo.find_user_by_id_string(raw_user_doc_id)
    if not user:
        await _notify_user_not_found(api, user_id)
        _invalidate_users_cache(flow_state)
        return None

    await _cycle_status(user)
    _invalidate_users_cache(flow_state)
    return None


# ============================================================================
# CREATE PASSIVE USER
# ============================================================================


async def build_create_first_name_text(flow_state, api: Any, user_id: int) -> str:
    return (
        "➕ **Create passive user**\n\n"
        "Enter the **first name** (given name):"
    )


async def handle_create_first_name_input(
    input_text: str, flow_state, api: Any, user_id: int
) -> Optional[str]:
    name = (input_text or "").strip()
    if not name:
        return STATE_CREATE_FIRST_NAME

    flow_state.set(KEY_CREATE_FIRST_NAME, name)
    return STATE_CREATE_LAST_NAME


async def build_create_last_name_text(flow_state, api: Any, user_id: int) -> str:
    first_name = flow_state.get(KEY_CREATE_FIRST_NAME, "")
    return (
        "➕ **Create passive user**\n\n"
        f"First name: **{first_name}**\n\n"
        "Enter the **last name** (surname):"
    )


async def handle_create_last_name_input(
    input_text: str, flow_state, api: Any, user_id: int
) -> Optional[str]:
    first_name = str(flow_state.get(KEY_CREATE_FIRST_NAME, "")).strip()
    last_name = (input_text or "").strip()

    if not first_name or not last_name:
        return STATE_CREATE_LAST_NAME

    try:
        new_user = await api.conversation_manager.repo.create_passive_user(
            first_name=first_name,
            last_name=last_name,
        )
    except ValueError as exc:
        await api.message_manager.send_text(
            user_id,
            f"❌ {exc}",
            vanish=True,
            conv=True,
            delete_after=4,
        )
        return STATE_CREATE_FIRST_NAME
    except DuplicateKeyError:
        await api.message_manager.send_text(
            user_id,
            "❌ Display name already exists. Please try again.",
            vanish=True,
            conv=True,
            delete_after=4,
        )
        return STATE_CREATE_FIRST_NAME

    _invalidate_users_cache(flow_state)
    flow_state.pop(KEY_CREATE_FIRST_NAME, None)

    await api.message_manager.send_text(
        user_id,
        f"✅ **Created:** {first_name} {last_name}\n**Display name:** {new_user.display_name}",
        vanish=True,
        conv=True,
        delete_after=4,
    )

    return STATE_MAIN


# ============================================================================
# MANAGE MENU (list -> details)
# ============================================================================


async def build_manage_menu_text(flow_state, api: Any, user_id: int) -> str:
    return "🗂 **Manage users**\n\nSelect a user to view details:"


async def build_manage_menu_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    users = await _get_all_users(flow_state, api)

    items: List[tuple[str, str]] = []
    for user in users:
        user_doc_id = str(user.id) if user.id is not None else ""
        if not user_doc_id:
            continue
        items.append((user.display_name, f"{CB_MANAGE_SELECT_PREFIX}{user_doc_id}"))

    grid = GridLayout(items_per_row=2)
    return grid.build(
        items=items,
        footer_buttons=[[ButtonCallback("◁ Back", CommonCallbacks.BACK)]],
    )


async def handle_manage_menu_button(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    if data == CommonCallbacks.BACK:
        return STATE_MAIN

    raw_user_doc_id = _callback_id(data, CB_MANAGE_SELECT_PREFIX)
    if not raw_user_doc_id:
        return None

    flow_state.set(KEY_SELECTED_USER_ID, raw_user_doc_id)
    return STATE_MANAGE_DETAILS


async def build_manage_details_text(flow_state, api: Any, user_id: int) -> str:
    user = await _get_selected_user(flow_state, api)

    if not user:
        return "❌ **User not found**"

    is_telegram = isinstance(user, TelegramUser)
    kind = "telegram" if is_telegram else "passive"
    status = _status_label(user)

    admin_text = ""
    if is_telegram:
        is_admin = await api.conversation_manager.repo.is_user_admin(int(user.user_id))
        admin_text = f"Admin: **{is_admin}**\n"

    lines = [
        "👤 **User details**",
        "",
        f"Display: **{user.display_name}**",
        f"Type: **{kind}**",
        f"Status: **{status}** {_status_dot(user)}",
        "",
        f"First name: **{user.first_name}**",
        f"Last name: **{user.last_name or ''}**",
        f"Stable ID: `{user.stable_id}`",
        "",
        f"Created: `{user.created_at}`",
        f"Updated: `{user.updated_at}`",
    ]

    if admin_text:
        lines.insert(6, admin_text.rstrip("\n"))

    if is_telegram:
        telegram_lines = [
            "",
            f"Telegram user_id: `{user.user_id}`",
            f"Username: `{user.username}`",
            f"Last login: `{user.last_login}`",
        ]
        if user.paypal_link:
            telegram_lines.append(f"PayPal: {user.paypal_link}")
        lines.extend(telegram_lines)

    return "\n".join(lines)


async def build_manage_details_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    user = await _get_selected_user(flow_state, api)

    if not user:
        return [[ButtonCallback("◁ Back", CommonCallbacks.BACK)]]

    rows: List[List[ButtonCallback]] = [
        [ButtonCallback("🟢🟡🔴 Cycle status", CB_DETAILS_TOGGLE_STATUS)],
        [ButtonCallback("✏️ Change display name", CB_DETAILS_RENAME)],
    ]

    if isinstance(user, TelegramUser):
        is_admin = await api.conversation_manager.repo.is_user_admin(int(user.user_id))
        if is_admin:
            rows.insert(1, [ButtonCallback("🚫 Revoke admin", CB_DETAILS_REVOKE_ADMIN)])
        else:
            rows.insert(1, [ButtonCallback("⭐ Promote to admin", CB_DETAILS_PROMOTE_ADMIN)])

    if not await _has_orders_or_debts(user):
        rows.append([ButtonCallback("🗑 Delete user", CB_DETAILS_DELETE)])

    rows.append([ButtonCallback("◁ Back", CommonCallbacks.BACK)])
    return rows


async def handle_manage_details_button(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    if data == CommonCallbacks.BACK:
        return STATE_MANAGE_MENU

    user = await _get_selected_user(flow_state, api)

    if not user:
        await _notify_user_not_found(api, user_id)
        return STATE_MANAGE_MENU

    if data == CB_DETAILS_TOGGLE_STATUS:
        await _cycle_status(user)
        _invalidate_users_cache(flow_state)
        return None

    if data == CB_DETAILS_PROMOTE_ADMIN and isinstance(user, TelegramUser):
        ok = await api.conversation_manager.repo.add_admin(int(user.user_id))
        if not ok:
            await api.message_manager.send_text(
                user_id,
                "❌ Failed to promote user to admin.",
                vanish=True,
                conv=True,
                delete_after=3,
            )
        return None

    if data == CB_DETAILS_REVOKE_ADMIN and isinstance(user, TelegramUser):
        ok = await api.conversation_manager.repo.remove_admin(int(user.user_id))
        if not ok:
            await api.message_manager.send_text(
                user_id,
                "❌ Failed to revoke admin rights.",
                vanish=True,
                conv=True,
                delete_after=3,
            )
        return None

    if data == CB_DETAILS_RENAME:
        return STATE_MANAGE_RENAME

    if data == CB_DETAILS_DELETE:
        if await _has_orders_or_debts(user):
            await api.message_manager.send_text(
                user_id,
                "❌ Cannot delete: user has orders and/or debts.",
                vanish=True,
                conv=True,
                delete_after=3,
            )
            return None
        return STATE_DELETE_CONFIRM

    return None


# ============================================================================
# DELETE USER
# ============================================================================


async def build_delete_confirm_text(flow_state, api: Any, user_id: int) -> str:
    user = await _get_selected_user(flow_state, api)

    if not user:
        return "❌ **User not found**"

    return (
        "🗑 **Delete user**\n\n"
        f"User: **{user.display_name}**\n\n"
        "This will permanently delete the user.\n"
        "This cannot be undone."
    )


async def build_delete_confirm_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    return [
        [ButtonCallback("🗑 Delete", CommonCallbacks.CONFIRM)],
        [ButtonCallback("◁ Back", CommonCallbacks.BACK)],
    ]


async def handle_delete_confirm_button(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    if data == CommonCallbacks.BACK:
        return STATE_MANAGE_DETAILS

    if data != CommonCallbacks.CONFIRM:
        return None

    user = await _get_selected_user(flow_state, api)
    if not user:
        await _notify_user_not_found(api, user_id)
        return STATE_MANAGE_MENU

    if await _has_orders_or_debts(user):
        await api.message_manager.send_text(
            user_id,
            "❌ Cannot delete: user has orders and/or debts.",
            vanish=True,
            conv=True,
            delete_after=3,
        )
        return STATE_MANAGE_DETAILS

    if isinstance(user, TelegramUser):
        await api.conversation_manager.repo.remove_admin(int(user.user_id))

    await user.delete()

    _invalidate_users_cache(flow_state)
    flow_state.pop(KEY_SELECTED_USER_ID, None)
    return STATE_MANAGE_MENU


# ============================================================================
# RENAME DISPLAY NAME
# ============================================================================


async def build_manage_rename_text(flow_state, api: Any, user_id: int) -> str:
    user = await _get_selected_user(flow_state, api)

    current = user.display_name if user else "(unknown)"
    return (
        "✏️ **Change display name**\n\n"
        f"Current: **{current}**\n\n"
        "Enter the new display name:"
    )


async def handle_manage_rename_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    user = await _get_selected_user(flow_state, api)
    if not user:
        return STATE_MANAGE_MENU

    new_name = (input_text or "").strip()
    if not new_name:
        return STATE_MANAGE_RENAME

    existing = await api.conversation_manager.repo.find_user_by_display_name(new_name)
    if existing and existing.stable_id != user.stable_id:
        await api.message_manager.send_text(
            user_id,
            "❌ Display name already exists.",
            vanish=True,
            conv=True,
            delete_after=3,
        )
        return STATE_MANAGE_RENAME

    user.display_name = new_name

    try:
        await user.save()
    except DuplicateKeyError:
        await api.message_manager.send_text(
            user_id,
            "❌ Display name already exists.",
            vanish=True,
            conv=True,
            delete_after=3,
        )
        return STATE_MANAGE_RENAME

    _invalidate_users_cache(flow_state)
    await api.message_manager.send_text(
        user_id,
        f"✅ Updated display name to {new_name}",
        vanish=True,
        conv=True,
        delete_after=2,
    )

    return STATE_MANAGE_DETAILS


def create_users_flow() -> MessageFlow:
    flow = MessageFlow()

    flow.add_state(
        make_state(
            STATE_MAIN,
            text_builder=build_main_text,
            keyboard_builder=build_main_keyboard,
            exit_buttons=[CommonCallbacks.CLOSE],
            next_state_map={
                CB_OPEN_STATUS_MENU: STATE_STATUS_MENU,
                CB_OPEN_CREATE_PASSIVE: STATE_CREATE_FIRST_NAME,
                CB_OPEN_MANAGE_MENU: STATE_MANAGE_MENU,
            },
        )
    )

    flow.add_state(
        make_state(
            STATE_STATUS_MENU,
            text_builder=build_status_menu_text,
            keyboard_builder=build_status_menu_keyboard,
            exit_buttons=[],
            on_button_press=handle_status_menu_button,
        )
    )

    flow.add_state(
        make_state(
            STATE_CREATE_FIRST_NAME,
            text_builder=build_create_first_name_text,
            buttons=[[ButtonCallback("◁ Back", CommonCallbacks.BACK)]],
            exit_buttons=[],
            next_state_map={CommonCallbacks.BACK: STATE_MAIN},
            input_validator=TextLengthValidator(min_length=1, max_length=40),
            on_input_received=handle_create_first_name_input,
        )
    )

    flow.add_state(
        make_state(
            STATE_CREATE_LAST_NAME,
            text_builder=build_create_last_name_text,
            buttons=[[ButtonCallback("◁ Back", CommonCallbacks.BACK)]],
            exit_buttons=[],
            next_state_map={CommonCallbacks.BACK: STATE_CREATE_FIRST_NAME},
            input_validator=TextLengthValidator(min_length=1, max_length=60),
            on_input_received=handle_create_last_name_input,
        )
    )

    flow.add_state(
        make_state(
            STATE_MANAGE_MENU,
            text_builder=build_manage_menu_text,
            keyboard_builder=build_manage_menu_keyboard,
            exit_buttons=[],
            on_button_press=handle_manage_menu_button,
        )
    )

    flow.add_state(
        make_state(
            STATE_MANAGE_DETAILS,
            text_builder=build_manage_details_text,
            keyboard_builder=build_manage_details_keyboard,
            exit_buttons=[],
            on_button_press=handle_manage_details_button,
        )
    )

    flow.add_state(
        make_state(
            STATE_DELETE_CONFIRM,
            text_builder=build_delete_confirm_text,
            keyboard_builder=build_delete_confirm_keyboard,
            exit_buttons=[],
            on_button_press=handle_delete_confirm_button,
        )
    )

    flow.add_state(
        make_state(
            STATE_MANAGE_RENAME,
            text_builder=build_manage_rename_text,
            buttons=[[ButtonCallback("◁ Back", CommonCallbacks.BACK)]],
            exit_buttons=[],
            next_state_map={CommonCallbacks.BACK: STATE_MANAGE_DETAILS},
            input_validator=TextLengthValidator(min_length=2, max_length=40),
            on_input_received=handle_manage_rename_input,
        )
    )

    return flow
