"""Registration flow (MessageFlow-based).

Converts the legacy registration conversation into the MessageFlow system.

All database operations remain in `handlers.users`.
"""

from __future__ import annotations

from typing import Any, Optional
from pymongo.errors import DuplicateKeyError

from ..message_flow import MessageFlow, ButtonCallback
from ..message_flow_helpers import make_state, get_persistent_keyboard, ExitStateBuilder
from ..message_flow_ids import CommonCallbacks, CommonStateIds

from ...handlers import users
from ...database.beanie_repo import DisplayNameConflictError


def create_registration_flow() -> MessageFlow:
    flow = MessageFlow()

    # State identifiers
    STATE_CONFIRM = "confirm_register"
    STATE_PASSWORD = "password_input"
    STATE_ASK_FIRST = "ask_first_name"
    STATE_ASK_LAST = "ask_last_name"
    STATE_ASK_DISPLAY = "ask_display_name"
    SKIP_LAST_CB = "skip_last_name"
    STATE_TAKEOVER_DECLINE_CONFIRM = "takeover_decline_confirm"
    STATE_TAKEOVER = "takeover_confirm"

    async def _send_registration_success(api: Any, user_id: int, new_user: Optional[Any] = None, first_name: Optional[str] = None) -> None:
        if new_user:
            name = getattr(new_user, "first_name", None) or first_name or ""
            display = getattr(new_user, "display_name", None)
            message = (
                f"✅ Registration successful! Welcome {name}!\n"
                f"Your display name: **{display}**\n\n"

                "💡 Getting Started\n"
                " - send a number of coffees to quick-order for yourself.\n"
                " - enter /order to order coffees for a larger group.\n"
                " - enter /help for a complete overview about all commands.\n"
            )
        else:
            message = (
                f"✅ Registration successful! Welcome {first_name or ''}!\n\n"
                "💡 Getting Started\n"
                " - send a number of coffees to quick-order for yourself.\n"
                " - enter /order to order coffees for a larger group.\n"
                " - enter /help for a complete overview about all commands.\n"
            )
        await api.message_manager.send_keyboard(
            user_id,
            message,
            get_persistent_keyboard(),
            True,
            True,
        )

    async def _handle_duplicate_key_error(err: str, api: Any, user_id: int) -> Optional[str]:
        if "phone_1 dup key" in err:
            await api.message_manager.send_text(
                user_id,
                "❌ A user with your phone number is already registered. Please contact your admin.",
                True,
                True,
            )
            return CommonStateIds.EXIT_CANCELLED
        if "display_name" in err:
            await api.message_manager.send_text(
                user_id,
                "❌ That display name is already taken. Please choose another.",
                True,
                True,
            )
            return STATE_ASK_DISPLAY
        await api.message_manager.send_text(
            user_id,
            "❌ This user account is already registered. Please contact your admin if you believe this is an error.",
            True,
            True,
        )
        return CommonStateIds.EXIT_CANCELLED

    # Helper: create user or route to takeover
    async def _create_user_from_flow(flow_state, api: Any, user_id: int) -> Optional[str]:
        first_name = flow_state.get("reg_first_name")
        last_name = flow_state.get("reg_last_name")
        username = flow_state.get("reg_username")
        phone = flow_state.get("reg_phone")
        photo_id = flow_state.get("reg_photo_id")
        lang_code = flow_state.get("reg_lang_code", "en")

        # Check for existing passive user (skip if user chose manual name entry)
        skip_takeover = bool(flow_state.get("skip_passive_takeover", False))
        if not skip_takeover:
            existing = await users.find_passive_user_by_name(first_name=first_name, last_name=last_name)
            if existing:
                flow_state.set("existing_passive_user", existing)
                return STATE_TAKEOVER

        # Create new telegram user
        try:
            new_user = await users.register_user(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                photo_id=photo_id,
                lang_code=lang_code,
            )

            await _send_registration_success(api, user_id, new_user=new_user)
            return "__exit__"

        except DuplicateKeyError as e:
            return await _handle_duplicate_key_error(str(e), api, user_id)
        except DisplayNameConflictError as e:
            # Existing user has identical first+last name; ask the registrant to choose a display name
            await api.message_manager.send_text(
                user_id,
                (
                    f"❌ A user with the same name already exists with the display name '{e.existing_display_name}'.\n\n"
                    "Please choose your own display name (you cannot use the existing one)."
                ),
                True,
                True,
            )
            # Store existing conflicting display name for reference
            flow_state.set("existing_conflict_display_name", e.existing_display_name)
            # We are asking the registrant for a custom display name because
            # automatic generation failed for an exact-name collision. Ensure
            # we won't accidentally convert the passive user by clearing it
            # and marking that the registrant opted out of takeover.
            flow_state.set("existing_passive_user", None)
            flow_state.set("skip_passive_takeover", True)
            return STATE_ASK_DISPLAY

        except Exception:
            await api.message_manager.send_text(
                user_id,
                "❌ Registration failed. Please try again later.",
                True,
                True,
            )
            return CommonStateIds.EXIT_CANCELLED


    async def handle_password_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
        pw = (input_text or "").strip()
        if not pw:
            return STATE_PASSWORD

        # Check password via handlers (decorated with repo injection)
        ok = await users.check_password(pw)
        if not ok:
            tries = int(flow_state.get("reg_password_tries", 0)) + 1
            flow_state.set("reg_password_tries", tries)
            if tries < 3:
                await api.message_manager.send_text(
                    user_id,
                    f"❌ Password incorrect. Please try again. ({tries}/3 attempts used)",
                    vanish=True,
                    conv=True,
                    delete_after=4,
                )
                return STATE_PASSWORD
            await api.message_manager.send_text(
                user_id,
                "❌ Too many incorrect attempts. Registration aborted.",
                True,
                True,
            )
            return CommonStateIds.EXIT_CANCELLED

        # Password correct -> fetch Telegram entity
        user_entity = await api.bot.get_entity(user_id)
        username = getattr(user_entity, "username", None)
        first_name = getattr(user_entity, "first_name", None)
        last_name = getattr(user_entity, "last_name", None)
        phone = getattr(user_entity, "phone", None)
        photo = getattr(user_entity, "photo", None)
        lang_code = getattr(user_entity, "lang_code", "en")

        if phone is not None and (not isinstance(phone, str) or not phone.strip()):
            phone = None

        if photo and hasattr(photo, "photo_id"):
            photo = photo.photo_id
        else:
            photo = None

        if first_name is not None:
            first_name = first_name.strip().title()

        if last_name is not None:
            last_name = last_name.strip().title()

        if username is None and first_name:
            username = f"{first_name.lower()}_{last_name.lower()}" if last_name else first_name.lower()

        flow_state.set("reg_username", username)
        flow_state.set("reg_first_name", first_name)
        flow_state.set("reg_last_name", last_name)
        flow_state.set("reg_phone", phone)
        flow_state.set("reg_photo_id", photo)
        flow_state.set("reg_lang_code", lang_code)

        # Ask for missing names if necessary
        if not first_name:
            return STATE_ASK_FIRST
        if not last_name:
            return STATE_ASK_LAST

        # Otherwise try to create or takeover
        return await _create_user_from_flow(flow_state, api, user_id)


    async def handle_first_name_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
        name = (input_text or "").strip().title()
        if not name:
            return STATE_ASK_FIRST
        flow_state.set("reg_first_name", name)
        # Mark that the user entered names manually (used to decide takeover prompts)
        flow_state.set("reg_names_entered", True)
        if not flow_state.get("reg_last_name"):
            return STATE_ASK_LAST
        return await _create_user_from_flow(flow_state, api, user_id)


    async def handle_last_name_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
        name = (input_text or "").strip().title()
        if not name:
            return STATE_ASK_LAST
        flow_state.set("reg_last_name", name)
        # Mark that the user entered names manually (used to decide takeover prompts)
        flow_state.set("reg_names_entered", True)
        return await _create_user_from_flow(flow_state, api, user_id)


    async def handle_last_name_button(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
        if data == SKIP_LAST_CB:
            # User chose to skip entering a last name
            flow_state.set("reg_last_name", None)
            # Mark that the user made an explicit choice about their name
            flow_state.set("reg_names_entered", True)
            return await _create_user_from_flow(flow_state, api, user_id)
        return None


    async def build_takeover_text(flow_state, api: Any, user_id: int) -> str:
        existing = flow_state.get("existing_passive_user")
        if not existing:
            return "❌ No matching passive user found."
        return (
            f"Found existing user: **{existing.first_name} {existing.last_name or ''}**\n\n"
            "Do you want to take over this user?"
        )


    async def handle_takeover_button(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
        if data == CommonCallbacks.BACK:
            passive = flow_state.get("existing_passive_user")
            # If there is a passive user and the registrant already has both
            # first and last name available (e.g., from Telegram profile),
            # attempt to create a new account immediately. If creation fails
            # due to an identical-name conflict, _create_user_from_flow will
            # prompt for a display name.
            if passive:
                reg_first = (flow_state.get("reg_first_name") or "").strip()
                reg_last = (flow_state.get("reg_last_name") or "").strip()
                if reg_first and reg_last:
                    flow_state.set("skip_passive_takeover", True)
                    return await _create_user_from_flow(flow_state, api, user_id)

                # Missing one or both names -> ask for them. Keep the
                # `existing_passive_user` in state so that after the user
                # provides names the flow can re-evaluate takeover.
                if not reg_first:
                    return STATE_ASK_FIRST
                return STATE_ASK_LAST

            # No passive user found — behave like a normal manual registration.
            flow_state.set("skip_passive_takeover", True)
            return STATE_ASK_FIRST

        if data != CommonCallbacks.CONFIRM:
            return None

        passive = flow_state.get("existing_passive_user")
        if not passive:
            await api.message_manager.send_text(user_id, "❌ Passive user not found.", True, True)
            return CommonStateIds.EXIT_CANCELLED

        try:
            new_user = await users.convert_passive_to_telegram_user(
                passive_user=passive,
                user_id=user_id,
                username=flow_state.get("reg_username"),
                first_name=flow_state.get("reg_first_name"),
                last_name=flow_state.get("reg_last_name"),
                phone=flow_state.get("reg_phone"),
                photo_id=flow_state.get("reg_photo_id"),
                lang_code=flow_state.get("reg_lang_code", "en"),
            )

            await _send_registration_success(api, user_id, new_user=new_user)
            return "__exit__"

        except DuplicateKeyError as e:
            return await _handle_duplicate_key_error(str(e), api, user_id)

        except Exception:
            await api.message_manager.send_text(
                user_id,
                "❌ Failed to take over existing user. Please try again or contact admin.",
                True,
                True,
            )
            return CommonStateIds.EXIT_CANCELLED


    async def handle_takeover_decline_confirm_button(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
        # This handler is used when the user declined takeover but their name exactly
        # matches an existing passive user and we ask them to confirm the decline.
        if data == CommonCallbacks.CONFIRM:
            # User confirmed they do NOT want to take over -> attempt to create a new user.
            flow_state.set("skip_passive_takeover", True)
            # Clear the passive user so subsequent steps don't try to convert it.
            flow_state.set("existing_passive_user", None)
            return await _create_user_from_flow(flow_state, api, user_id)

        if data == CommonCallbacks.BACK:
            # User changed their mind and wants to go back to takeover choice.
            return STATE_TAKEOVER

        return None


    # Build flow states
    # Confirmation
    flow.add_confirmation(
        STATE_CONFIRM,
        "Do you want to register?",
        on_confirm_state=STATE_PASSWORD,
        on_cancel_state=CommonStateIds.EXIT_CANCELLED,
    )

    # Password input
    flow.add_state(
        make_state(
            STATE_PASSWORD,
            text="🔒 Please enter the registration password:",
            input_prompt=None,
            on_input_received=handle_password_input,
            timeout=60,
        )
    )

    # Ask first name
    flow.add_state(
        make_state(
            STATE_ASK_FIRST,
            text="Please provide your first name to complete registration.",
            on_input_received=handle_first_name_input,
            input_prompt=None,
            timeout=45,
        )
    )

    # Ask last name
    flow.add_state(
        make_state(
            STATE_ASK_LAST,
            text="Please provide your last name to complete registration.",
            on_input_received=handle_last_name_input,
            on_button_press=handle_last_name_button,
            buttons=[[ButtonCallback("Skip", SKIP_LAST_CB)]],
            input_prompt=None,
            timeout=45,
        )
    )

    # Ask for custom display name (used when autogenerated candidate collides)
    async def handle_display_name_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
        name = (input_text or "").strip()
        if not name:
            await api.message_manager.send_text(user_id, "Please enter a display name.", True, True)
            return STATE_ASK_DISPLAY

        # Decide whether we are converting a passive user or creating a new one.
        # If the registrant explicitly chose to skip passive-user takeover,
        # always create a new Telegram user even if an `existing_passive_user`
        # is still present in the state.
        skip_takeover = bool(flow_state.get("skip_passive_takeover", False))
        passive = None if skip_takeover else flow_state.get("existing_passive_user")
        try:
            if passive:
                new_user = await users.convert_passive_to_telegram_user(
                    passive_user=passive,
                    user_id=user_id,
                    username=flow_state.get("reg_username"),
                    first_name=flow_state.get("reg_first_name"),
                    last_name=flow_state.get("reg_last_name"),
                    phone=flow_state.get("reg_phone"),
                    photo_id=flow_state.get("reg_photo_id"),
                    lang_code=flow_state.get("reg_lang_code", "en"),
                    display_name=name,
                )
            else:
                new_user = await users.register_user(
                    user_id=user_id,
                    username=flow_state.get("reg_username"),
                    first_name=flow_state.get("reg_first_name"),
                    last_name=flow_state.get("reg_last_name"),
                    phone=flow_state.get("reg_phone"),
                    photo_id=flow_state.get("reg_photo_id"),
                    lang_code=flow_state.get("reg_lang_code", "en"),
                    display_name=name,
                )

            await _send_registration_success(api, user_id, new_user=new_user)
            return "__exit__"

        except DuplicateKeyError as e:
            return await _handle_duplicate_key_error(str(e), api, user_id)

        except Exception:
            await api.message_manager.send_text(user_id, "❌ Registration failed. Please try again later.", True, True)
            return CommonStateIds.EXIT_CANCELLED

    flow.add_state(
        make_state(
            STATE_ASK_DISPLAY,
            text="The display name we tried is already taken. Please choose your display name:",
            on_input_received=handle_display_name_input,
            input_prompt=None,
            timeout=120,
        )
    )

    # Takeover confirmation (Yes/No)
    flow.add_state(
        make_state(
            STATE_TAKEOVER,
            text_builder=build_takeover_text,
            buttons=[[ButtonCallback("Yes", CommonCallbacks.CONFIRM), ButtonCallback("No", CommonCallbacks.BACK)]],
            on_button_press=handle_takeover_button,
            timeout=60,
        )
    )

    # Confirm-decline state: user has identical name to passive user and declined takeover
    flow.add_state(
        make_state(
            STATE_TAKEOVER_DECLINE_CONFIRM,
            text="Are you sure you don't want to take over this existing user?",
            buttons=[[ButtonCallback("Yes", CommonCallbacks.CONFIRM), ButtonCallback("No", CommonCallbacks.BACK)]],
            on_button_press=handle_takeover_decline_confirm_button,
            timeout=60,
        )
    )

    # Cancellation / success states (use exit builders)
    flow.add_state(ExitStateBuilder.create_cancelled())
    flow.add_state(ExitStateBuilder.create_success())

    return flow
