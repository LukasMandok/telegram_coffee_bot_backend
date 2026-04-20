"""Settings-related flows using MessageFlow system.

This module contains flows for user settings and configuration,
including PayPal link setup.
"""

from typing import Optional, List

from ..common.log import Logger
from .message_flow import (
    MessageFlow, MessageDefinition, ButtonCallback,
    MessageAction, StateType, TextLengthValidator
)
from .message_flow_helpers import (
    CommonCallbacks,
    CommonStateIds,
    ExitStateBuilder,
    NavigationButtons,
    make_state,
)
from ..handlers.paypal import create_paypal_link, validate_paypal_link


STATE_MAIN = "main"
STATE_ENTER_LINK = "enter_link"
STATE_CONFIRM_SAVE = "confirm_save"
STATE_CONFIRM_REMOVE = "confirm_remove"

STATE_EXIT_REMOVAL_CANCELLED = "exit_removal_cancelled"
STATE_EXIT_SAVE_SUCCESS = "exit_save_success"
STATE_EXIT_REMOVE_SUCCESS = "exit_remove_success"

CB_ADD = "add"
CB_CHANGE = "change"
CB_REMOVE = "remove"

KEY_PAYPAL_INPUT = "paypal_input"
KEY_VALIDATION_ERROR_TEXT = "validation_error_text"
KEY_FORMATTED_LINK = "formatted_link"
KEY_ORIGINAL_INPUT = "original_input"
KEY_VALIDATION_ATTEMPTS = "validation_attempts"
KEY_OLD_PAYPAL_LINK = "old_paypal_link"


# ============================================================================
# PAYPAL LINK SETUP FLOW
# ============================================================================

async def build_paypal_main_text(flow_state, api, user_id) -> str:
    """Build the main PayPal setup text."""
    user = await api.conversation_manager.repo.find_user_by_id(user_id)
    
    if user.paypal_link:
        return (
            f"💳 **Current PayPal Link**\n\n"
            f"Your current PayPal link: {user.paypal_link}\n\n"
            f"What would you like to do?"
        )
    else:
        return (
            "💳 **PayPal Setup**\n\n"
            "Set up your PayPal.me link to receive payments for coffee cards.\n\n"
            "What would you like to do?"
        )


async def handle_paypal_main_button(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle main PayPal menu button presses."""
    if data in (CB_CHANGE, CB_ADD):
        return STATE_ENTER_LINK
    if data == CB_REMOVE:
        return STATE_CONFIRM_REMOVE
    if data in (CommonCallbacks.CLOSE, CommonCallbacks.CANCEL):
        return CommonStateIds.EXIT_CANCELLED
    return None


# ============================================================================
# ENTER LINK STATE
# ============================================================================

async def build_enter_link_text(flow_state, api, user_id) -> str:
    """Build the text for entering a PayPal link."""
    # Check if there's a validation error to display
    validation_error = flow_state.get(KEY_VALIDATION_ERROR_TEXT)
    if validation_error:
        # Clear it after using it
        flow_state.pop(KEY_VALIDATION_ERROR_TEXT, None)
        return validation_error
    
    # Normal prompt text
    user = await api.conversation_manager.repo.find_user_by_id(user_id)
    
    if user.paypal_link:
        intro = "🔄 **Change PayPal Link**\n\n"
    else:
        intro = "➕ **Add PayPal Link**\n\n"
    
    return (
        f"{intro}"
        "Please provide either:\n"
        "• Your PayPal username (e.g., `LukasMandok`)\n"
        "• Your full PayPal.me link (e.g., `https://paypal.me/LukasMandok`)\n\n"
        "ℹ️ Don't know your PayPal.me link? Check: https://www.paypal.com/myaccount/profile/\n\n"
        "💡 Type your username or link below:"
    )




async def handle_paypal_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
    """Handle PayPal link input and validate it."""
    logger = Logger("PayPalFlow")
    
    # Format the link
    formatted_link, username = create_paypal_link(input_text)
    logger.info(f"User {user_id} input: '{input_text}' -> Formatted: '{formatted_link}'")
    
    # Validate immediately
    try:
        logger.info(f"Starting validation for: {formatted_link}")
        is_valid = await validate_paypal_link(formatted_link, username)
        logger.info(f"Validation result: {is_valid} for link: {formatted_link}")
        
        if is_valid:
            # Store the formatted link and move to confirm
            flow_state.set(KEY_FORMATTED_LINK, formatted_link)
            flow_state.set(KEY_ORIGINAL_INPUT, input_text)
            logger.info(f"PayPal link validated successfully for user {user_id}: {formatted_link}")
            return STATE_CONFIRM_SAVE
        else:
            # Validation failed - show error and stay in input state
            logger.warning(f"PayPal link validation failed for user {user_id}: {formatted_link}")
            flow_state.set(KEY_FORMATTED_LINK, formatted_link)
            
            # Get attempt count
            attempts = flow_state.get(KEY_VALIDATION_ATTEMPTS, 0) + 1
            flow_state.set(KEY_VALIDATION_ATTEMPTS, attempts)
            logger.debug(f"Validation attempt {attempts}/3 for user {user_id}")
            
            max_attempts = 3
            if attempts >= max_attempts:
                # Max attempts reached, exit
                logger.warning(f"Max validation attempts ({max_attempts}) reached for user {user_id}")
                await api.message_manager.send_text(
                    user_id,
                    f"❌ **Maximum Attempts Reached**\n\n"
                    f"PayPal setup failed after {max_attempts} attempts.\n"
                    f"The link could not be validated: {formatted_link}\n\n"
                    f"Please try again later with /paypal or contact support if you believe this is an error.",
                    vanish=True,
                    conv=True,
                    delete_after=10
                )
                return None  # Exit flow
            
            # Show error by updating the message and stay in input state
            remaining = max_attempts - attempts
            flow_state.set(KEY_VALIDATION_ERROR_TEXT, 
                f"❌ **Validation Failed** (Attempt {attempts}/{max_attempts})\n\n"
                f"The PayPal link is not valid or doesn't exist:\n"
                f"{formatted_link}\n\n"
                f"Please check:\n"
                f"• Is your username correct?\n"
                f"• Does your PayPal.me link exist?\n"
                f"• Visit: https://www.paypal.com/myaccount/profile/\n\n"
                f"You have **{remaining} attempt(s)** remaining.\n\n"
                f"💡 Type your username or link below:"
            )
            return STATE_ENTER_LINK  # Stay in input state for retry
    except Exception as e:
        # Validation error - show error and stay in input state
        logger.error(f"Exception during PayPal validation for user {user_id}, link: {formatted_link}", exc=e)
        flow_state.set(KEY_FORMATTED_LINK, formatted_link)
        
        # Get attempt count
        attempts = flow_state.get(KEY_VALIDATION_ATTEMPTS, 0) + 1
        flow_state.set(KEY_VALIDATION_ATTEMPTS, attempts)
        logger.debug(f"Validation attempt {attempts}/3 (exception) for user {user_id}")
        
        max_attempts = 3
        if attempts >= max_attempts:
            # Max attempts reached, exit
            logger.warning(f"Max validation attempts ({max_attempts}) reached after exception for user {user_id}")
            await api.message_manager.send_text(
                user_id,
                f"❌ **Maximum Attempts Reached**\n\n"
                f"PayPal setup failed after {max_attempts} attempts.\n"
                f"Validation error: {str(e)}\n\n"
                f"Please try again later with /paypal or contact support.",
                vanish=True,
                conv=True,
                delete_after=10
            )
            return None  # Exit flow
        
        # Show error by updating the message and stay in input state
        remaining = max_attempts - attempts
        flow_state.set(KEY_VALIDATION_ERROR_TEXT,
            f"❌ **Validation Error** (Attempt {attempts}/{max_attempts})\n\n"
            f"Error validating link: {str(e)}\n\n"
            f"You have **{remaining} attempt(s)** remaining.\n\n"
            f"💡 Type your username or link below:"
        )
        return STATE_ENTER_LINK  # Stay in input state for retry


# ============================================================================
# CONFIRM SAVE STATE
# ============================================================================

async def build_confirm_save_text(flow_state, api, user_id) -> str:
    """Build confirmation text for saving PayPal link."""
    formatted_link = flow_state.get(KEY_FORMATTED_LINK, 'Unknown')
    
    return (
        f"✅ **PayPal Link Validated**\n\n"
        f"Your PayPal link has been validated:\n"
        f"{formatted_link}\n\n"
        f"Do you want to save this link?"
    )


async def handle_confirm_save_button(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle confirmation button press."""
    if data == CommonCallbacks.CONFIRM:
        # Save the link
        formatted_link = flow_state.get(KEY_FORMATTED_LINK)
        user = await api.conversation_manager.repo.find_user_by_id(user_id)
        user.paypal_link = formatted_link
        await user.save()
        
        return STATE_EXIT_SAVE_SUCCESS
    elif data == CommonCallbacks.CANCEL:
        return CommonStateIds.EXIT_CANCELLED
    return None


# ============================================================================
# EXIT STATE TEXT BUILDERS (for dynamic content)
# ============================================================================

async def build_save_success_text(flow_state, api, user_id) -> str:
    """Build save success text with link info."""
    logger = Logger("PayPalFlow")
    
    formatted_link = flow_state.get(KEY_FORMATTED_LINK, 'Unknown')
    logger.info(f"PayPal link saved for user {user_id}: {formatted_link}")
    
    return (
        f"✅ **PayPal Link Saved**\n\n"
        f"Your PayPal link has been saved successfully:\n"
        f"{formatted_link}\n\n"
        f"You can now receive payments via PayPal!"
    )


async def build_remove_success_text(flow_state, api, user_id) -> str:
    """Build removal success text with link info."""
    logger = Logger("PayPalFlow")
    
    old_link = flow_state.get(KEY_OLD_PAYPAL_LINK, 'Unknown')
    logger.info(f"PayPal link removed for user {user_id}: {old_link}")
    
    return (
        f"✅ **PayPal Link Removed**\n\n"
        f"Your PayPal link has been removed successfully.\n\n"
        f"You can add a new link anytime using the /paypal command."
    )


# ============================================================================
# CONFIRM REMOVE STATE
# ============================================================================

async def build_confirm_remove_text(flow_state, api, user_id) -> str:
    """Build confirmation text for removing PayPal link."""
    user = await api.conversation_manager.repo.find_user_by_id(user_id)
    
    return (
        f"⚠️ **Remove PayPal Link?**\n\n"
        f"Are you sure you want to remove your PayPal link?\n"
        f"Current link: `{user.paypal_link}`\n\n"
        f"This action cannot be undone, but you can add a new link anytime."
    )


async def handle_confirm_remove_button(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle remove confirmation button press."""
    if data == CommonCallbacks.CONFIRM:
        # Remove the link
        user = await api.conversation_manager.repo.find_user_by_id(user_id)
        old_link = user.paypal_link
        flow_state.set(KEY_OLD_PAYPAL_LINK, old_link)
        user.paypal_link = None
        await user.save()
        
        return STATE_EXIT_REMOVE_SUCCESS
    elif data == CommonCallbacks.CANCEL:
        return STATE_EXIT_REMOVAL_CANCELLED
    return None


# ============================================================================
# FLOW DEFINITION
# ============================================================================

def create_paypal_flow(
    *,
    invoked_from_card_creation: bool = False,
    exit_message_delete_after_seconds: int = 0,
) -> MessageFlow:
    """Create the PayPal setup message flow."""
    flow = MessageFlow()

    exit_delete_after = int(exit_message_delete_after_seconds) if invoked_from_card_creation else 0

    async def build_main_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        user = await api.conversation_manager.repo.find_user_by_id(user_id)

        nav_row = (
            NavigationButtons.cancel(text="❌ Cancel")
            if invoked_from_card_creation
            else NavigationButtons.close()
        )

        if user.paypal_link:
            return [
                [ButtonCallback("🔄 Change Link", CB_CHANGE), ButtonCallback("❌ Remove Link", CB_REMOVE)],
                nav_row,
            ]

        return [
            [ButtonCallback("➕ Add PayPal Link", CB_ADD)],
            nav_row,
        ]

    enter_link_defaults = {
        KEY_VALIDATION_ATTEMPTS: 0,
        KEY_VALIDATION_ERROR_TEXT: None,
        KEY_FORMATTED_LINK: None,
        KEY_ORIGINAL_INPUT: None,
    }
    
    # Main menu
    flow.add_state(make_state(
        STATE_MAIN,
        text_builder=build_paypal_main_text,
        keyboard_builder=build_main_keyboard,
        action=MessageAction.AUTO,
        timeout=120,
        exit_buttons=[],  # No default exit buttons - handler manages navigation
        on_button_press=handle_paypal_main_button,
    ))
    
    # Enter link state (text input with cancel button)
    async def handle_enter_link_cancel(data: str, flow_state, api, user_id) -> Optional[str]:
        """Handle cancel button in enter_link state."""
        if data == CommonCallbacks.CANCEL:
            return CommonStateIds.EXIT_CANCELLED
        return None
    
    flow.add_state(MessageDefinition(
        state_id=STATE_ENTER_LINK,
        state_type=StateType.MIXED,  # Allow both text input and button press
        text_builder=build_enter_link_text,
        buttons=[
            [ButtonCallback("❌ Cancel", CommonCallbacks.CANCEL)]
        ],
        action=MessageAction.EDIT,
        defaults=enter_link_defaults,
        input_validator=TextLengthValidator(min_length=3, max_length=200),
        input_timeout=120,
        input_storage_key=KEY_PAYPAL_INPUT,
        exit_buttons=[],  # No default exit buttons - handler manages navigation
        on_input_received=handle_paypal_input,
        on_button_press=handle_enter_link_cancel,
    ))
    
    # Confirm save state
    flow.add_state(make_state(
        STATE_CONFIRM_SAVE,
        text_builder=build_confirm_save_text,
        buttons=[
            [ButtonCallback("✅ Save Link", CommonCallbacks.CONFIRM)],
            [ButtonCallback("❌ Cancel", CommonCallbacks.CANCEL)]
        ],
        action=MessageAction.EDIT,
        timeout=60,
        exit_buttons=[],  # No default exit buttons - handler manages navigation
        on_button_press=handle_confirm_save_button,
    ))
    
    # Confirm remove state
    flow.add_state(make_state(
        STATE_CONFIRM_REMOVE,
        text_builder=build_confirm_remove_text,
        buttons=[
            [ButtonCallback("✅ Yes, Remove", CommonCallbacks.CONFIRM)],
            [ButtonCallback("❌ Cancel", CommonCallbacks.CANCEL)]
        ],
        action=MessageAction.EDIT,
        timeout=60,
        exit_buttons=[],  # No default exit buttons - handler manages navigation
        on_button_press=handle_confirm_remove_button,
    ))
    
    # Exit states - universal ExitStateBuilder
    cancel_message = "❌ **PayPal Link Setup Cancelled**\n\nNo changes were made."

    exit_cancelled = ExitStateBuilder.create_cancelled(
        state_id=CommonStateIds.EXIT_CANCELLED,
        message=cancel_message,
        delete_after=exit_delete_after,
    )
    flow.add_state(exit_cancelled)

    exit_removal_cancelled = ExitStateBuilder.create_cancelled(
        state_id=STATE_EXIT_REMOVAL_CANCELLED,
        message="❌ **Removal Cancelled**\n\nYour PayPal link was not removed.",
        delete_after=exit_delete_after,
    )
    flow.add_state(exit_removal_cancelled)

    exit_save_success = ExitStateBuilder.create(
        state_id=STATE_EXIT_SAVE_SUCCESS,
        text_builder=build_save_success_text,
        delete_after=exit_delete_after,
    )
    flow.add_state(exit_save_success)

    exit_remove_success = ExitStateBuilder.create(
        state_id=STATE_EXIT_REMOVE_SUCCESS,
        text_builder=build_remove_success_text,
        delete_after=exit_delete_after,
    )
    flow.add_state(exit_remove_success)
    
    return flow
