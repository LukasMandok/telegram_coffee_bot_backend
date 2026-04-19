from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional

from ..message_flow import ButtonCallback, MessageDefinition, MessageFlow, StateType, TextLengthValidator
from ..message_flow_helpers import CommonCallbacks, NavigationButtons, format_date
from ...models.beanie_models import TelegramUser
from ...models.feedback_models import Feedback, FeedbackComment, FeedbackStatus, FeedbackType


STATE_MAIN = "main"

STATE_CREATE_TYPE = "create_type"
STATE_CREATE_TITLE = "create_title"
STATE_CREATE_DESCRIPTION = "create_description"
STATE_CREATE_PRIORITY = "create_priority"

STATE_LIST_MY = "list_my"
STATE_LIST_ADMIN_BUGS = "list_admin_bugs"
STATE_LIST_ADMIN_FEATURES = "list_admin_features"
STATE_LIST_ADMIN_GENERAL = "list_admin_general"
STATE_DETAILS = "details"

STATE_ADD_COMMENT = "add_comment"

STATE_USER_EDIT_MENU = "user_edit_menu"
STATE_USER_EDIT_TITLE = "user_edit_title"
STATE_USER_EDIT_DESCRIPTION = "user_edit_description"
STATE_USER_EDIT_PRIORITY = "user_edit_priority"

STATE_ADMIN_CHANGE_STATUS = "admin_change_status"
STATE_ADMIN_CHANGE_PRIORITY = "admin_change_priority"

STATE_DELETE_CONFIRM = "delete_confirm"


CB_CREATE = "create"
CB_VIEW_MY = "view_my"
CB_VIEW_BUGS = "view_bugs"
CB_VIEW_FEATURES = "view_features"
CB_VIEW_GENERAL = "view_general"

CB_DETAILS_EDIT = "details_edit"
CB_DETAILS_DELETE = "details_delete"
CB_DETAILS_COMMENT = "details_comment"

CB_ADMIN_STATUS = "admin_status"
CB_ADMIN_PRIORITY = "admin_priority"

CB_EDIT_TITLE = "edit_title"
CB_EDIT_DESCRIPTION = "edit_description"
CB_EDIT_PRIORITY = "edit_priority"


KEY_CREATE_TYPE = "feedback_create_type"
KEY_CREATE_TITLE = "feedback_create_title"
KEY_CREATE_DESCRIPTION = "feedback_create_description"
KEY_CREATE_PRIORITY = "feedback_create_priority"

KEY_SELECTED_FEEDBACK_ID = "feedback_selected_id"
KEY_RETURN_STATE = "feedback_return_state"

KEY_LIST_PAGE_MY = "feedback_list_page_my"
KEY_LIST_PAGE_ADMIN_BUGS = "feedback_list_page_admin_bugs"
KEY_LIST_PAGE_ADMIN_FEATURES = "feedback_list_page_admin_features"
KEY_LIST_PAGE_ADMIN_GENERAL = "feedback_list_page_admin_general"

KEY_DETAILS_COMMENTS_PAGE = "feedback_details_comments_page"
KEY_ADD_COMMENT_COMMENTS_PAGE = "feedback_add_comment_comments_page"

KEY_FILTER_OPEN = "feedback_filter_open"
KEY_FILTER_IN_PROGRESS = "feedback_filter_in_progress"
KEY_FILTER_COMPLETED = "feedback_filter_completed"
KEY_FILTER_REJECTED = "feedback_filter_rejected"
KEY_FILTER_ARCHIVED = "feedback_filter_archived"

CB_LIST_PAGE_PREV = "list_page_prev"
CB_LIST_PAGE_NEXT = "list_page_next"
CB_LIST_PAGE_INFO = "list_page_info"

CB_FILTER_OPEN = "filter_open"
CB_FILTER_IN_PROGRESS = "filter_in_progress"
CB_FILTER_COMPLETED = "filter_completed"
CB_FILTER_REJECTED = "filter_rejected"
CB_FILTER_ARCHIVED = "filter_archived"

CB_DELETE_YES = "delete_yes"

CB_ABORT_CREATE = "abort_create"

CB_COMMENTS_NEWER = "comments_newer"
CB_COMMENTS_OLDER = "comments_older"
CB_COMMENTS_INFO = "comments_info"


@dataclass(frozen=True)
class FeedbackListItem:
    feedback_id: str
    title: str
    status: FeedbackStatus
    feedback_type: FeedbackType
    needs_attention: bool


def _now() -> datetime:
    return datetime.now()


async def _is_admin(api: Any, user_id: int) -> bool:
    return await api.conversation_manager.repo.is_user_admin(int(user_id))


async def _get_telegram_user_doc(api: Any, user_id: int):
    return await api.conversation_manager.repo.find_user_by_id(int(user_id))


async def _get_feedback(api: Any, feedback_id: str) -> Optional[Feedback]:
    if not feedback_id:
        return None
    try:
        return await Feedback.get(feedback_id)
    except Exception:
        return None


async def _get_feedback_submitter(feedback: Feedback) -> Optional[TelegramUser]:
    if isinstance(feedback.submitter, TelegramUser):
        return feedback.submitter

    try:
        await feedback.fetch_link("submitter")
    except Exception:
        return None

    if isinstance(feedback.submitter, TelegramUser):
        return feedback.submitter
    return None


async def _can_view_feedback(api: Any, user_id: int, feedback: Feedback) -> bool:
    if await _is_admin(api, user_id):
        return True

    submitter = await _get_feedback_submitter(feedback)
    if submitter is None:
        return False

    return bool(submitter.user_id == int(user_id))


async def _can_delete_feedback(api: Any, user_id: int, feedback: Feedback) -> bool:
    if await _is_admin(api, user_id):
        return True
    return await _can_view_feedback(api, user_id, feedback)


async def _notify_no_permission(api: Any, user_id: int) -> None:
    await api.message_manager.send_text(
        int(user_id),
        "❌ You don't have permission to do that.",
        vanish=True,
        conv=True,
        delete_after=3,
    )


def _format_priority(value: Any) -> str:
    if value is None:
        return "-"
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return str(value)
    return "-" if ivalue <= 0 else str(ivalue)


def _format_feedback_status(value: Any) -> str:
    if isinstance(value, FeedbackStatus):
        if value == FeedbackStatus.MODIFIED:
            return FeedbackStatus.OPEN.value
        if value == FeedbackStatus.POSTPONED:
            return "archived"
        return value.value
    return str(value)


def _format_feedback_type(value: Any) -> str:
    if isinstance(value, FeedbackType):
        return value.value
    return str(value)


def _get_bool(flow_state, key: str, default: bool) -> bool:
    raw = flow_state.get(key, default)
    return bool(raw) if isinstance(raw, bool) else bool(raw)


def _get_enabled_status_values(flow_state) -> List[str]:
    active_open = _get_bool(flow_state, KEY_FILTER_OPEN, False)
    active_in_progress = _get_bool(flow_state, KEY_FILTER_IN_PROGRESS, False)
    active_completed = _get_bool(flow_state, KEY_FILTER_COMPLETED, False)
    active_rejected = _get_bool(flow_state, KEY_FILTER_REJECTED, False)
    active_archived = _get_bool(flow_state, KEY_FILTER_ARCHIVED, False)

    has_any_active = bool(
        active_open or active_in_progress or active_completed or active_rejected or active_archived
    )

    statuses: List[str] = []

    # Default: show everything.
    if not has_any_active:
        return [
            FeedbackStatus.OPEN.value,
            FeedbackStatus.MODIFIED.value,
            FeedbackStatus.IN_PROGRESS.value,
            FeedbackStatus.COMPLETED.value,
            FeedbackStatus.REJECTED.value,
            FeedbackStatus.POSTPONED.value,
        ]

    # When any filter is active: show only the activated ones (combined).
    if active_open:
        statuses.append(FeedbackStatus.OPEN.value)
        statuses.append(FeedbackStatus.MODIFIED.value)
    if active_in_progress:
        statuses.append(FeedbackStatus.IN_PROGRESS.value)
    if active_completed:
        statuses.append(FeedbackStatus.COMPLETED.value)
    if active_rejected:
        statuses.append(FeedbackStatus.REJECTED.value)
    if active_archived:
        statuses.append(FeedbackStatus.POSTPONED.value)
    return statuses


def _normalize_status(status: FeedbackStatus) -> FeedbackStatus:
    return FeedbackStatus.OPEN if status == FeedbackStatus.MODIFIED else status


def _type_icon(value: FeedbackType) -> str:
    if value == FeedbackType.BUG:
        return "🪲"
    if value == FeedbackType.FEATURE_REQUEST:
        return "✨"
    return "💬"


def _status_icon(value: FeedbackStatus) -> str:
    status = _normalize_status(value)
    if status == FeedbackStatus.OPEN:
        return "🆕"
    if status == FeedbackStatus.IN_PROGRESS:
        return "⏳"
    if status == FeedbackStatus.COMPLETED:
        return "✅"
    if status == FeedbackStatus.REJECTED:
        return "❌"
    if status == FeedbackStatus.POSTPONED:
        return "🚫"
    return "❔"


def _is_closed_or_archived(status: FeedbackStatus) -> bool:
    normalized = _normalize_status(status)
    return normalized in (FeedbackStatus.COMPLETED, FeedbackStatus.REJECTED, FeedbackStatus.POSTPONED)


def _needs_attention_for_side(feedback: Feedback, *, side: str) -> bool:
    if side == "admin":
        if not feedback.viewed_by_admin:
            return True
        if feedback.title_updated or feedback.description_updated:
            return True

    for comment in feedback.comments:
        if side == "submitter" and comment.author_is_admin and not comment.viewed:
            return True
        if side == "admin" and (not comment.author_is_admin) and not comment.viewed:
            return True
    return False


def _render_comments_for_viewer(feedback: Feedback, *, viewer_side: str, limit: int = 10) -> str:
    # Keep legacy behavior: just show the newest comments within a generous line budget.
    # This helper is no longer used for paging UI.
    text, _, _ = _render_comments_page(
        feedback,
        viewer_side=viewer_side,
        page=1,
        line_limit=max(25, int(limit) * 3),
    )
    return text


def _render_comments_page(
    feedback: Feedback,
    *,
    viewer_side: str,
    page: int,
    line_limit: int,
) -> tuple[str, int, int]:
    def _estimate_wrapped_lines(text: str, wrap_width: int = 60) -> int:
        if not text:
            return 1
        total_lines = 0
        for raw_line in text.splitlines() or [""]:
            length = len(raw_line)
            total_lines += 1 if length <= 0 else ((length - 1) // wrap_width + 1)
        return max(1, total_lines)

    def _comment_line_count(comment: FeedbackComment) -> int:
        # One header line + wrapped message lines.
        return 1 + _estimate_wrapped_lines(comment.message)

    comments = sorted(list(feedback.comments), key=lambda c: c.created_at)
    if not comments:
        return "-", 1, 1

    pages: List[List[FeedbackComment]] = []
    current: List[FeedbackComment] = []
    current_lines = 0

    for comment in reversed(comments):  # newest -> oldest
        needed = _comment_line_count(comment)
        if current and (current_lines + needed) > int(line_limit):
            pages.append(list(reversed(current)))  # show oldest->newest inside page
            current = []
            current_lines = 0

        current.append(comment)
        current_lines += needed

    if current:
        pages.append(list(reversed(current)))

    total_pages = max(1, len(pages))
    current_page = max(1, min(int(page), total_pages))
    page_comments = pages[current_page - 1]

    rendered: List[str] = []
    for comment in page_comments:
        unread = False
        if viewer_side == "submitter" and comment.author_is_admin and not comment.viewed:
            unread = True
        if viewer_side == "admin" and (not comment.author_is_admin) and not comment.viewed:
            unread = True

        prefix = "🟡" if unread else "•"
        rendered.append(
            f"{prefix} **{comment.author_display_name} - {format_date(comment.created_at)}**:\n{comment.message}"
        )

    return "\n".join(rendered), total_pages, current_page


async def _submitter_has_unread_admin_comment(api: Any, user_id: int) -> bool:
    telegram_user = await _get_telegram_user_doc(api, int(user_id))
    if telegram_user is None or telegram_user.id is None:
        return False

    query = {
        "submitter.$id": telegram_user.id,
        "comments": {"$elemMatch": {"author_is_admin": True, "viewed": False}},
    }
    return bool(await Feedback.find(query, fetch_links=False).count() > 0)


async def _admin_has_attention_for_type(api: Any, feedback_type: FeedbackType) -> bool:
    query = {
        "type": feedback_type.value,
        "$or": [
            {"viewed_by_admin": False},
            {"title_updated": True},
            {"description_updated": True},
            {"comments": {"$elemMatch": {"author_is_admin": False, "viewed": False}}},
        ],
    }
    return bool(await Feedback.find(query, fetch_links=False).count() > 0)


async def build_main_text(flow_state, api: Any, user_id: int) -> str:
    telegram_user = await _get_telegram_user_doc(api, int(user_id))
    submitted = 0
    if telegram_user is not None and telegram_user.id is not None:
        submitted = await Feedback.find({"submitter.$id": telegram_user.id}, fetch_links=False).count()
        
    return (
        "🗣️ **Feedback**\n\n"
        f"Submitted feedback: **{submitted}**"
    )


async def select_create(flow_state, api: Any, user_id: int) -> Optional[str]:
    flow_state.set(KEY_RETURN_STATE, STATE_MAIN)
    flow_state.clear(KEY_CREATE_TYPE, KEY_CREATE_TITLE, KEY_CREATE_DESCRIPTION, KEY_CREATE_PRIORITY)
    return STATE_CREATE_TYPE


async def abort_create(flow_state, api: Any, user_id: int) -> Optional[str]:
    flow_state.clear(KEY_CREATE_TYPE, KEY_CREATE_TITLE, KEY_CREATE_DESCRIPTION, KEY_CREATE_PRIORITY)
    return STATE_MAIN


async def open_my_list(flow_state, api: Any, user_id: int) -> Optional[str]:
    flow_state.set(KEY_RETURN_STATE, STATE_MAIN)
    flow_state.set(KEY_LIST_PAGE_MY, 1)
    return STATE_LIST_MY


async def open_admin_bugs_list(flow_state, api: Any, user_id: int) -> Optional[str]:
    flow_state.set(KEY_RETURN_STATE, STATE_MAIN)
    flow_state.set(KEY_LIST_PAGE_ADMIN_BUGS, 1)
    return STATE_LIST_ADMIN_BUGS


async def open_admin_features_list(flow_state, api: Any, user_id: int) -> Optional[str]:
    flow_state.set(KEY_RETURN_STATE, STATE_MAIN)
    flow_state.set(KEY_LIST_PAGE_ADMIN_FEATURES, 1)
    return STATE_LIST_ADMIN_FEATURES


async def open_admin_general_list(flow_state, api: Any, user_id: int) -> Optional[str]:
    flow_state.set(KEY_RETURN_STATE, STATE_MAIN)
    flow_state.set(KEY_LIST_PAGE_ADMIN_GENERAL, 1)
    return STATE_LIST_ADMIN_GENERAL


async def build_create_type_text(flow_state, api: Any, user_id: int) -> str:
    return "Select feedback type:"  # keyboard provides choices


async def build_create_type_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    def _set_type(value: FeedbackType):
        async def _handler(inner_flow_state, inner_api: Any, inner_user_id: int) -> Optional[str]:
            inner_flow_state.set(KEY_CREATE_TYPE, value.value)
            return STATE_CREATE_TITLE

        return _handler

    return [
        [ButtonCallback("bug", "type_bug", callback_handler=_set_type(FeedbackType.BUG))],
        [
            ButtonCallback(
                "feature request",
                "type_feature_request",
                callback_handler=_set_type(FeedbackType.FEATURE_REQUEST),
            )
        ],
        [ButtonCallback("general", "type_general", callback_handler=_set_type(FeedbackType.GENERAL))],
        [ButtonCallback("◁ Back", CB_ABORT_CREATE, callback_handler=abort_create)],
    ]


async def handle_create_title_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    flow_state.set(KEY_CREATE_TITLE, (input_text or "").strip())
    return STATE_CREATE_DESCRIPTION


async def handle_create_description_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    flow_state.set(KEY_CREATE_DESCRIPTION, (input_text or "").strip())

    raw_type = str(flow_state.get(KEY_CREATE_TYPE, "")).strip()
    if raw_type == FeedbackType.GENERAL.value:
        flow_state.set(KEY_CREATE_PRIORITY, None)
        return await save_new_feedback(flow_state, api, int(user_id))

    return STATE_CREATE_PRIORITY


async def build_priority_keyboard(
    *,
    on_selected,
) -> List[List[ButtonCallback]]:
    def _make(priority: int):
        async def _handler(flow_state, api: Any, user_id: int) -> Optional[str]:
            return await on_selected(int(priority), flow_state, api, int(user_id))

        return _handler

    return [
        [
            ButtonCallback("1", "p1", callback_handler=_make(1)),
            ButtonCallback("2", "p2", callback_handler=_make(2)),
            ButtonCallback("3", "p3", callback_handler=_make(3)),
            ButtonCallback("4", "p4", callback_handler=_make(4)),
            ButtonCallback("5", "p5", callback_handler=_make(5)),
        ],
        [ButtonCallback("◁ Back", CommonCallbacks.BACK)],
    ]


async def build_create_priority_text(flow_state, api: Any, user_id: int) -> str:
    return "Select priority (1-5, where 5 is high priority):"


async def save_new_feedback(flow_state, api: Any, user_id: int) -> Optional[str]:
    raw_type = str(flow_state.get(KEY_CREATE_TYPE, "")).strip()
    raw_title = str(flow_state.get(KEY_CREATE_TITLE, "")).strip()
    raw_description = str(flow_state.get(KEY_CREATE_DESCRIPTION, "")).strip()

    feedback_type = FeedbackType(raw_type) if raw_type else None
    if feedback_type is None:
        await api.message_manager.send_text(
            int(user_id),
            "❌ Missing feedback type.",
            vanish=True,
            conv=True,
            delete_after=3,
        )
        return STATE_CREATE_TYPE

    telegram_user = await _get_telegram_user_doc(api, int(user_id))
    if telegram_user is None:
        await api.message_manager.send_text(
            int(user_id),
            "❌ Could not find your user in the database.",
            vanish=True,
            conv=True,
            delete_after=3,
        )
        return STATE_MAIN

    priority: Optional[int]
    if feedback_type == FeedbackType.GENERAL:
        priority = None
    else:
        try:
            raw_priority = flow_state.get(KEY_CREATE_PRIORITY)
            priority = int(raw_priority) if raw_priority is not None else None
        except (TypeError, ValueError):
            priority = None

    created = _now()
    feedback = Feedback(
        title=raw_title,
        description=raw_description,
        type=feedback_type,
        priority=priority,
        status=FeedbackStatus.OPEN,
        submitter=telegram_user,  # type: ignore[arg-type]
        viewed_by_admin=False,
        created_at=created,
        updated_at=created,
    )

    await feedback.insert()

    flow_state.set(KEY_SELECTED_FEEDBACK_ID, str(feedback.id))
    flow_state.set(KEY_RETURN_STATE, STATE_MAIN)

    return STATE_DETAILS


async def handle_create_priority_selected(priority: int, flow_state, api: Any, user_id: int) -> Optional[str]:
    flow_state.set(KEY_CREATE_PRIORITY, int(priority))
    return await save_new_feedback(flow_state, api, int(user_id))


async def list_my_feedback(flow_state, api: Any, user_id: int) -> List[FeedbackListItem]:
    telegram_user = await _get_telegram_user_doc(api, int(user_id))
    if telegram_user is None or telegram_user.id is None:
        return []

    statuses = _get_enabled_status_values(flow_state)
    if not statuses:
        return []

    docs = await Feedback.find(
        {"submitter.$id": telegram_user.id, "status": {"$in": statuses}},
        fetch_links=False,
    ).sort("-updated_at").to_list()

    docs.sort(key=lambda d: (1 if _is_closed_or_archived(d.status) else 0, -int(d.updated_at.timestamp())))

    return [
        FeedbackListItem(
            feedback_id=str(doc.id),
            title=doc.title,
            status=_normalize_status(doc.status),
            feedback_type=doc.type,
            needs_attention=_needs_attention_for_side(doc, side="submitter"),
        )
        for doc in docs
        if doc.id is not None
    ]


async def list_all_feedback(flow_state, api: Any, user_id: int, *, only_type: FeedbackType | None = None) -> List[FeedbackListItem]:
    if not await _is_admin(api, int(user_id)):
        return []

    statuses = _get_enabled_status_values(flow_state)
    if not statuses:
        return []

    query: dict[str, Any] = {"status": {"$in": statuses}}
    if only_type is not None:
        query["type"] = only_type.value

    docs = await Feedback.find(query, fetch_links=False).sort("-updated_at").to_list()
    docs.sort(key=lambda d: (1 if _is_closed_or_archived(d.status) else 0, -int(d.updated_at.timestamp())))
    return [
        FeedbackListItem(
            feedback_id=str(doc.id),
            title=doc.title,
            status=_normalize_status(doc.status),
            feedback_type=doc.type,
            needs_attention=_needs_attention_for_side(doc, side="admin"),
        )
        for doc in docs
        if doc.id is not None
    ]
def build_feedback_list_button(
    item: FeedbackListItem,
    idx: int,
    *,
    return_state: str,
    show_type_icon: bool,
) -> ButtonCallback:
    attention_prefix = "⚠️ " if item.needs_attention else ""
    status_icon = _status_icon(item.status)
    type_icon = f"{_type_icon(item.feedback_type)} " if show_type_icon else ""
    label = f"{attention_prefix}{type_icon}{item.title} {status_icon}"

    async def _select(flow_state, api: Any, user_id: int) -> Optional[str]:
        flow_state.set(KEY_SELECTED_FEEDBACK_ID, item.feedback_id)
        flow_state.set(KEY_RETURN_STATE, return_state)
        flow_state.set(KEY_DETAILS_COMMENTS_PAGE, 1)
        flow_state.set(KEY_ADD_COMMENT_COMMENTS_PAGE, 1)
        return STATE_DETAILS

    return ButtonCallback(label, f"select:{item.feedback_id}", callback_handler=_select)


async def build_list_my_text(flow_state, api: Any, user_id: int) -> str:
    return "📄 **My feedback**\n\nSelect an entry:" 


async def build_list_admin_bugs_text(flow_state, api: Any, user_id: int) -> str:
    if not await _is_admin(api, int(user_id)):
        return "❌ Admin only.\n\nGo back."
    return "**🪲 Bugs**\n\nSelect an entry:"


async def build_list_admin_features_text(flow_state, api: Any, user_id: int) -> str:
    if not await _is_admin(api, int(user_id)):
        return "❌ Admin only.\n\nGo back."
    return "**✨ Features**\n\nSelect an entry:"


async def build_list_admin_general_text(flow_state, api: Any, user_id: int) -> str:
    if not await _is_admin(api, int(user_id)):
        return "❌ Admin only.\n\nGo back."
    return "**💬 General feedback**\n\nSelect an entry:"


def _get_int(flow_state, key: str, default: int) -> int:
    raw = flow_state.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, value)


async def _toggle_filter(flow_state, *, key: str, page_key: str, state_id: str) -> Optional[str]:
    flow_state.set(key, not _get_bool(flow_state, key, False))
    flow_state.set(page_key, 1)
    return state_id


async def _change_page(flow_state, *, page_key: str, delta: int, max_page: int, state_id: str) -> Optional[str]:
    current = _get_int(flow_state, page_key, 1)
    next_page = max(1, min(max_page, current + int(delta)))
    flow_state.set(page_key, next_page)
    return state_id


async def _build_feedback_list_keyboard(
    *,
    flow_state,
    state_id: str,
    page_key: str,
    return_state: str,
    items: List[FeedbackListItem],
    show_type_icon: bool,
    page_size: int = 10,
) -> List[List[ButtonCallback]]:
    active_open = _get_bool(flow_state, KEY_FILTER_OPEN, False)
    active_in_progress = _get_bool(flow_state, KEY_FILTER_IN_PROGRESS, False)
    active_completed = _get_bool(flow_state, KEY_FILTER_COMPLETED, False)
    active_rejected = _get_bool(flow_state, KEY_FILTER_REJECTED, False)
    active_archived = _get_bool(flow_state, KEY_FILTER_ARCHIVED, False)

    total_items = len(items)
    total_pages = max(1, (total_items + page_size - 1) // page_size) if total_items else 1
    current_page = min(_get_int(flow_state, page_key, 1), total_pages)
    flow_state.set(page_key, current_page)

    start = (current_page - 1) * page_size
    end = min(start + page_size, total_items)
    page_items = items[start:end]

    rows: List[List[ButtonCallback]] = []

    # Filters
    def _label(icon: str, active: bool) -> str:
        return f"[{icon}]" if active else icon

    rows.append(
        [
            ButtonCallback(
                _label(_status_icon(FeedbackStatus.OPEN), active_open),
                CB_FILTER_OPEN,
                callback_handler=lambda fs, api, uid: _toggle_filter(
                    fs,
                    key=KEY_FILTER_OPEN,
                    page_key=page_key,
                    state_id=state_id,
                ),
            ),
            ButtonCallback(
                _label(_status_icon(FeedbackStatus.IN_PROGRESS), active_in_progress),
                CB_FILTER_IN_PROGRESS,
                callback_handler=lambda fs, api, uid: _toggle_filter(
                    fs,
                    key=KEY_FILTER_IN_PROGRESS,
                    page_key=page_key,
                    state_id=state_id,
                ),
            ),
            ButtonCallback(
                _label(_status_icon(FeedbackStatus.COMPLETED), active_completed),
                CB_FILTER_COMPLETED,
                callback_handler=lambda fs, api, uid: _toggle_filter(
                    fs,
                    key=KEY_FILTER_COMPLETED,
                    page_key=page_key,
                    state_id=state_id,
                ),
            ),
            ButtonCallback(
                _label(_status_icon(FeedbackStatus.REJECTED), active_rejected),
                CB_FILTER_REJECTED,
                callback_handler=lambda fs, api, uid: _toggle_filter(
                    fs,
                    key=KEY_FILTER_REJECTED,
                    page_key=page_key,
                    state_id=state_id,
                ),
            ),
            ButtonCallback(
                _label(_status_icon(FeedbackStatus.POSTPONED), active_archived),
                CB_FILTER_ARCHIVED,
                callback_handler=lambda fs, api, uid: _toggle_filter(
                    fs,
                    key=KEY_FILTER_ARCHIVED,
                    page_key=page_key,
                    state_id=state_id,
                ),
            ),
        ]
    )

    # Items (single column)
    for idx, item in enumerate(page_items, start=start):
        rows.append(
            [
                build_feedback_list_button(
                    item,
                    idx,
                    return_state=return_state,
                    show_type_icon=show_type_icon,
                )
            ]
        )

    # Pagination nav
    nav_row: List[ButtonCallback] = []
    if current_page > 1:
        nav_row.append(
            ButtonCallback(
                "◀️ Prev",
                CB_LIST_PAGE_PREV,
                callback_handler=lambda fs, api, uid: _change_page(
                    fs,
                    page_key=page_key,
                    delta=-1,
                    max_page=total_pages,
                    state_id=state_id,
                ),
            )
        )

    if total_pages > 1:
        nav_row.append(ButtonCallback(f"Page {current_page}/{total_pages}", CB_LIST_PAGE_INFO))

    if current_page < total_pages:
        nav_row.append(
            ButtonCallback(
                "Next ▶️",
                CB_LIST_PAGE_NEXT,
                callback_handler=lambda fs, api, uid: _change_page(
                    fs,
                    page_key=page_key,
                    delta=1,
                    max_page=total_pages,
                    state_id=state_id,
                ),
            )
        )

    if nav_row:
        rows.append(nav_row)

    async def _go_back_to_main(inner_flow_state, inner_api: Any, inner_user_id: int) -> Optional[str]:
        return STATE_MAIN

    rows.append([ButtonCallback("◁ Back", CommonCallbacks.BACK, callback_handler=_go_back_to_main)])
    return rows


async def build_list_my_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    items = await list_my_feedback(flow_state, api, int(user_id))
    return await _build_feedback_list_keyboard(
        flow_state=flow_state,
        state_id=STATE_LIST_MY,
        page_key=KEY_LIST_PAGE_MY,
        return_state=STATE_LIST_MY,
        items=items,
        show_type_icon=True,
    )


async def build_list_admin_bugs_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    items = await list_all_feedback(flow_state, api, int(user_id), only_type=FeedbackType.BUG)
    return await _build_feedback_list_keyboard(
        flow_state=flow_state,
        state_id=STATE_LIST_ADMIN_BUGS,
        page_key=KEY_LIST_PAGE_ADMIN_BUGS,
        return_state=STATE_LIST_ADMIN_BUGS,
        items=items,
        show_type_icon=False,
    )


async def build_list_admin_features_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    items = await list_all_feedback(flow_state, api, int(user_id), only_type=FeedbackType.FEATURE_REQUEST)
    return await _build_feedback_list_keyboard(
        flow_state=flow_state,
        state_id=STATE_LIST_ADMIN_FEATURES,
        page_key=KEY_LIST_PAGE_ADMIN_FEATURES,
        return_state=STATE_LIST_ADMIN_FEATURES,
        items=items,
        show_type_icon=False,
    )


async def build_list_admin_general_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    items = await list_all_feedback(flow_state, api, int(user_id), only_type=FeedbackType.GENERAL)
    return await _build_feedback_list_keyboard(
        flow_state=flow_state,
        state_id=STATE_LIST_ADMIN_GENERAL,
        page_key=KEY_LIST_PAGE_ADMIN_GENERAL,
        return_state=STATE_LIST_ADMIN_GENERAL,
        items=items,
        show_type_icon=False,
    )


async def build_details_text(flow_state, api: Any, user_id: int) -> str:
    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    if feedback is None:
        return "❌ Feedback not found."

    can_view = await _can_view_feedback(api, int(user_id), feedback)
    if not can_view:
        return "❌ You don't have permission to view this feedback."

    submitter = await _get_feedback_submitter(feedback)
    submitter_text = "-" if submitter is None else f"{submitter.display_name}"

    viewer_is_admin = await _is_admin(api, int(user_id))
    viewer_is_submitter = submitter is not None and submitter.user_id == int(user_id)
    viewer_side = "admin" if viewer_is_admin and not viewer_is_submitter else "submitter"

    title_prefix = "🟡 " if (viewer_side == "admin" and feedback.title_updated) else ""
    desc_prefix = "🟡 " if (viewer_side == "admin" and feedback.description_updated) else ""

    comments_page = _get_int(flow_state, KEY_DETAILS_COMMENTS_PAGE, 1)
    comments_text, total_pages, current_page = _render_comments_page(
        feedback,
        viewer_side=viewer_side,
        page=comments_page,
        line_limit=25,
    )
    flow_state.set(KEY_DETAILS_COMMENTS_PAGE, current_page)

    comments_header = "Comments:"
    if total_pages > 1:
        comments_header = f"Comments (page {current_page}/{total_pages}):"

    created_at = format_date(feedback.created_at)
    updated_at = format_date(feedback.updated_at)

    return (
        f"🗣️ **Feedback**\n\n"
        f"Title: {title_prefix}**{feedback.title}**\n"
        f"Type: `{_format_feedback_type(feedback.type)}`\n"
        f"Priority: `{_format_priority(feedback.priority)}`\n"
        f"Status: `{_format_feedback_status(feedback.status)}`\n\n"
        f"Description: {desc_prefix}\n{feedback.description}\n\n"
        f"{comments_header}\n{comments_text}\n\n"
        f"Submitter: {submitter_text}\n"
        f"Created at: {created_at}\n"
        f"Updated at: {updated_at}"
    )


async def mark_comments_viewed_on_open(flow_state, api: Any, user_id: int) -> None:
    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    if feedback is None:
        return

    if not await _can_view_feedback(api, int(user_id), feedback):
        return

    submitter = await _get_feedback_submitter(feedback)
    if submitter is None:
        return

    viewer_id = int(user_id)
    viewer_is_submitter = submitter.user_id == viewer_id
    viewer_is_admin = await _is_admin(api, viewer_id)

    changed = False
    if viewer_is_admin:
        if not feedback.viewed_by_admin:
            feedback.viewed_by_admin = True
            changed = True

        if feedback.title_updated:
            feedback.title_updated = False
            changed = True
        if feedback.description_updated:
            feedback.description_updated = False
            changed = True

    for comment in feedback.comments:
        if viewer_is_submitter and comment.author_is_admin and not comment.viewed:
            comment.viewed = True
            changed = True
        elif viewer_is_admin and (not comment.author_is_admin) and not comment.viewed:
            comment.viewed = True
            changed = True

    if changed:
        await feedback.save()


async def build_details_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    if feedback is None:
        return [[ButtonCallback("◁ Back", CommonCallbacks.BACK)]]

    can_view = await _can_view_feedback(api, int(user_id), feedback)
    if not can_view:
        return [[ButtonCallback("◁ Back", CommonCallbacks.BACK)]]

    submitter = await _get_feedback_submitter(feedback)
    viewer_is_submitter = submitter is not None and submitter.user_id == int(user_id)
    is_admin = await _is_admin(api, int(user_id))
    viewer_side = "admin" if is_admin and not viewer_is_submitter else "submitter"

    def _change_comments_page(key: str, delta: int) -> Any:
        async def _handler(inner_flow_state, inner_api: Any, inner_user_id: int) -> Optional[str]:
            current = _get_int(inner_flow_state, key, 1)
            inner_flow_state.set(key, max(1, current + int(delta)))
            return STATE_DETAILS

        return _handler

    comments_page = _get_int(flow_state, KEY_DETAILS_COMMENTS_PAGE, 1)
    _, total_comment_pages, current_comment_page = _render_comments_page(
        feedback,
        viewer_side=viewer_side,
        page=comments_page,
        line_limit=25,
    )
    flow_state.set(KEY_DETAILS_COMMENTS_PAGE, current_comment_page)

    comment_nav_row: List[ButtonCallback] = []
    if total_comment_pages > 1:
        if current_comment_page > 1:
            comment_nav_row.append(
                ButtonCallback(
                    "◀️ Newer",
                    CB_COMMENTS_NEWER,
                    callback_handler=_change_comments_page(KEY_DETAILS_COMMENTS_PAGE, -1),
                )
            )
        comment_nav_row.append(
            ButtonCallback(
                f"{current_comment_page}/{total_comment_pages}",
                CB_COMMENTS_INFO,
                callback_handler=_change_comments_page(KEY_DETAILS_COMMENTS_PAGE, 0),
            )
        )
        if current_comment_page < total_comment_pages:
            comment_nav_row.append(
                ButtonCallback(
                    "Older ▶️",
                    CB_COMMENTS_OLDER,
                    callback_handler=_change_comments_page(KEY_DETAILS_COMMENTS_PAGE, 1),
                )
            )

    if is_admin:
        add_comment_row = [ButtonCallback("💬 Add comment", CB_DETAILS_COMMENT), *comment_nav_row]
        buttons: List[List[ButtonCallback]] = [
            [
                ButtonCallback("🧭 Status", CB_ADMIN_STATUS),
                ButtonCallback("⭐ Priority", CB_ADMIN_PRIORITY),
                ButtonCallback("🗑 Delete", CB_DETAILS_DELETE),
            ],
            add_comment_row,
            [ButtonCallback("◁ Back", CommonCallbacks.BACK)],
        ]
        return [row for row in buttons if row]

    # normal user (submitter)
    buttons = [
        [ButtonCallback("💬 Add comment", CB_DETAILS_COMMENT), *comment_nav_row],
        [ButtonCallback("✏️ Edit", CB_DETAILS_EDIT), ButtonCallback("🗑 Delete", CB_DETAILS_DELETE)],
        [ButtonCallback("◁ Back", CommonCallbacks.BACK)],
    ]
    return [row for row in buttons if row]


async def handle_details_button(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    if data == CommonCallbacks.BACK:
        return str(flow_state.get(KEY_RETURN_STATE, STATE_MAIN))

    if data == CB_DETAILS_EDIT:
        return STATE_USER_EDIT_MENU

    if data == CB_DETAILS_DELETE:
        return STATE_DELETE_CONFIRM

    if data == CB_DETAILS_COMMENT:
        flow_state.set(KEY_ADD_COMMENT_COMMENTS_PAGE, 1)
        return STATE_ADD_COMMENT

    if data == CB_ADMIN_STATUS:
        return STATE_ADMIN_CHANGE_STATUS

    if data == CB_ADMIN_PRIORITY:
        return STATE_ADMIN_CHANGE_PRIORITY

    return None


async def build_add_comment_text(flow_state, api: Any, user_id: int) -> str:
    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    if feedback is None:
        return "💬 **Add comment**\n\nWrite your message:"

    submitter = await _get_feedback_submitter(feedback)
    viewer_is_admin = await _is_admin(api, int(user_id))
    viewer_is_submitter = submitter is not None and submitter.user_id == int(user_id)
    viewer_side = "admin" if viewer_is_admin and not viewer_is_submitter else "submitter"

    comments_page = _get_int(flow_state, KEY_ADD_COMMENT_COMMENTS_PAGE, 1)
    comments_text, total_pages, current_page = _render_comments_page(
        feedback,
        viewer_side=viewer_side,
        page=comments_page,
        line_limit=25,
    )
    flow_state.set(KEY_ADD_COMMENT_COMMENTS_PAGE, current_page)

    comments_header = "Comments:"
    if total_pages > 1:
        comments_header = f"Comments (page {current_page}/{total_pages}):"

    return f"💬 **Add comment**\n\n{comments_header}\n{comments_text}\n\nWrite your message:"


async def build_add_comment_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    if feedback is None:
        return [[ButtonCallback("◁ Back", CommonCallbacks.BACK)]]

    submitter = await _get_feedback_submitter(feedback)
    viewer_is_admin = await _is_admin(api, int(user_id))
    viewer_is_submitter = submitter is not None and submitter.user_id == int(user_id)
    viewer_side = "admin" if viewer_is_admin and not viewer_is_submitter else "submitter"

    def _change_comments_page(delta: int) -> Any:
        async def _handler(inner_flow_state, inner_api: Any, inner_user_id: int) -> Optional[str]:
            current = _get_int(inner_flow_state, KEY_ADD_COMMENT_COMMENTS_PAGE, 1)
            inner_flow_state.set(KEY_ADD_COMMENT_COMMENTS_PAGE, max(1, current + int(delta)))
            return STATE_ADD_COMMENT

        return _handler

    comments_page = _get_int(flow_state, KEY_ADD_COMMENT_COMMENTS_PAGE, 1)
    _, total_comment_pages, current_comment_page = _render_comments_page(
        feedback,
        viewer_side=viewer_side,
        page=comments_page,
        line_limit=25,
    )
    flow_state.set(KEY_ADD_COMMENT_COMMENTS_PAGE, current_comment_page)

    rows: List[List[ButtonCallback]] = []
    if total_comment_pages > 1:
        nav_row: List[ButtonCallback] = []
        if current_comment_page > 1:
            nav_row.append(ButtonCallback("◀️ Newer", CB_COMMENTS_NEWER, callback_handler=_change_comments_page(-1)))
        nav_row.append(ButtonCallback(f"{current_comment_page}/{total_comment_pages}", CB_COMMENTS_INFO, callback_handler=_change_comments_page(0)))
        if current_comment_page < total_comment_pages:
            nav_row.append(ButtonCallback("Older ▶️", CB_COMMENTS_OLDER, callback_handler=_change_comments_page(1)))
        if nav_row:
            rows.append(nav_row)

    rows.append([ButtonCallback("◁ Back", CommonCallbacks.BACK)])
    return rows


async def handle_add_comment_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    message = (input_text or "").strip()
    if not message:
        await api.message_manager.send_text(
            int(user_id),
            "❌ Comment cannot be empty.",
            vanish=True,
            conv=True,
            delete_after=3,
        )
        return STATE_ADD_COMMENT

    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    if feedback is None:
        await api.message_manager.send_text(
            int(user_id),
            "❌ Feedback not found.",
            vanish=True,
            conv=True,
            delete_after=3,
        )
        return STATE_DETAILS

    if not await _can_view_feedback(api, int(user_id), feedback):
        await _notify_no_permission(api, int(user_id))
        return STATE_DETAILS

    author = await _get_telegram_user_doc(api, int(user_id))
    author_name = str(user_id) if author is None else str(author.display_name)
    author_is_admin = await _is_admin(api, int(user_id))

    feedback.comments.append(
        FeedbackComment(
            author_user_id=int(user_id),
            author_display_name=author_name,
            author_is_admin=bool(author_is_admin),
            message=message,
            created_at=_now(),
            viewed=False,
        )
    )

    # Mark feedback as "updated" for admin inbox when submitter writes.
    if not bool(author_is_admin):
        feedback.viewed_by_admin = False

    feedback.updated_at = _now()
    await feedback.save()

    return STATE_DETAILS


async def build_user_edit_menu_text(flow_state, api: Any, user_id: int) -> str:
    return "✏️ **Edit feedback**\n\nWhat do you want to edit?"


async def build_user_edit_menu_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    if feedback is None:
        return [[ButtonCallback("◁ Back", CommonCallbacks.BACK)]]

    # Only submitter can edit (admins are view-only here).
    submitter = await _get_feedback_submitter(feedback)
    if submitter is None or submitter.user_id != int(user_id):
        await _notify_no_permission(api, int(user_id))
        return [[ButtonCallback("◁ Back", CommonCallbacks.BACK)]]

    buttons: List[List[ButtonCallback]] = [
        [ButtonCallback("Title", CB_EDIT_TITLE)],
        [ButtonCallback("Description", CB_EDIT_DESCRIPTION)],
    ]

    if _format_feedback_type(feedback.type) != FeedbackType.GENERAL.value:
        buttons.append([ButtonCallback("Priority", CB_EDIT_PRIORITY)])

    buttons.append([ButtonCallback("◁ Back", CommonCallbacks.BACK)])
    return buttons


async def handle_user_edit_menu_button(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    if data == CommonCallbacks.BACK:
        return STATE_DETAILS
    if data == CB_EDIT_TITLE:
        return STATE_USER_EDIT_TITLE
    if data == CB_EDIT_DESCRIPTION:
        return STATE_USER_EDIT_DESCRIPTION
    if data == CB_EDIT_PRIORITY:
        return STATE_USER_EDIT_PRIORITY
    return None


async def _save_user_edit(
    *,
    api: Any,
    user_id: int,
    flow_state,
    feedback_id: str,
    apply,
) -> Optional[str]:
    feedback = await _get_feedback(api, str(feedback_id))
    if feedback is None:
        return STATE_DETAILS

    submitter = await _get_feedback_submitter(feedback)
    if submitter is None or submitter.user_id != int(user_id):
        await _notify_no_permission(api, int(user_id))
        return STATE_DETAILS

    apply(feedback)

    # Mark as updated for admin inbox.
    feedback.viewed_by_admin = False
    feedback.updated_at = _now()
    await feedback.save()

    return STATE_DETAILS


async def build_user_edit_title_text(flow_state, api: Any, user_id: int) -> str:
    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    old_title = "-" if feedback is None else feedback.title
    return f"Current title:\n{old_title}\n\nEnter new title:" 


async def handle_user_edit_title_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    new_title = (input_text or "").strip()

    def _apply(feedback: Feedback) -> None:
        feedback.title = new_title
        feedback.title_updated = True

    return await _save_user_edit(
        api=api,
        user_id=int(user_id),
        flow_state=flow_state,
        feedback_id=str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")),
        apply=_apply,
    )


async def build_user_edit_description_text(flow_state, api: Any, user_id: int) -> str:
    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    old_desc = "-" if feedback is None else feedback.description
    return f"Current description:\n{old_desc}\n\nEnter new description:" 


async def handle_user_edit_description_input(input_text: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    new_desc = (input_text or "").strip()

    def _apply(feedback: Feedback) -> None:
        feedback.description = new_desc
        feedback.description_updated = True

    return await _save_user_edit(
        api=api,
        user_id=int(user_id),
        flow_state=flow_state,
        feedback_id=str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")),
        apply=_apply,
    )


async def build_user_edit_priority_text(flow_state, api: Any, user_id: int) -> str:
    return "Select new priority (1-5):"


async def handle_user_priority_selected(priority: int, flow_state, api: Any, user_id: int) -> Optional[str]:
    def _apply(feedback: Feedback) -> None:
        feedback.priority = int(priority)

    return await _save_user_edit(
        api=api,
        user_id=int(user_id),
        flow_state=flow_state,
        feedback_id=str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")),
        apply=_apply,
    )


async def build_admin_change_status_text(flow_state, api: Any, user_id: int) -> str:
    return "Select new status:" 


async def build_admin_change_status_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    if not await _is_admin(api, int(user_id)):
        await _notify_no_permission(api, int(user_id))
        return [[ButtonCallback("◁ Back", CommonCallbacks.BACK)]]

    items: List[List[ButtonCallback]] = []

    def _make(status: FeedbackStatus):
        async def _handler(inner_flow_state, inner_api: Any, inner_user_id: int) -> Optional[str]:
            feedback_id = str(inner_flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
            feedback = await _get_feedback(inner_api, feedback_id)
            if feedback is None:
                return STATE_DETAILS

            feedback.status = status
            feedback.updated_at = _now()
            await feedback.save()
            return STATE_DETAILS

        return _handler

    for status in [
        FeedbackStatus.OPEN,
        FeedbackStatus.IN_PROGRESS,
        FeedbackStatus.COMPLETED,
        FeedbackStatus.REJECTED,
        FeedbackStatus.POSTPONED,
    ]:
        items.append([ButtonCallback(_format_feedback_status(status), f"status_{status.value}", callback_handler=_make(status))])

    items.append([ButtonCallback("◁ Back", CommonCallbacks.BACK)])
    return items


async def build_admin_change_priority_text(flow_state, api: Any, user_id: int) -> str:
    return "Select new priority (1-5):" 


async def handle_admin_priority_selected(priority: int, flow_state, api: Any, user_id: int) -> Optional[str]:
    if not await _is_admin(api, int(user_id)):
        await _notify_no_permission(api, int(user_id))
        return STATE_DETAILS

    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    if feedback is None:
        return STATE_DETAILS

    feedback.priority = int(priority)
    feedback.updated_at = _now()
    await feedback.save()

    return STATE_DETAILS


async def build_delete_confirm_question(flow_state, api: Any, user_id: int) -> str:
    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    if feedback is None:
        return "Delete this feedback?\n\nThis cannot be undone."
    return f"Delete feedback **{feedback.title}**?\n\nThis cannot be undone."


async def build_delete_confirm_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
    return [
        [ButtonCallback("🗑 Yes, delete", CB_DELETE_YES)],
        [ButtonCallback("◁ Back", CommonCallbacks.BACK)],
    ]


async def handle_delete_confirm_button(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    if data == CommonCallbacks.BACK:
        return STATE_DETAILS

    if data != CB_DELETE_YES:
        return None

    feedback_id = str(flow_state.get(KEY_SELECTED_FEEDBACK_ID, "")).strip()
    feedback = await _get_feedback(api, feedback_id)
    if feedback is None:
        await api.message_manager.send_text(
            int(user_id),
            "❌ Feedback not found.",
            vanish=True,
            conv=True,
            delete_after=5,
        )
        return STATE_DETAILS

    if not await _can_delete_feedback(api, int(user_id), feedback):
        await _notify_no_permission(api, int(user_id))
        return STATE_DETAILS

    try:
        await feedback.delete()
        await api.message_manager.send_text(
            int(user_id),
            "✅ Deleted.",
            vanish=True,
            conv=True,
            delete_after=5,
        )
        flow_state.set(KEY_SELECTED_FEEDBACK_ID, "")
        return str(flow_state.get(KEY_RETURN_STATE, STATE_MAIN))
    except Exception as exc:
        await api.message_manager.send_text(
            int(user_id),
            f"❌ Delete failed: {type(exc).__name__}",
            vanish=True,
            conv=True,
            delete_after=5,
        )
        return STATE_DETAILS


def create_feedback_flow() -> MessageFlow:
    flow = MessageFlow()

    async def _main_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
        is_admin = await _is_admin(api, int(user_id))
        submitter_attention = await _submitter_has_unread_admin_comment(api, int(user_id))

        view_my_label = "📄 My feedback"
        if submitter_attention:
            view_my_label = f"⚠️ {view_my_label}"

        rows: List[List[ButtonCallback]] = [
            [
                ButtonCallback("➕ Create feedback", CB_CREATE, callback_handler=select_create),
                ButtonCallback(view_my_label, CB_VIEW_MY, callback_handler=open_my_list),
            ],
        ]
        if is_admin:
            bugs_attention = await _admin_has_attention_for_type(api, FeedbackType.BUG)
            features_attention = await _admin_has_attention_for_type(api, FeedbackType.FEATURE_REQUEST)
            general_attention = await _admin_has_attention_for_type(api, FeedbackType.GENERAL)

            bugs_label = "🪲 Bugs"
            if bugs_attention:
                bugs_label = f"⚠️ {bugs_label}"
            features_label = "✨ Features"
            if features_attention:
                features_label = f"⚠️ {features_label}"
            general_label = "💬 General"
            if general_attention:
                general_label = f"⚠️ {general_label}"

            rows.append([ButtonCallback(bugs_label, CB_VIEW_BUGS, callback_handler=open_admin_bugs_list), 
                         ButtonCallback(features_label, CB_VIEW_FEATURES, callback_handler=open_admin_features_list),
                         ButtonCallback(general_label, CB_VIEW_GENERAL, callback_handler=open_admin_general_list)])
            
        rows.append(NavigationButtons.close())
        return rows

    flow.add_state(
        MessageDefinition(
            state_id=STATE_MAIN,
            state_type=StateType.BUTTON,
            text_builder=build_main_text,
            keyboard_builder=_main_keyboard,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CREATE_TYPE,
            state_type=StateType.BUTTON,
            text_builder=build_create_type_text,
            keyboard_builder=build_create_type_keyboard,
            next_state_map={CommonCallbacks.BACK: STATE_MAIN},
            exit_buttons=[],
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CREATE_TITLE,
            state_type=StateType.TEXT_INPUT,
            text="Enter title:",
            input_validator=TextLengthValidator(min_length=1, max_length=80),
            on_input_received=handle_create_title_input,
            buttons=[[ButtonCallback("◁ Back", CB_ABORT_CREATE, callback_handler=abort_create)]],
            exit_buttons=[],
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CREATE_DESCRIPTION,
            state_type=StateType.TEXT_INPUT,
            text="Enter description:",
            input_validator=TextLengthValidator(min_length=1, max_length=1500),
            on_input_received=handle_create_description_input,
            buttons=[[ButtonCallback("◁ Back", CB_ABORT_CREATE, callback_handler=abort_create)]],
            exit_buttons=[],
        )
    )

    async def _create_priority_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
        rows = await build_priority_keyboard(on_selected=handle_create_priority_selected)
        rows.append([ButtonCallback("◁ Back to menu", CB_ABORT_CREATE, callback_handler=abort_create)])
        return rows

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CREATE_PRIORITY,
            state_type=StateType.BUTTON,
            text_builder=build_create_priority_text,
            keyboard_builder=_create_priority_keyboard,
            next_state_map={CommonCallbacks.BACK: STATE_CREATE_DESCRIPTION},
            exit_buttons=[],
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_LIST_MY,
            state_type=StateType.BUTTON,
            text_builder=build_list_my_text,
            keyboard_builder=build_list_my_keyboard,
            defaults={
                KEY_LIST_PAGE_MY: 1,
                KEY_FILTER_OPEN: False,
                KEY_FILTER_IN_PROGRESS: False,
                KEY_FILTER_COMPLETED: False,
                KEY_FILTER_REJECTED: False,
                KEY_FILTER_ARCHIVED: False,
            },
            exit_buttons=[],
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_LIST_ADMIN_BUGS,
            state_type=StateType.BUTTON,
            text_builder=build_list_admin_bugs_text,
            keyboard_builder=build_list_admin_bugs_keyboard,
            defaults={
                KEY_LIST_PAGE_ADMIN_BUGS: 1,
                KEY_FILTER_OPEN: False,
                KEY_FILTER_IN_PROGRESS: False,
                KEY_FILTER_COMPLETED: False,
                KEY_FILTER_REJECTED: False,
                KEY_FILTER_ARCHIVED: False,
            },
            exit_buttons=[],
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_LIST_ADMIN_FEATURES,
            state_type=StateType.BUTTON,
            text_builder=build_list_admin_features_text,
            keyboard_builder=build_list_admin_features_keyboard,
            defaults={
                KEY_LIST_PAGE_ADMIN_FEATURES: 1,
                KEY_FILTER_OPEN: False,
                KEY_FILTER_IN_PROGRESS: False,
                KEY_FILTER_COMPLETED: False,
                KEY_FILTER_REJECTED: False,
                KEY_FILTER_ARCHIVED: False,
            },
            exit_buttons=[],
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_LIST_ADMIN_GENERAL,
            state_type=StateType.BUTTON,
            text_builder=build_list_admin_general_text,
            keyboard_builder=build_list_admin_general_keyboard,
            defaults={
                KEY_LIST_PAGE_ADMIN_GENERAL: 1,
                KEY_FILTER_OPEN: False,
                KEY_FILTER_IN_PROGRESS: False,
                KEY_FILTER_COMPLETED: False,
                KEY_FILTER_REJECTED: False,
                KEY_FILTER_ARCHIVED: False,
            },
            exit_buttons=[],
        )
    )

    # Details
    flow.add_state(
        MessageDefinition(
            state_id=STATE_DETAILS,
            state_type=StateType.BUTTON,
            text_builder=build_details_text,
            keyboard_builder=build_details_keyboard,
            on_button_press=handle_details_button,
            on_render=mark_comments_viewed_on_open,
            exit_buttons=[],
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_ADD_COMMENT,
            state_type=StateType.TEXT_INPUT,
            text_builder=build_add_comment_text,
            keyboard_builder=build_add_comment_keyboard,
            input_validator=TextLengthValidator(min_length=1, max_length=1500),
            on_input_received=handle_add_comment_input,
            next_state_map={CommonCallbacks.BACK: STATE_DETAILS},
            on_render=mark_comments_viewed_on_open,
            exit_buttons=[],
        )
    )

    # User update menu
    flow.add_state(
        MessageDefinition(
            state_id=STATE_USER_EDIT_MENU,
            state_type=StateType.BUTTON,
            text_builder=build_user_edit_menu_text,
            keyboard_builder=build_user_edit_menu_keyboard,
            on_button_press=handle_user_edit_menu_button,
            exit_buttons=[],
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_USER_EDIT_TITLE,
            state_type=StateType.TEXT_INPUT,
            text_builder=build_user_edit_title_text,
            input_validator=TextLengthValidator(min_length=1, max_length=80),
            on_input_received=handle_user_edit_title_input,
            buttons=[[ButtonCallback("◁ Back", CommonCallbacks.BACK)]],
            next_state_map={CommonCallbacks.BACK: STATE_USER_EDIT_MENU},
            exit_buttons=[],
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_USER_EDIT_DESCRIPTION,
            state_type=StateType.TEXT_INPUT,
            text_builder=build_user_edit_description_text,
            input_validator=TextLengthValidator(min_length=1, max_length=1500),
            on_input_received=handle_user_edit_description_input,
            buttons=[[ButtonCallback("◁ Back", CommonCallbacks.BACK)]],
            next_state_map={CommonCallbacks.BACK: STATE_USER_EDIT_MENU},
            exit_buttons=[],
        )
    )

    async def _user_priority_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
        return await build_priority_keyboard(on_selected=handle_user_priority_selected)

    flow.add_state(
        MessageDefinition(
            state_id=STATE_USER_EDIT_PRIORITY,
            state_type=StateType.BUTTON,
            text_builder=build_user_edit_priority_text,
            keyboard_builder=_user_priority_keyboard,
            next_state_map={CommonCallbacks.BACK: STATE_USER_EDIT_MENU},
            exit_buttons=[],
        )
    )

    # Admin status
    flow.add_state(
        MessageDefinition(
            state_id=STATE_ADMIN_CHANGE_STATUS,
            state_type=StateType.BUTTON,
            text_builder=build_admin_change_status_text,
            keyboard_builder=build_admin_change_status_keyboard,
            next_state_map={CommonCallbacks.BACK: STATE_DETAILS},
            exit_buttons=[],
        )
    )

    # Admin priority
    async def _admin_priority_keyboard(flow_state, api: Any, user_id: int) -> List[List[ButtonCallback]]:
        return await build_priority_keyboard(on_selected=handle_admin_priority_selected)

    flow.add_state(
        MessageDefinition(
            state_id=STATE_ADMIN_CHANGE_PRIORITY,
            state_type=StateType.BUTTON,
            text_builder=build_admin_change_priority_text,
            keyboard_builder=_admin_priority_keyboard,
            next_state_map={CommonCallbacks.BACK: STATE_DETAILS},
            exit_buttons=[],
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_DELETE_CONFIRM,
            state_type=StateType.BUTTON,
            text_builder=build_delete_confirm_question,
            keyboard_builder=build_delete_confirm_keyboard,
            on_button_press=handle_delete_confirm_button,
            exit_buttons=[],
        )
    )

    return flow
