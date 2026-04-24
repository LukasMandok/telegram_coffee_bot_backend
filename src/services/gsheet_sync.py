from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Literal, Optional, Protocol, Sequence, Set, Tuple

from beanie import Link as BeanieLink
import gspread
from gspread.utils import ValueInputOption

from src.api.gsheet_api import GsheetAPI
from src.common.log import Logger
from src.config import app_config
from src.models.coffee_models import CoffeeCard, Payment, PaymentReason, UserDebt
from src.models.beanie_models import PassiveUser, TelegramUser
from src.models.beanie_models import AppSettings


logger = Logger("GsheetSync")


class GsheetSyncApi(Protocol):
    repo: Any
    message_manager: Any
    def get_snapshot_manager(self) -> Any:
        ...


@dataclass(frozen=True)
class LocalPaidAmountChange:
    """Snapshot of a bot-side paid-amount change for conflict resolution."""

    card_id: str
    debtor_id: str
    value_before: float
    value_after: float


class GsheetSyncManager:
    """Singleton manager for Google Sheets sync state and orchestration."""

    _instance: Optional["GsheetSyncManager"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False  # type: ignore[attr-defined]
        return cls._instance

    def __init__(self) -> None:
        if self.__dict__.get("_initialized", False):
            return

        self.api: Optional[GsheetSyncApi] = None

        # In-process cache of the last exported snapshot, keyed by worksheet title.
        # This is intentionally not persisted: it speeds up periodic sync runs and
        # reduces unnecessary Google Sheets API calls.
        self.last_exported_signature_by_worksheet: Dict[str, str] = {}
        self.last_exported_worksheet_order: List[str] = []
        self.layout_signature_by_worksheet: Dict[str, str] = {}

        # Keep all Google Sheets operations on a single worker thread.
        self.gsheet_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gsheet")

        # Prevent overlapping syncs in-process.
        self.sync_lock = asyncio.Lock()

        # Debounced background sync task for action-triggered syncs.
        self.action_sync_task: Optional[asyncio.Task[None]] = None
        self.pending_local_paid_changes: Dict[Tuple[str, str], LocalPaidAmountChange] = {}
        self.pending_action_reasons: Set[str] = set()
        self.action_sync_rerun_requested: bool = False

        self._initialized = True

    def set_api(self, api: Optional[GsheetSyncApi]) -> None:
        """Inject the running bot API for conflict notifications."""
        self.api = api

    async def warmup_gsheet_api(self) -> None:
        """Initialize the Google Sheets client early (startup warmup)."""
        loop = asyncio.get_running_loop()

        def _warm() -> None:
            api = GsheetAPI()
            _ = api.spreadsheet.fetch_sheet_metadata()

        await loop.run_in_executor(self.gsheet_executor, _warm)

    def request_sync_after_action(
        self,
        *,
        reason: str,
        paid_changes: Optional[Sequence[LocalPaidAmountChange]] = None,
    ) -> None:
        """Request a debounced background one-shot sync after a state-changing action."""

        if paid_changes:
            for change in paid_changes:
                key = (str(change.card_id), str(change.debtor_id))
                existing = self.pending_local_paid_changes.get(key)
                if existing is None:
                    self.pending_local_paid_changes[key] = change
                else:
                    self.pending_local_paid_changes[key] = LocalPaidAmountChange(
                        card_id=existing.card_id,
                        debtor_id=existing.debtor_id,
                        value_before=existing.value_before,
                        value_after=change.value_after,
                    )

        if reason:
            self.pending_action_reasons.add(reason)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        if self.action_sync_task is not None and not self.action_sync_task.done():
            logger.debug(f"Action-triggered sync already running; queue rerun (reason={reason})")
            self.action_sync_rerun_requested = True
            return

        logger.trace(
            f"Queued action-triggered sync: reason={reason} pending_reasons={len(self.pending_action_reasons)} pending_paid_changes={len(self.pending_local_paid_changes)}"
        )

        self.action_sync_task = loop.create_task(self._run_action_triggered_sync())

    def _drain_pending_action_inputs(self) -> Tuple[Set[str], List[LocalPaidAmountChange]]:
        reasons = set(self.pending_action_reasons)
        changes = list(self.pending_local_paid_changes.values())
        self.pending_action_reasons.clear()
        self.pending_local_paid_changes = {}
        return reasons, changes

    async def _run_action_triggered_sync(self) -> None:
        while True:
            reasons, local_paid_changes = self._drain_pending_action_inputs()
            reason_text = ",".join(sorted(reasons)) if reasons else "(unknown)"

            try:
                settings_doc = await AppSettings.find_one()
                if not settings_doc:
                    settings_doc = AppSettings()
                    await settings_doc.insert()

                gsheet_settings = settings_doc.gsheet

                if not bool(gsheet_settings.sync_after_actions_enabled):
                    logger.debug(f"Sync-after-actions disabled; skip (reason={reason_text})")
                    return

                logger.info(f"Starting action-triggered Google Sheets sync (reason={reason_text})")
                # When restoring a snapshot the local DB values are authoritative; skip the
                # remote-to-local paid import so the restored values are pushed back to the sheet
                # instead of being overwritten by the stale remote values.
                skip_remote_import = "snapshot_restored" in reasons
                if local_paid_changes:
                    await self.sync_all_cards_once(mode="action", local_paid_changes=local_paid_changes, skip_remote_import=skip_remote_import)
                else:
                    await self.sync_all_cards_once(mode="manual", skip_remote_import=skip_remote_import)
                logger.info(f"Finished action-triggered Google Sheets sync (reason={reason_text})")
            except Exception as exc:
                logger.error(
                    f"Action-triggered Google Sheets sync failed (reason={reason_text}): {type(exc).__name__}: {exc!r}",
                    exc_info=exc,
                )

            if (
                not self.action_sync_rerun_requested
                and not self.pending_action_reasons
                and not self.pending_local_paid_changes
            ):
                return

            self.action_sync_rerun_requested = False

    async def sync_all_cards_once(
        self,
        *,
        mode: "SyncMode" = "manual",
        local_paid_changes: Optional[Sequence[LocalPaidAmountChange]] = None,
        skip_remote_import: bool = False,
    ) -> None:
        await _sync_all_cards_once_impl(self, mode=mode, local_paid_changes=local_paid_changes, skip_remote_import=skip_remote_import)

    async def run_periodic_gsheet_sync(self, *, stop_event: asyncio.Event) -> None:
        await _run_periodic_gsheet_sync_impl(self, stop_event=stop_event)


def get_gsheet_sync_manager() -> GsheetSyncManager:
    return GsheetSyncManager()


def set_gsheet_sync_api(api: Optional[GsheetSyncApi]) -> None:
    get_gsheet_sync_manager().set_api(api)


async def warmup_gsheet_api() -> None:
    await get_gsheet_sync_manager().warmup_gsheet_api()


def request_gsheet_sync_after_action(
    *,
    reason: str,
    paid_changes: Optional[Sequence[LocalPaidAmountChange]] = None,
) -> None:
    get_gsheet_sync_manager().request_sync_after_action(reason=reason, paid_changes=paid_changes)


@dataclass(frozen=True)
class CardUserRow:
    stable_id: str
    name: str
    coffees: int
    is_purchaser: bool
    fraction_percent: str
    cost_eur: float
    correction_eur: float
    total_debt_eur: float
    paid_eur: float
    owed_eur: float


@dataclass(frozen=True)
class CardSheetPayload:
    worksheet_title: str
    card_name: str
    card_id: str
    is_active: bool
    created_at: datetime
    purchaser_stable_id: str
    purchaser_name: str
    paypal_link: str
    total_coffees: int
    remaining_coffees: int
    cost_per_coffee: float
    total_cost: float
    updated_at_iso: str
    rows: List[CardUserRow]


def _sanitize_worksheet_title(title: str) -> str:
    # Google Sheets worksheet title constraints
    # - cannot contain: : \ / ? * [ ]
    # - max length 100
    invalid = ":\\/?*[]"
    sanitized = "".join("_" if c in invalid else c for c in title)
    sanitized = sanitized.strip()
    return sanitized[:100] if len(sanitized) > 100 else sanitized


_CARD_WORKSHEET_TITLE_RE = re.compile(r".* \([0-9a-fA-F]{4}\)$")


def _is_card_worksheet_title(title: str) -> bool:
    return bool(_CARD_WORKSHEET_TITLE_RE.fullmatch(title or ""))


def _delete_orphaned_card_worksheets(
    *,
    manager: GsheetSyncManager,
    api: GsheetAPI,
    existing_titles: Set[str],
    desired_titles: Set[str],
) -> None:
    orphaned = sorted(t for t in existing_titles if _is_card_worksheet_title(t) and t not in desired_titles)
    if not orphaned:
        return

    deleted = 0
    for title in orphaned:
        try:
            ws = api.spreadsheet.worksheet(title)
            api.spreadsheet.del_worksheet(ws)
            manager.last_exported_signature_by_worksheet.pop(title, None)
            manager.layout_signature_by_worksheet.pop(title, None)
            deleted += 1
        except Exception as exc:
            logger.error(
                f"Failed to delete orphaned worksheet: title={title} error={type(exc).__name__}: {exc!r}",
                exc_info=exc,
            )

    if deleted:
        logger.info(f"Deleted orphaned card worksheets: count={deleted}")


def _format_percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.00%"
    return f"{(numerator / denominator) * 100:.2f}%"


async def _build_payload_for_card(
    card: CoffeeCard,
    *,
    purchaser: Optional[PassiveUser],
    now_iso: str,
    debts_by_stable_id: Dict[str, UserDebt],
) -> CardSheetPayload:
    purchaser_stable_id = purchaser.stable_id if purchaser else ""
    purchaser_name = purchaser.display_name if purchaser else ""
    paypal_link = purchaser.paypal_link if purchaser and purchaser.paypal_link else ""

    total_consumed = sum(max(0, stats.total_coffees) for stats in card.consumer_stats.values())

    user_rows: List[CardUserRow] = []
    for stable_id, stats in card.consumer_stats.items():
        coffees = int(stats.total_coffees or 0)
        if coffees <= 0:
            continue

        name = (stats.display_name or "").strip() or stable_id
        fraction = _format_percent(coffees, total_consumed)
        cost = float(coffees) * float(card.cost_per_coffee)

        correction = 0.0
        total_debt = 0.0
        paid = 0.0

        if stable_id == purchaser_stable_id:
            correction = 0.0
            total_debt = 0.0
            paid = 0.0
        else:
            debt = debts_by_stable_id.get(stable_id)
            if debt is not None:
                correction = float(debt.debt_correction)
                total_debt = float(debt.total_amount)
                paid = float(debt.paid_amount)
                cost = float(debt.base_amount)
            else:
                # No debt record -> don't invent corrections; keep correction 0.
                correction = 0.0
                total_debt = float(cost)
                paid = 0.0

        owed = max(0.0, total_debt - paid)

        user_rows.append(
            CardUserRow(
                stable_id=stable_id,
                name=name,
                coffees=coffees,
                is_purchaser=(stable_id == purchaser_stable_id),
                fraction_percent=fraction,
                cost_eur=round(cost, 2),
                correction_eur=round(correction, 2),
                total_debt_eur=round(total_debt, 2),
                paid_eur=round(paid, 2),
                owed_eur=round(owed, 2),
            )
        )

    user_rows.sort(key=lambda r: r.name.casefold())

    worksheet_title = _sanitize_worksheet_title(f"{card.name} ({str(card.id)[-4:]})")
    return CardSheetPayload(
        worksheet_title=worksheet_title,
        card_name=card.name,
        card_id=str(card.id),
        is_active=bool(card.is_active),
        created_at=card.created_at,
        purchaser_stable_id=purchaser_stable_id,
        purchaser_name=purchaser_name,
        paypal_link=paypal_link,
        total_coffees=int(card.total_coffees),
        remaining_coffees=int(card.remaining_coffees),
        cost_per_coffee=float(card.cost_per_coffee),
        total_cost=float(card.total_cost),
        updated_at_iso=now_iso,
        rows=user_rows,
    )


def _grid_signature(grid: List[List[Any]]) -> str:
    """Return a stable signature for a rendered grid.

    We intentionally ignore the "Last updated" timestamp cell to allow skipping
    Sheets updates when the underlying data hasn't changed.
    """

    normalized: List[List[Any]] = []
    for r_idx, row in enumerate(grid):
        normalized_row: List[Any] = []
        for c_idx, value in enumerate(row):
            if r_idx == 1 and c_idx == 4:
                normalized_row.append("")
            else:
                normalized_row.append(value)
        normalized.append(normalized_row)

    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _payload_to_grid(payload: CardSheetPayload) -> List[List[Any]]:
    status = "Active" if payload.is_active else "Completed"

    # Layout constants
    title_row = 1
    meta_row_1 = 2
    meta_row_2 = 3
    meta_row_3 = 4
    spacer_row = 5
    header_row = 6
    first_data_row = 7

    grid: List[List[Any]] = []

    # Row 1: Title
    grid.append([payload.card_name, "", "", "", "", "", "", "", ""])

    # Row 2-3: Meta info
    grid.append(["Status", status, "", "Last updated", payload.updated_at_iso, "", "Purchaser", payload.purchaser_name, ""])
    grid.append(["Total coffees", payload.total_coffees, "", "Coffees left", payload.remaining_coffees, "", "PayPal", payload.paypal_link, ""])
    grid.append(["Cost/coffee", payload.cost_per_coffee, "", "Total cost", payload.total_cost, "", "", "", ""])

    # Spacer row to visually separate meta info from the main table
    grid.append(["", "", "", "", "", "", "", "", ""])

    # Row 5: Table header
    grid.append(
        [
            "Name",
            "Coffees",
            "Fraction",
            "Cost (€)",
            "Correction (€)",
            "Total debt (€)",
            "Paid (€)",
            "Owed (€)",
            "Stable ID",
        ]
    )

    # Data rows + formulas.
    # We keep table start fixed to make chart/formatting stable.
    # Fraction uses SUM over coffees column.
    if payload.rows:
        last_data_row = first_data_row + len(payload.rows) - 1
    else:
        last_data_row = first_data_row

    coffees_sum_range = f"$B${first_data_row}:$B${last_data_row}"
    # Cost/coffee is in column B of the 3rd meta row.
    cost_per_coffee_cell = f"$B${meta_row_3}"

    for idx, row in enumerate(payload.rows):
        sheet_row = first_data_row + idx
        # Column letters: A=name, B=coffees, C=fraction, D=cost, E=correction, F=total debt, G=paid, H=owed
        # Use ';' argument separators for locales where ',' is decimal separator (e.g. German).
        fraction_formula = f"=IF(SUM({coffees_sum_range})=0;0;B{sheet_row}/SUM({coffees_sum_range}))"

        if row.is_purchaser:
            cost_cell = f"=B{sheet_row}*{cost_per_coffee_cell}"
            correction_cell: Any = ""
            total_debt_cell: Any = ""
            paid_cell: Any = ""
            owed_cell: Any = ""
        else:
            cost_cell = f"=B{sheet_row}*{cost_per_coffee_cell}"
            correction_cell = row.correction_eur
            total_debt_cell = f"=D{sheet_row}+E{sheet_row}"
            paid_cell = row.paid_eur
            owed_cell = f"=F{sheet_row}-G{sheet_row}"

        grid.append(
            [
                row.name,
                row.coffees,
                fraction_formula,
                cost_cell,
                correction_cell,
                total_debt_cell,
                paid_cell,
                owed_cell,
                row.stable_id,
            ]
        )

    # Totals row (bold)
    totals_row = first_data_row + len(payload.rows) + 1
    if payload.rows:
        grid.append([])
        grid.append(
            [
                "Sum",
                f"=SUM(B{first_data_row}:B{last_data_row})",
                "",
                f"=SUM(D{first_data_row}:D{last_data_row})",
                f"=SUM(E{first_data_row}:E{last_data_row})",
                f"=SUM(F{first_data_row}:F{last_data_row})",
                f"=SUM(G{first_data_row}:G{last_data_row})",
                f"=SUM(H{first_data_row}:H{last_data_row})",
                "",
            ]
        )

    return grid


def _parse_money_cell(value: Any) -> Optional[float]:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return 0.0

    # Strip currency symbols and other non-numeric characters.
    cleaned = re.sub(r"[^0-9,\.\-]", "", text)
    if not cleaned or cleaned in {"-", ".", ","}:
        return None

    # Handle locales: "1.234,56" vs "1,234.56"
    if "." in cleaned and "," in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "")
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except Exception:
        return None


def _read_remote_paid_by_worksheet_titles(worksheet_titles: List[str]) -> Dict[str, Dict[str, float]]:
    """Read remote paid amounts per worksheet title.

    Returns mapping: worksheet_title -> stable_id -> paid_amount
    """

    api = GsheetAPI()

    results: Dict[str, Dict[str, float]] = {}
    for title in worksheet_titles:
        if not title:
            continue

        try:
            ws = api.spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            continue

        values: List[List[str]] = ws.get_all_values() or []
        logger.trace(f"Remote paid read: title={title} rows={len(values)}")
        if len(values) < 7:
            continue

        paid_by_stable_id: Dict[str, float] = {}
        for idx, row in enumerate(values[6:], start=7):
            if not row:
                continue

            name_cell = (row[0] if len(row) > 0 else "").strip()
            if name_cell.lower() == "sum":
                break

            stable_id_cell = (row[8] if len(row) > 8 else "").strip()
            if not stable_id_cell:
                logger.trace(
                    f"Remote paid row skipped (missing stable_id): title={title} row={idx} name={name_cell!r}"
                )
                continue

            paid_cell = row[6] if len(row) > 6 else ""
            paid_value = _parse_money_cell(paid_cell)
            if paid_value is None:
                logger.trace(
                    f"Remote paid parse failed: title={title} row={idx} stable_id={stable_id_cell} raw={paid_cell!r}"
                )
                continue
            paid_by_stable_id[stable_id_cell] = max(0.0, float(paid_value))

        if paid_by_stable_id:
            results[title] = paid_by_stable_id
            logger.debug(
                f"Remote paid parsed: title={title} stable_ids={len(paid_by_stable_id)}",
                extra_tag="GSHEET",
            )

    return results


def _apply_layout_chart_and_protection(
    *,
    api: GsheetAPI,
    worksheet: Any,
    payload: CardSheetPayload,
    chart_ids_by_sheet_id: Dict[int, List[int]],
    protected_range_ids_by_sheet_id: Dict[int, List[int]],
    apply_full_layout: bool,
    sheet_row_count: int,
    sheet_col_count: int,
) -> None:
    sheet_id = int(worksheet._properties.get("sheetId"))

    data_row_count = len(payload.rows)

    # Delete existing charts on this worksheet only when full layout is refreshed.
    delete_chart_requests = (
        [
            {"deleteEmbeddedObject": {"objectId": chart_id}}
            for chart_id in chart_ids_by_sheet_id.get(sheet_id, [])
        ]
        if apply_full_layout
        else []
    )

    # Delete existing protections on this worksheet to avoid duplicates.
    delete_protection_requests = [
        {"deleteProtectedRange": {"protectedRangeId": pr_id}}
        for pr_id in protected_range_ids_by_sheet_id.get(sheet_id, [])
    ]

    # Format ranges (0-based indices)
    header_row_index = 5  # row 6
    title_row_index = 0

    first_data_row_index = 6  # row 7
    last_data_row_index = first_data_row_index + max(0, data_row_count)

    currency_cols = [3, 4, 5, 6, 7]  # D-H
    percent_col = 2  # C

    requests: List[Dict[str, Any]] = []

    if apply_full_layout:
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {
                            "frozenRowCount": 6,
                            "frozenColumnCount": 1,
                        },
                    },
                    "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
                }
            }
        )

    # Bold title row
    if apply_full_layout:
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": title_row_index,
                        "endRowIndex": title_row_index + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 9,
                    },
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}},
                    "fields": "userEnteredFormat.textFormat",
                }
            }
        )

    # Bold header row
    if apply_full_layout:
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": header_row_index,
                        "endRowIndex": header_row_index + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 9,
                    },
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            }
        )

    # Percent format for fraction column (data rows only)
    if apply_full_layout:
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": first_data_row_index,
                        "endRowIndex": last_data_row_index,
                        "startColumnIndex": percent_col,
                        "endColumnIndex": percent_col + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "PERCENT", "pattern": "0.00%"}
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            }
        )

    # Currency format for cost/debt columns (data rows + totals rows)
    if apply_full_layout:
        for col in currency_cols:
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": first_data_row_index,
                            "endRowIndex": last_data_row_index + 2,
                            "startColumnIndex": col,
                            "endColumnIndex": col + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {"type": "CURRENCY", "pattern": "€0.00"}
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            )

    # Set column widths (A wider)
    if apply_full_layout:
        requests.extend(
            [
                {
                    "updateDimensionProperties": {
                        "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                        "properties": {"pixelSize": 180},
                        "fields": "pixelSize",
                    }
                },
                {
                    "updateDimensionProperties": {
                        "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 8},
                        "properties": {"pixelSize": 120},
                        "fields": "pixelSize",
                    }
                },
                {
                    "updateDimensionProperties": {
                        "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 8, "endIndex": 9},
                        "properties": {"hiddenByUser": True},
                        "fields": "hiddenByUser",
                    }
                },
            ]
        )

    # Add a pie chart to the right of the table
    if apply_full_layout and data_row_count > 0:
        first_row = 6  # row 7 (0-based)
        last_row = first_row + data_row_count
        chart_request = {
            "addChart": {
                "chart": {
                    "spec": {
                        "title": "Coffee fraction",
                        "pieChart": {
                            "legendPosition": "LABELED_LEGEND",
                            "domain": {
                                "sourceRange": {
                                    "sources": [
                                        {
                                            "sheetId": sheet_id,
                                            "startRowIndex": first_row,
                                            "endRowIndex": last_row,
                                            "startColumnIndex": 0,
                                            "endColumnIndex": 1,
                                        }
                                    ]
                                }
                            },
                            "series": {
                                "sourceRange": {
                                    "sources": [
                                        {
                                            "sheetId": sheet_id,
                                            "startRowIndex": first_row,
                                            "endRowIndex": last_row,
                                            "startColumnIndex": 1,
                                            "endColumnIndex": 2,
                                        }
                                    ]
                                }
                            },
                        }
                    },
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {
                                "sheetId": sheet_id,
                                "rowIndex": 5,
                                "columnIndex": 9,
                            },
                            "offsetXPixels": 0,
                            "offsetYPixels": 0,
                        }
                    },
                }
            }
        }
        requests.append(chart_request)

    requests.append(
        _build_sheet_protection_request(
            sheet_id=sheet_id,
            sheet_row_count=sheet_row_count,
            sheet_col_count=sheet_col_count,
            is_active=payload.is_active,
            data_row_count=data_row_count,
        )
    )

    batch = {"requests": delete_chart_requests + delete_protection_requests + requests}
    api.spreadsheet.batch_update(batch)


def _build_sheet_protection_request(
    *,
    sheet_id: int,
    sheet_row_count: int,
    sheet_col_count: int,
    is_active: bool,
    data_row_count: int,
) -> Dict[str, Any]:
    if is_active:
        protected_range: Dict[str, Any] = {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": sheet_row_count,
                "startColumnIndex": 0,
                "endColumnIndex": sheet_col_count,
            },
            "warningOnly": False,
            "description": "Lock active card",
            "editors": {
                "users": [app_config.SERVICE_ACCOUNT_EMAIL],
                "groups": [],
                "domainUsersCanEdit": False,
            },
        }

        return {"addProtectedRange": {"protectedRange": protected_range}}

    protected_range: Dict[str, Any] = {
        "range": {"sheetId": sheet_id},
        "warningOnly": False,
        "description": "Lock completed card except paid column",
        "editors": {
            "users": [app_config.SERVICE_ACCOUNT_EMAIL],
            "groups": [],
            "domainUsersCanEdit": False,
        },
    }

    if not is_active and data_row_count > 0:
        first_data_row_index = 6  # row 7 (0-based)
        protected_range["unprotectedRanges"] = [
            {
                "sheetId": sheet_id,
                "startRowIndex": first_data_row_index,
                "endRowIndex": first_data_row_index + data_row_count,
                "startColumnIndex": 6,
                "endColumnIndex": 7,
            }
        ]

    return {"addProtectedRange": {"protectedRange": protected_range}}


def _write_payloads_to_gsheet(
    manager: GsheetSyncManager,
    payloads: List[CardSheetPayload],
    force_refresh_titles: Optional[Set[str]] = None,
) -> None:
    api = GsheetAPI()
    force_refresh_titles = set(force_refresh_titles or set())

    payloads_sorted = sorted(payloads, key=lambda p: p.created_at, reverse=True)

    metadata = api.spreadsheet.fetch_sheet_metadata()
    existing_titles: Set[str] = {
        str(s.get("properties", {}).get("title"))
        for s in metadata.get("sheets", [])
        if s.get("properties", {}).get("title") is not None
    }

    chart_ids_by_sheet_id: Dict[int, List[int]] = {}
    protected_range_ids_by_sheet_id: Dict[int, List[int]] = {}
    sheet_geometry_by_title: Dict[str, Tuple[int, int]] = {}
    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        sheet_id = props.get("sheetId")
        if sheet_id is None:
            continue

        grid_properties = props.get("gridProperties", {})
        title = str(props.get("title") or "")
        sheet_geometry_by_title[title] = (
            int(grid_properties.get("rowCount", 1000)),
            int(grid_properties.get("columnCount", 9)),
        )

        charts = sheet.get("charts", [])
        chart_ids_by_sheet_id[int(sheet_id)] = [
            int(c.get("chartId")) for c in charts if c.get("chartId") is not None
        ]

        protected_ranges = sheet.get("protectedRanges", [])
        protected_range_ids_by_sheet_id[int(sheet_id)] = [
            int(r.get("protectedRangeId"))
            for r in protected_ranges
            if r.get("protectedRangeId") is not None
        ]

    updated_count = 0
    skipped_count = 0

    for payload in payloads_sorted:
        grid = _payload_to_grid(payload)
        if not grid:
            logger.debug(f"Skip worksheet (empty grid): title={payload.worksheet_title}")
            continue

        signature = _grid_signature(grid)
        cached = manager.last_exported_signature_by_worksheet.get(payload.worksheet_title)
        force_refresh = payload.worksheet_title in force_refresh_titles

        if payload.worksheet_title in existing_titles and cached == signature and not force_refresh:
            logger.debug(f"Skip worksheet values (cache hit): title={payload.worksheet_title}")
            logger.trace(f"Cache signature: title={payload.worksheet_title} sig={signature}")
            skipped_count += 1
        else:
            if payload.worksheet_title in existing_titles:
                logger.debug(
                    f"Update worksheet (cache miss): title={payload.worksheet_title} prev_sig={'set' if cached else 'none'}"
                )
                logger.trace(
                    f"Update signatures: title={payload.worksheet_title} prev={cached or ''} new={signature}"
                )
            else:
                logger.debug(f"Create worksheet: title={payload.worksheet_title}")

            worksheet = api._get_or_create_worksheet(payload.worksheet_title)
            worksheet.clear()

            worksheet.update(values=grid, range_name="A1", value_input_option=ValueInputOption.user_entered)

            manager.last_exported_signature_by_worksheet[payload.worksheet_title] = signature
            updated_count += 1

        worksheet = api._get_or_create_worksheet(payload.worksheet_title)

        layout_signature = f"active={int(payload.is_active)};rows={len(payload.rows)}"
        apply_full_layout = (
            payload.worksheet_title not in existing_titles
            or manager.layout_signature_by_worksheet.get(payload.worksheet_title) != layout_signature
        )
        logger.trace(
            f"Layout decision: title={payload.worksheet_title} apply_full_layout={apply_full_layout} signature={layout_signature}"
        )

        # Apply layout styling, chart and protection after values exist
        sheet_row_count, sheet_col_count = sheet_geometry_by_title.get(
            payload.worksheet_title,
            (1000, 9),
        )

        _apply_layout_chart_and_protection(
            api=api,
            worksheet=worksheet,
            payload=payload,
            chart_ids_by_sheet_id=chart_ids_by_sheet_id,
            protected_range_ids_by_sheet_id=protected_range_ids_by_sheet_id,
            apply_full_layout=apply_full_layout,
            sheet_row_count=sheet_row_count,
            sheet_col_count=sheet_col_count,
        )

        manager.layout_signature_by_worksheet[payload.worksheet_title] = layout_signature

        if payload.worksheet_title not in manager.last_exported_signature_by_worksheet:
            manager.last_exported_signature_by_worksheet[payload.worksheet_title] = signature

    card_titles = [p.worksheet_title for p in payloads_sorted]

    _delete_orphaned_card_worksheets(
        manager=manager,
        api=api,
        existing_titles=existing_titles,
        desired_titles=set(card_titles),
    )

    if card_titles != manager.last_exported_worksheet_order:
        _reorder_card_worksheets(api=api, card_titles=card_titles)
        manager.last_exported_worksheet_order = card_titles

    logger.info(
        f"Gsheet export done: updated={updated_count} skipped={skipped_count} total={len(payloads_sorted)}"
    )


def _reorder_card_worksheets(*, api: GsheetAPI, card_titles: List[str]) -> None:
    """Move card worksheets to the front (left) in the given order."""
    worksheets = api.spreadsheet.worksheets()
    title_to_sheet_id: Dict[str, int] = {}
    for ws in worksheets:
        sheet_id = ws._properties.get("sheetId")
        if sheet_id is None:
            continue
        title_to_sheet_id[ws.title] = int(sheet_id)

    card_title_set = set(card_titles)
    non_card_titles = [ws.title for ws in worksheets if ws.title not in card_title_set]
    new_order_titles = [t for t in card_titles if t in title_to_sheet_id] + non_card_titles

    requests: List[Dict[str, Any]] = []
    for new_index, title in enumerate(new_order_titles):
        sheet_id = title_to_sheet_id.get(title)
        if sheet_id is None:
            continue
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "index": new_index},
                    "fields": "index",
                }
            }
        )

    if requests:
        api.spreadsheet.batch_update({"requests": requests})


SyncMode = Literal["periodic", "action", "manual"]


@dataclass(frozen=True)
class PaidConflictEvent:
    kind: Literal["overwrite_sheet", "accept_remote"]
    card_id: str
    card_name: str
    debtor_stable_id: str
    debtor_name: str
    value_before: float
    value_after: float
    value_remote: float


@dataclass(frozen=True)
class _DebtPaidBackup:
    debt: UserDebt
    paid_amount: float
    is_settled: bool
    settled_at: Optional[datetime]
    updated_at: datetime


async def _rollback_paid_updates(backups: Sequence[_DebtPaidBackup]) -> None:
    for backup in backups:
        backup.debt.paid_amount = float(backup.paid_amount)
        backup.debt.is_settled = bool(backup.is_settled)
        backup.debt.settled_at = backup.settled_at
        backup.debt.updated_at = backup.updated_at
        await backup.debt.save()


async def _rollback_created_payments(payments: Sequence[Payment]) -> None:
    for payment in reversed(list(payments)):
        try:
            await payment.delete()
        except Exception:
            continue


async def _create_payment_for_remote_paid_import(
    *,
    debt: UserDebt,
    amount: float,
    description: str,
) -> Payment:
    if amount <= 0:
        raise ValueError("Payment amount must be greater than zero")

    payment = Payment(
        payer=debt.debtor,  # type: ignore[arg-type]
        recipient=debt.creditor,  # type: ignore[arg-type]
        amount=float(amount),
        reason=PaymentReason.GSHEET_IMPORT,
        target_debt=debt,
        description=description,
    )
    await payment.insert()
    return payment


def _format_eur(value: float) -> str:
    return f"{float(value):.2f} €"


async def _send_conflict_notifications(api: Optional[GsheetSyncApi], events: Sequence[PaidConflictEvent]) -> None:
    if api is None:
        return

    if not events:
        return

    stable_ids = sorted({e.debtor_stable_id for e in events if e.debtor_stable_id})
    users = await TelegramUser.find({"stable_id": {"$in": stable_ids}}).to_list() if stable_ids else []
    telegram_user_id_by_stable_id: Dict[str, int] = {
        str(u.stable_id): int(u.user_id) for u in users if u.stable_id and u.user_id is not None
    }

    admin_ids_raw = []
    try:
        admin_ids_raw = await api.repo.get_registered_admins()
    except Exception:
        admin_ids_raw = []

    admin_ids: List[int] = []
    for admin_id in admin_ids_raw or []:
        try:
            admin_ids.append(int(admin_id))
        except Exception:
            continue

    for event in events:
        debtor_user_id = telegram_user_id_by_stable_id.get(event.debtor_stable_id)

        debtor_text: Optional[str] = None
        admin_text: Optional[str] = None

        if event.kind == "overwrite_sheet":
            debtor_text = (
                "⚠️ **Payment mismatch detected**\n\n"
                f"Card: **{event.card_name}**\n"
                f"Your paid amount in the spreadsheet was **{_format_eur(event.value_remote)}**, "
                f"but the bot recorded **{_format_eur(event.value_after)}** internally.\n\n"
                "The spreadsheet value was overwritten with the internal value."
            )
        else:
            debtor_text = (
                "💸 **Payment registered from spreadsheet**\n\n"
                f"Card: **{event.card_name}**\n"
                f"Paid amount updated: **{_format_eur(event.value_before)} → {_format_eur(event.value_remote)}**"
            )

            admin_text = (
                "⚠️ **GSheet payment update accepted (remote won)**\n\n"
                f"Card: **{event.card_name}**\n"
                f"Debtor: **{event.debtor_name}** (stable_id={event.debtor_stable_id})\n"
                f"before(bot)={_format_eur(event.value_before)} remote(sheet)={_format_eur(event.value_remote)}"
            )

        if debtor_text and debtor_user_id is not None:
            try:
                await api.message_manager.send_user_notification(int(debtor_user_id), debtor_text)
            except Exception:
                pass

        if admin_text is not None:
            for admin_id in admin_ids:
                try:
                    await api.message_manager.send_user_notification(int(admin_id), admin_text)
                except Exception:
                    continue


async def _sync_all_cards_once_impl(
    manager: GsheetSyncManager,
    *,
    mode: SyncMode = "manual",
    local_paid_changes: Optional[Sequence[LocalPaidAmountChange]] = None,
    skip_remote_import: bool = False,
) -> None:
    async with manager.sync_lock:
        logger.info(f"Gsheet sync started: Coffee Cards (mode={mode})", extra_tag="GSHEET")

        start_ts = time.perf_counter()
        logger.debug("Sync snapshot: start")

        now_iso = datetime.now().isoformat(timespec="seconds")

        settings = await AppSettings.find_one()
        if not settings:
            settings = AppSettings()
            await settings.insert()

        two_way_enabled = bool(settings.gsheet.two_way_sync_enabled)
        logger.debug(f"Gsheet two-way sync enabled={two_way_enabled}", extra_tag="GSHEET")

        cards = await CoffeeCard.find(fetch_links=False).to_list()
        logger.debug(f"Snapshot loaded: cards={len(cards)}")

        purchaser_ids: Set[Any] = set()
        card_ids: List[Any] = []
        completed_card_count = 0
        for card in cards:
            card_ids.append(card.id)
            if not card.is_active:
                completed_card_count += 1
            purchaser = card.purchaser
            if isinstance(purchaser, BeanieLink):
                purchaser_ids.add(purchaser.ref.id)

        logger.debug(
            f"Snapshot loaded: completed_cards={completed_card_count} active_cards={len(cards) - completed_card_count}",
            extra_tag="GSHEET",
        )

        debts = (
            await UserDebt.find({"coffee_card.$id": {"$in": card_ids}}, fetch_links=False).to_list()  # type: ignore[arg-type]
            if card_ids
            else []
        )
        logger.debug(f"Snapshot loaded: debts={len(debts)}")

        debtor_ids: Set[Any] = set()
        for debt in debts:
            debtor = debt.debtor
            if isinstance(debtor, BeanieLink):
                debtor_ids.add(debtor.ref.id)

        user_ids = list(set(purchaser_ids) | debtor_ids)
        passive_users = await PassiveUser.find({"_id": {"$in": user_ids}}).to_list() if user_ids else []
        telegram_users = await TelegramUser.find({"_id": {"$in": user_ids}}).to_list() if user_ids else []
        logger.debug(
            f"Snapshot loaded: users_total={len(user_ids)} passive={len(passive_users)} telegram={len(telegram_users)}",
            extra_tag="GSHEET",
        )

        users_by_id: Dict[Any, PassiveUser] = {}
        stable_id_by_id: Dict[Any, str] = {}
        for user in [*passive_users, *telegram_users]:
            if user.id is None:
                continue
            users_by_id[user.id] = user
            stable_id_by_id[user.id] = user.stable_id

        telegram_users_by_stable_id: Dict[str, TelegramUser] = {
            user.stable_id: user for user in telegram_users if isinstance(user, TelegramUser)
        }

        debts_by_card_and_stable_id: Dict[str, Dict[str, UserDebt]] = {}
        for debt in debts:
            card_link = debt.coffee_card
            debtor_link = debt.debtor
            if not isinstance(card_link, BeanieLink) or not isinstance(debtor_link, BeanieLink):
                continue

            card_id = str(card_link.ref.id)
            debtor_id = debtor_link.ref.id
            stable_id = stable_id_by_id.get(debtor_id, "")
            if not stable_id:
                continue

            debts_by_card_and_stable_id.setdefault(card_id, {})[stable_id] = debt

        logger.trace(f"Snapshot indexed: cards_with_debts={len(debts_by_card_and_stable_id)}")

        card_by_id: Dict[str, CoffeeCard] = {str(c.id): c for c in cards}
        worksheet_title_by_card_id: Dict[str, str] = {
            str(c.id): _sanitize_worksheet_title(f"{c.name} ({str(c.id)[-4:]})") for c in cards
        }

        remote_paid_by_title: Dict[str, Dict[str, float]] = {}
        _closed_card_titles: List[str] = []
        if two_way_enabled:
            _closed_card_titles = [
                worksheet_title_by_card_id[str(c.id)]
                for c in cards
                if not c.is_active
            ]

            if skip_remote_import:
                logger.debug(
                    f"Remote paid import skipped (snapshot restore): closed_titles={len(_closed_card_titles)}",
                    extra_tag="GSHEET",
                )
            else:
                logger.debug(
                    f"Remote paid scan: closed_titles={len(_closed_card_titles)}",
                    extra_tag="GSHEET",
                )
                logger.trace(f"Remote paid titles: {_closed_card_titles}")

                if _closed_card_titles:
                    loop = asyncio.get_running_loop()
                    remote_paid_by_title = await loop.run_in_executor(
                        manager.gsheet_executor,
                        _read_remote_paid_by_worksheet_titles,
                        _closed_card_titles,
                    )
                    logger.debug(
                        f"Remote paid read: worksheets_with_rows={len(remote_paid_by_title)}",
                        extra_tag="GSHEET",
                    )
        else:
            logger.debug("Remote paid import skipped (two-way disabled)", extra_tag="GSHEET")

        local_change_by_key: Dict[Tuple[str, str], LocalPaidAmountChange] = {}
        if local_paid_changes:
            for change in local_paid_changes:
                local_change_by_key[(str(change.card_id), str(change.debtor_id))] = change

        conflict_events: List[PaidConflictEvent] = []
        debt_backups: List[_DebtPaidBackup] = []
        planned_remote_payments: List[Tuple[UserDebt, float, str]] = []
        remote_paid_accept_notifications: List[Tuple[UserDebt, float]] = []
        force_sheet_refresh_titles: Set[str] = set()

        # After a snapshot restore the local paid values are authoritative. Force-refresh all
        # closed-card worksheets so the sheet is overwritten with the restored local values.
        if skip_remote_import and _closed_card_titles:
            force_sheet_refresh_titles.update(_closed_card_titles)
            logger.debug(
                f"Force-refresh closed worksheets after snapshot restore: count={len(_closed_card_titles)}",
                extra_tag="GSHEET",
            )

        if two_way_enabled and remote_paid_by_title:
            eps = 1e-9

            for debt in debts:
                card_link = debt.coffee_card
                debtor_link = debt.debtor
                if not isinstance(card_link, BeanieLink) or not isinstance(debtor_link, BeanieLink):
                    continue

                card_id = str(card_link.ref.id)
                debtor_id = str(debtor_link.ref.id)

                card = card_by_id.get(card_id)
                if card is None or card.is_active:
                    continue

                stable_id = stable_id_by_id.get(debtor_link.ref.id, "")
                if not stable_id:
                    continue

                title = worksheet_title_by_card_id.get(card_id, "")
                remote_paid = (remote_paid_by_title.get(title, {}) or {}).get(stable_id)
                if remote_paid is None:
                    continue

                current_paid = float(debt.paid_amount or 0.0)

                before_paid = current_paid
                after_paid = current_paid
                local_change = local_change_by_key.get((card_id, debtor_id)) if mode == "action" else None
                if local_change is not None:
                    before_paid = float(local_change.value_before)
                    after_paid = current_paid

                stats = card.consumer_stats.get(stable_id)
                debtor_name = stats.display_name if stats is not None else stable_id

                logger.trace(
                    "Remote paid candidate: "
                    f"card={card.name} title={title} debtor={debtor_name} stable_id={stable_id} "
                    f"paid_current={current_paid:.2f} paid_remote={float(remote_paid):.2f} "
                    f"mode={mode} before={before_paid:.2f} after={after_paid:.2f} has_local_change={local_change is not None}"
                )

                if mode == "action" and local_change is not None and abs(after_paid - before_paid) > eps:
                    if abs(after_paid - float(remote_paid)) > eps:
                        logger.warning(
                            "Gsheet conflict: bot change overwriting remote paid amount "
                            f"(card={card.name} debtor={debtor_name} before={before_paid:.2f} after={after_paid:.2f} remote={float(remote_paid):.2f})",
                            extra_tag="GSHEET",
                        )
                        conflict_events.append(
                            PaidConflictEvent(
                                kind="overwrite_sheet",
                                card_id=card_id,
                                card_name=str(card.name),
                                debtor_stable_id=stable_id,
                                debtor_name=str(debtor_name),
                                value_before=before_paid,
                                value_after=after_paid,
                                value_remote=float(remote_paid),
                            )
                        )
                    continue

                delta = float(remote_paid) - before_paid
                if abs(delta) <= eps:
                    logger.trace(
                        f"Remote paid unchanged: card={card.name} debtor={debtor_name} paid={current_paid:.2f}"
                    )
                    continue

                if delta < 0:
                    logger.warning(
                        "Gsheet remote paid decreased: overwriting remote with local value "
                        f"(card={card.name} debtor={debtor_name} remote={float(remote_paid):.2f} local={current_paid:.2f})",
                        extra_tag="GSHEET",
                    )
                    force_sheet_refresh_titles.add(title)
                    continue

                debt_backups.append(
                    _DebtPaidBackup(
                        debt=debt,
                        paid_amount=float(debt.paid_amount or 0.0),
                        is_settled=bool(debt.is_settled),
                        settled_at=debt.settled_at,
                        updated_at=debt.updated_at or datetime.now(),
                    )
                )

                new_paid = max(0.0, min(float(remote_paid), float(debt.total_amount or 0.0)))
                if abs(new_paid - current_paid) <= eps:
                    debt_backups.pop()
                    continue

                description = (
                    f"Imported from Google Sheets: card='{card.name}' paid {current_paid:.2f} -> {new_paid:.2f}"
                )
                planned_remote_payments.append((debt, float(new_paid - current_paid), description))
                remote_paid_accept_notifications.append((debt, float(new_paid - current_paid)))

                debt.paid_amount = new_paid
                debt.updated_at = datetime.now()
                unpaid = float(debt.total_amount or 0.0) - float(debt.paid_amount)
                if unpaid <= eps:
                    debt.paid_amount = float(debt.total_amount or 0.0)
                    debt.is_settled = True
                    if debt.settled_at is None:
                        debt.settled_at = debt.updated_at
                else:
                    debt.is_settled = False
                    debt.settled_at = None

                    if mode == "action":
                        logger.warning(
                            "Gsheet conflict: accepting remote paid amount "
                            f"(card={card.name} debtor={debtor_name} before={before_paid:.2f} remote={float(remote_paid):.2f})",
                            extra_tag="GSHEET",
                        )
                        conflict_events.append(
                            PaidConflictEvent(
                                kind="accept_remote",
                                card_id=card_id,
                                card_name=str(card.name),
                                debtor_stable_id=stable_id,
                                debtor_name=str(debtor_name),
                                value_before=before_paid,
                                value_after=after_paid,
                                value_remote=float(remote_paid),
                            )
                        )

        saved_backups: List[_DebtPaidBackup] = []
        created_remote_payments: List[Payment] = []

        if debt_backups:
            snapshot_manager = manager.api.get_snapshot_manager() if manager.api is not None else None

            async def _apply_remote_paid_updates() -> None:
                try:
                    for backup in debt_backups:
                        await backup.debt.save()
                        saved_backups.append(backup)
                except Exception as exc:
                    logger.error(
                        f"Failed to save remote paid updates; rolling back ({type(exc).__name__}: {exc!r})",
                        extra_tag="GSHEET",
                        exc_info=exc,
                    )
                    try:
                        await _rollback_paid_updates(saved_backups)
                    except Exception:
                        logger.error("Rollback of paid updates failed", extra_tag="GSHEET")
                    raise

                if planned_remote_payments:
                    try:
                        for debt, amount, description in planned_remote_payments:
                            if amount <= 0:
                                continue
                            payment = await _create_payment_for_remote_paid_import(
                                debt=debt,
                                amount=float(amount),
                                description=description,
                            )
                            created_remote_payments.append(payment)
                    except Exception as exc:
                        logger.error(
                            f"Failed to create Payment records for remote paid updates; rolling back ({type(exc).__name__}: {exc!r})",
                            extra_tag="GSHEET",
                            exc_info=exc,
                        )
                        try:
                            await _rollback_created_payments(created_remote_payments)
                        except Exception:
                            logger.error("Rollback of created payments failed", extra_tag="GSHEET")
                        try:
                            await _rollback_paid_updates(saved_backups)
                        except Exception:
                            logger.error("Rollback of paid updates failed", extra_tag="GSHEET")
                        raise

                if remote_paid_accept_notifications and manager.api is not None:
                    try:
                        await _send_remote_paid_notifications(
                            manager.api,
                            remote_paid_accept_notifications,
                            card_by_id,
                            users_by_id,
                            telegram_users_by_stable_id,
                        )
                    except Exception as exc:
                        logger.error(
                            f"Failed to send remote paid notifications ({type(exc).__name__}: {exc!r})",
                            extra_tag="GSHEET",
                            exc_info=exc,
                        )

            if snapshot_manager is None:
                await _apply_remote_paid_updates()
            else:
                async with snapshot_manager.pending_snapshot(
                    reason="Remote Paid Import from Google Sheets",
                    context="gsheet_remote_paid_import",
                    collections=("user_debts", "payments"),
                    save_in_background=True,
                ):
                    await _apply_remote_paid_updates()

            logger.debug(
                f"Remote paid import applied: debts_updated={len(saved_backups)} payments_created={len(created_remote_payments)}",
                extra_tag="GSHEET",
            )

        payload_tasks = []
        for card in cards:
            purchaser_obj: Optional[PassiveUser]
            purchaser = card.purchaser
            if isinstance(purchaser, BeanieLink):
                purchaser_obj = users_by_id.get(purchaser.ref.id)
            else:
                purchaser_obj = purchaser

            payload_tasks.append(
                _build_payload_for_card(
                    card,
                    purchaser=purchaser_obj,
                    now_iso=now_iso,
                    debts_by_stable_id=debts_by_card_and_stable_id.get(str(card.id), {}),
                )
            )

        logger.debug(f"Building payloads: count={len(payload_tasks)}")
        payloads = await asyncio.gather(*payload_tasks)

        elapsed_snapshot = time.perf_counter() - start_ts
        logger.debug(f"Payloads built: count={len(payloads)} elapsed_s={elapsed_snapshot:.2f}")

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                manager.gsheet_executor,
                _write_payloads_to_gsheet,
                manager,
                payloads,
                force_sheet_refresh_titles,
            )
        except Exception as exc:
            if saved_backups or created_remote_payments:
                logger.error(
                    f"Gsheet export failed after DB updates; rolling back paid updates ({type(exc).__name__}: {exc!r})",
                    extra_tag="GSHEET",
                    exc_info=exc,
                )
                try:
                    await _rollback_created_payments(created_remote_payments)
                except Exception:
                    logger.error("Rollback of created payments failed", extra_tag="GSHEET")
                try:
                    await _rollback_paid_updates(saved_backups)
                except Exception:
                    logger.error("Rollback of paid updates failed", extra_tag="GSHEET")

            manager.last_exported_signature_by_worksheet.clear()
            manager.last_exported_worksheet_order.clear()
            manager.layout_signature_by_worksheet.clear()
            raise

        if mode == "action" and conflict_events:
            await _send_conflict_notifications(manager.api, conflict_events)

        total_elapsed = time.perf_counter() - start_ts
        logger.debug(f"Sync finished: elapsed_s={total_elapsed:.2f}")


async def sync_all_cards_once(
    *,
    mode: SyncMode = "manual",
    local_paid_changes: Optional[Sequence[LocalPaidAmountChange]] = None,
    skip_remote_import: bool = False,
) -> None:
    await get_gsheet_sync_manager().sync_all_cards_once(mode=mode, local_paid_changes=local_paid_changes, skip_remote_import=skip_remote_import)


async def _run_periodic_gsheet_sync_impl(manager: GsheetSyncManager, *, stop_event: asyncio.Event) -> None:
    logger.info("Starting periodic gsheet sync (admin-configured)", extra_tag="GSHEET")

    while not stop_event.is_set():
        try:
            settings = await AppSettings.find_one()
            if not settings:
                settings = AppSettings()
                await settings.insert()

            gsheet_settings = settings.gsheet
            enabled = bool(gsheet_settings.periodic_sync_enabled)
            interval_seconds = max(30, int(gsheet_settings.sync_period_minutes) * 60)
            logger.debug(
                f"Periodic sync settings loaded: enabled={enabled} interval_sec={interval_seconds}",
                extra_tag="GSHEET",
            )
        except Exception as exc:
            logger.error(
                f"Gsheet sync failed: Coffee Cards - failed to load gsheet settings ({type(exc).__name__}: {exc!r})",
                extra_tag="GSHEET",
                exc=exc,
            )
            enabled = bool(app_config.GSHEET_SYNC_ENABLED)
            interval_seconds = max(30, int(app_config.GSHEET_SYNC_INTERVAL_SECONDS))
            logger.debug(
                f"Fallback to env config: enabled={enabled} interval_sec={interval_seconds}",
                extra_tag="GSHEET",
            )

        if enabled:
            try:
                await manager.sync_all_cards_once(mode="periodic")
            except Exception as exc:
                logger.error(
                    f"Gsheet sync failed: Coffee Cards ({type(exc).__name__}: {exc!r})",
                    extra_tag="GSHEET",
                    exc=exc,
                )

        try:
            # When disabled, still poll periodically so enabling it via admin UI works without restart.
            sleep_seconds = interval_seconds if enabled else 30
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_seconds)
        except TimeoutError:
            continue


async def run_periodic_gsheet_sync(*, stop_event: asyncio.Event) -> None:
    await get_gsheet_sync_manager().run_periodic_gsheet_sync(stop_event=stop_event)


async def _send_remote_paid_notifications(
    api: GsheetSyncApi,
    remote_paid_accept_notifications: Sequence[Tuple[UserDebt, float]],
    card_by_id: Dict[str, CoffeeCard],
    users_by_id: Dict[Any, PassiveUser],
    telegram_users_by_stable_id: Dict[str, TelegramUser],
) -> None:
    """Send notifications to debtor and creditor when remote paid is accepted."""

    if not remote_paid_accept_notifications or not api:
        return

    try:
        admin_ids_raw = await api.repo.get_registered_admins()
    except Exception:
        admin_ids_raw = []

    admin_ids: List[int] = []
    for admin_id in admin_ids_raw or []:
        try:
            admin_ids.append(int(admin_id))
        except Exception:
            continue

    for debt, amount in remote_paid_accept_notifications:
        # fetch_all_links() fails for cross-collection polymorphic links: debt.debtor is typed as
        # Link[PassiveUser] but lives in the telegram_users collection, so Beanie queries the wrong
        # collection and gets nothing back. We already have everything in the pre-built dicts —
        # just pull by ID directly from the BeanieLink ref.
        card = card_by_id.get(str(debt.coffee_card.ref.id))  # type: ignore[union-attr]
        debtor_target = users_by_id.get(debt.debtor.ref.id)  # type: ignore[union-attr]
        creditor_target = users_by_id.get(debt.creditor.ref.id)  # type: ignore[union-attr]

        # Resolve to TelegramUser — only telegram users can receive bot notifications.
        if not isinstance(debtor_target, TelegramUser):
            debtor_target = telegram_users_by_stable_id.get(debtor_target.stable_id) if debtor_target else None
        if not isinstance(creditor_target, TelegramUser):
            creditor_target = telegram_users_by_stable_id.get(creditor_target.stable_id) if creditor_target else None

        if not card:
            logger.warning("Skipping remote paid notification: could not resolve card", extra_tag="GSHEET")
            continue

        debtor_name = debtor_target.display_name if debtor_target else "Unknown"
        creditor_name = creditor_target.display_name if creditor_target else "Unknown"
        card_name = card.name

        # amount is the delta (new_paid - old_paid); debt.paid_amount is already the updated value.
        paid_after = float(debt.paid_amount or 0.0)
        paid_before = max(0.0, paid_after - float(amount))
        remaining = max(0.0, float(debt.total_amount or 0.0) - paid_after)
        remaining_text = f"**€{remaining:.2f}**" if remaining > 1e-9 else "none — fully settled! 🎉"

        directly_notified: Set[int] = set()

        if isinstance(debtor_target, TelegramUser):
            debtor_text = (
                "💸 **Payment registered from spreadsheet**\n\n"
                f"Card: **{card_name}**\n"
                f"Paid amount: **€{paid_before:.2f} → €{paid_after:.2f}** (+€{float(amount):.2f})\n"
                f"Remaining: {remaining_text}\n\n"
                f"Your debt to **{creditor_name}** has been updated."
            )
            try:
                logger.debug(
                    f"Sending remote paid notification to debtor: user_id={debtor_target.user_id} amount={float(amount):.2f}",
                    extra_tag="GSHEET",
                )
                await api.message_manager.send_user_notification(
                    int(debtor_target.user_id),
                    debtor_text,
                )
                directly_notified.add(int(debtor_target.user_id))
            except Exception as exc:
                logger.warning(
                    f"Failed to send remote paid notification to debtor (user_id={debtor_target.user_id}): {exc!r}",
                    extra_tag="GSHEET",
                )

        if isinstance(creditor_target, TelegramUser):
            creditor_text = (
                "✅ **Payment received (via spreadsheet)**\n\n"
                f"From: **{debtor_name}**\n"
                f"Card: **{card_name}**\n"
                f"Paid amount: **€{paid_before:.2f} → €{paid_after:.2f}** (+€{float(amount):.2f})\n"
                f"Remaining: {remaining_text}"
            )
            try:
                logger.debug(
                    f"Sending remote paid notification to creditor: user_id={creditor_target.user_id} amount={float(amount):.2f}",
                    extra_tag="GSHEET",
                )
                await api.message_manager.send_user_notification(
                    int(creditor_target.user_id),
                    creditor_text,
                )
                directly_notified.add(int(creditor_target.user_id))
            except Exception as exc:
                logger.warning(
                    f"Failed to send remote paid notification to creditor (user_id={creditor_target.user_id}): {exc!r}",
                    extra_tag="GSHEET",
                )

        for admin_id in admin_ids:
            if admin_id in directly_notified:
                continue
            admin_text = (
                "✅ **GSheet payment update accepted**\n\n"
                f"Card: **{card_name}**\n"
                f"Debtor: **{debtor_name}**\n"
                f"Paid amount: **€{paid_before:.2f} → €{paid_after:.2f}** (+€{float(amount):.2f})\n"
                f"Remaining: {remaining_text}"
            )
            try:
                await api.message_manager.send_user_notification(int(admin_id), admin_text)
            except Exception as exc:
                logger.warning(
                    f"Failed to send remote paid notification to admin (user_id={admin_id}): {exc!r}",
                    extra_tag="GSHEET",
                )
