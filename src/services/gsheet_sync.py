from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Set

from beanie import Link as BeanieLink
from gspread.utils import ValueInputOption

from src.api.gsheet_api import GsheetAPI
from src.common.log import Logger, log_gsheet_sync_failed, log_gsheet_sync_started
from src.config import app_config
from src.models.coffee_models import CoffeeCard, UserDebt
from src.models.beanie_models import PassiveUser, TelegramUser
from src.models.beanie_models import AppSettings


logger = Logger("GsheetSync")


# In-process cache of the last exported snapshot, keyed by worksheet title.
# This is intentionally not persisted: it speeds up periodic sync runs and
# reduces unnecessary Google Sheets API calls.
_LAST_EXPORTED_SIGNATURE_BY_WORKSHEET: Dict[str, str] = {}
_LAST_EXPORTED_WORKSHEET_ORDER: List[str] = []


# Keep all Google Sheets operations on a single worker thread.
# This allows caching the GsheetAPI client (and avoids re-initializing it)
# while keeping the async event loop responsive.
_GSHEET_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gsheet")

# Prevent overlapping syncs in-process (e.g. periodic sync + manual /sync)
# from writing older snapshots after newer ones.
_SYNC_LOCK = asyncio.Lock()


# Debounced background sync task for action-triggered syncs.
_ACTION_SYNC_TASK: Optional[asyncio.Task[None]] = None


async def warmup_gsheet_api() -> None:
    """Initialize the Google Sheets client early (startup warmup)."""
    loop = asyncio.get_running_loop()

    def _warm() -> None:
        api = GsheetAPI()
        # One metadata call to validate access and warm up HTTP/session.
        _ = api.spreadsheet.fetch_sheet_metadata()

    await loop.run_in_executor(_GSHEET_EXECUTOR, _warm)


def request_gsheet_sync_after_action(*, reason: str) -> None:
    """Request a background one-shot sync after a state-changing action.

    This is intentionally fire-and-forget and debounced (only one outstanding task).
    """
    global _ACTION_SYNC_TASK

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (e.g., called from a sync script context). Ignore.
        return

    if _ACTION_SYNC_TASK is not None and not _ACTION_SYNC_TASK.done():
        logger.debug(f"Action-triggered sync already running; skip (reason={reason})")
        return

    _ACTION_SYNC_TASK = loop.create_task(_run_action_triggered_sync(reason=reason))


async def _run_action_triggered_sync(*, reason: str) -> None:
    try:
        settings_doc = await AppSettings.find_one()
        gsheet_settings = settings_doc.gsheet if settings_doc else AppSettings().gsheet

        if not bool(getattr(gsheet_settings, "sync_after_actions_enabled", True)):
            logger.debug(f"Sync-after-actions disabled; skip (reason={reason})")
            return

        logger.info(f"Starting action-triggered Google Sheets sync (reason={reason})")
        await sync_all_cards_once()
        logger.info(f"Finished action-triggered Google Sheets sync (reason={reason})")
    except Exception as exc:
        logger.error(
            f"Action-triggered Google Sheets sync failed (reason={reason}): {type(exc).__name__}: {exc!r}",
            exc_info=exc,
        )


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


def _delete_orphaned_card_worksheets(*, api: GsheetAPI, existing_titles: Set[str], desired_titles: Set[str]) -> None:
    orphaned = sorted(t for t in existing_titles if _is_card_worksheet_title(t) and t not in desired_titles)
    if not orphaned:
        return

    deleted = 0
    for title in orphaned:
        try:
            ws = api.spreadsheet.worksheet(title)
            api.spreadsheet.del_worksheet(ws)
            _LAST_EXPORTED_SIGNATURE_BY_WORKSHEET.pop(title, None)
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
    purchaser: Optional[object],
    now_iso: str,
    debts_by_stable_id: Dict[str, UserDebt],
) -> CardSheetPayload:
    purchaser_stable_id = getattr(purchaser, "stable_id", "") if purchaser else ""
    purchaser_name = getattr(purchaser, "display_name", "") if purchaser else ""
    paypal_link = (getattr(purchaser, "paypal_link", None) if purchaser else None) or ""

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
    header_row = 5
    first_data_row = 6

    grid: List[List[Any]] = []

    # Row 1: Title
    grid.append([payload.card_name, "", "", "", "", "", "", ""])

    # Row 2-3: Meta info
    grid.append(["Status", status, "", "Last updated", payload.updated_at_iso, "", "Purchaser", payload.purchaser_name])
    grid.append(["Total coffees", payload.total_coffees, "", "Coffees left", payload.remaining_coffees, "", "PayPal", payload.paypal_link])
    grid.append(["Cost/coffee", payload.cost_per_coffee, "", "Total cost", payload.total_cost, "", "", ""])

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
            ]
        )

    return grid


def _apply_layout_chart_and_protection(
    *,
    api: GsheetAPI,
    worksheet: Any,
    payload: CardSheetPayload,
    chart_ids_by_sheet_id: Dict[int, List[int]],
    protected_range_ids_by_sheet_id: Dict[int, List[int]],
) -> None:
    sheet_id = int(worksheet._properties.get("sheetId"))

    data_row_count = len(payload.rows)

    # Delete existing charts on this worksheet to avoid duplicates.
    delete_chart_requests = [
        {"deleteEmbeddedObject": {"objectId": chart_id}}
        for chart_id in chart_ids_by_sheet_id.get(sheet_id, [])
    ]

    # Delete existing protections on this worksheet to avoid duplicates.
    delete_protection_requests = [
        {"deleteProtectedRange": {"protectedRangeId": pr_id}}
        for pr_id in protected_range_ids_by_sheet_id.get(sheet_id, [])
    ]

    # Format ranges (0-based indices)
    header_row_index = 4  # row 5
    title_row_index = 0

    first_data_row_index = 5  # row 6
    last_data_row_index = first_data_row_index + max(0, data_row_count)

    currency_cols = [3, 4, 5, 6, 7]  # D-H
    percent_col = 2  # C

    requests: List[Dict[str, Any]] = []

    requests.append(
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {
                        "frozenRowCount": 5,
                        "frozenColumnCount": 1,
                    },
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        }
    )

    # Bold title row
    requests.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": title_row_index,
                    "endRowIndex": title_row_index + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 8,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}},
                "fields": "userEnteredFormat.textFormat",
            }
        }
    )

    # Bold header row
    requests.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": header_row_index,
                    "endRowIndex": header_row_index + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 8,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        }
    )

    # Percent format for fraction column (data rows only)
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
        ]
    )

    # Add a pie chart to the right of the table
    if data_row_count > 0:
        first_row = 5  # row 6 (0-based)
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
                                "rowIndex": 4,
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

    # Protect the sheet so only one column is editable.
    # - Active card: allow editing Coffees column (B)
    # - Completed card: allow editing Paid column (G)
    first_data_row_index = 5  # row 6 (0-based)
    editable_col = 1 if payload.is_active else 6

    # Protect meta + header rows fully (rows 1-5).
    requests.append(
        {
            "addProtectedRange": {
                "protectedRange": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": first_data_row_index,
                        "startColumnIndex": 0,
                        "endColumnIndex": 8,
                    },
                    "warningOnly": False,
                    "description": "Lock header/meta",
                }
            }
        }
    )

    # Protect everything below the data block (blank + totals rows), fully.
    if data_row_count > 0:
        requests.append(
            {
                "addProtectedRange": {
                    "protectedRange": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": first_data_row_index + data_row_count,
                            "startColumnIndex": 0,
                            "endColumnIndex": 8,
                        },
                        "warningOnly": False,
                        "description": "Lock totals",
                    }
                }
            }
        )

        # Protect data rows: all columns except the editable one.
        if editable_col > 0:
            requests.append(
                {
                    "addProtectedRange": {
                        "protectedRange": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": first_data_row_index,
                                "endRowIndex": first_data_row_index + data_row_count,
                                "startColumnIndex": 0,
                                "endColumnIndex": editable_col,
                            },
                            "warningOnly": False,
                            "description": "Lock left columns",
                        }
                    }
                }
            )

        if editable_col + 1 < 8:
            requests.append(
                {
                    "addProtectedRange": {
                        "protectedRange": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": first_data_row_index,
                                "endRowIndex": first_data_row_index + data_row_count,
                                "startColumnIndex": editable_col + 1,
                                "endColumnIndex": 8,
                            },
                            "warningOnly": False,
                            "description": "Lock right columns",
                        }
                    }
                }
            )

        # Extra safety: lock the purchaser row entirely (they should not get debt/paid edits).
        for idx, row in enumerate(payload.rows):
            if not row.is_purchaser:
                continue
            purchaser_row = first_data_row_index + idx
            requests.append(
                {
                    "addProtectedRange": {
                        "protectedRange": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": purchaser_row,
                                "endRowIndex": purchaser_row + 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": 8,
                            },
                            "warningOnly": False,
                            "description": "Lock purchaser row",
                        }
                    }
                }
            )

    batch = {"requests": delete_chart_requests + delete_protection_requests + requests}
    api.spreadsheet.batch_update(batch)


def _write_payloads_to_gsheet(payloads: List[CardSheetPayload]) -> None:
    api = GsheetAPI()

    payloads_sorted = sorted(payloads, key=lambda p: p.created_at, reverse=True)

    metadata = api.spreadsheet.fetch_sheet_metadata()
    existing_titles: Set[str] = {
        str(s.get("properties", {}).get("title"))
        for s in metadata.get("sheets", [])
        if s.get("properties", {}).get("title") is not None
    }

    chart_ids_by_sheet_id: Dict[int, List[int]] = {}
    protected_range_ids_by_sheet_id: Dict[int, List[int]] = {}
    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        sheet_id = props.get("sheetId")
        if sheet_id is None:
            continue

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
        cached = _LAST_EXPORTED_SIGNATURE_BY_WORKSHEET.get(payload.worksheet_title)
        if payload.worksheet_title in existing_titles and cached == signature:
            logger.debug(f"Skip worksheet (cache hit): title={payload.worksheet_title}")
            logger.trace(f"Cache signature: title={payload.worksheet_title} sig={signature}")
            skipped_count += 1
            continue

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

        # Apply layout styling, chart and protection after values exist
        _apply_layout_chart_and_protection(
            api=api,
            worksheet=worksheet,
            payload=payload,
            chart_ids_by_sheet_id=chart_ids_by_sheet_id,
            protected_range_ids_by_sheet_id=protected_range_ids_by_sheet_id,
        )

        _LAST_EXPORTED_SIGNATURE_BY_WORKSHEET[payload.worksheet_title] = signature
        updated_count += 1

    card_titles = [p.worksheet_title for p in payloads_sorted]

    _delete_orphaned_card_worksheets(
        api=api,
        existing_titles=existing_titles,
        desired_titles=set(card_titles),
    )

    global _LAST_EXPORTED_WORKSHEET_ORDER
    if card_titles != _LAST_EXPORTED_WORKSHEET_ORDER:
        _reorder_card_worksheets(api=api, card_titles=card_titles)
        _LAST_EXPORTED_WORKSHEET_ORDER = card_titles

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


async def sync_all_cards_once() -> None:
    async with _SYNC_LOCK:
        log_gsheet_sync_started("Coffee Cards")

        start_ts = time.perf_counter()
        logger.debug("Sync snapshot: start")

    # Snapshot all DB data up-front so the export isn't influenced by concurrent updates.
        now_iso = datetime.now().isoformat(timespec="seconds")

        cards = await CoffeeCard.find(fetch_links=False).to_list()

        logger.debug(f"Snapshot loaded: cards={len(cards)}")

        purchaser_ids: Set[Any] = set()
        card_ids: List[Any] = []
        for card in cards:
            card_ids.append(card.id)
            purchaser = getattr(card, "purchaser", None)
            if isinstance(purchaser, BeanieLink):
                purchaser_ids.add(purchaser.ref.id)

    # Load all debts for all cards in one query.
        debts = (
            await UserDebt.find({"coffee_card.$id": {"$in": card_ids}}, fetch_links=False).to_list()  # type: ignore[arg-type]
            if card_ids
            else []
        )

        logger.debug(f"Snapshot loaded: debts={len(debts)}")

        debtor_ids: Set[Any] = set()
        for debt in debts:
            debtor = getattr(debt, "debtor", None)
            if isinstance(debtor, BeanieLink):
                debtor_ids.add(debtor.ref.id)

    # Resolve purchaser + debtor stable_ids in bulk.
        user_ids = list(set(purchaser_ids) | debtor_ids)
        passive_users = await PassiveUser.find({"_id": {"$in": user_ids}}).to_list() if user_ids else []
        telegram_users = await TelegramUser.find({"_id": {"$in": user_ids}}).to_list() if user_ids else []

        logger.debug(
            f"Snapshot loaded: users_total={len(user_ids)} passive={len(passive_users)} telegram={len(telegram_users)}"
        )

        users_by_id: Dict[Any, object] = {}
        stable_id_by_id: Dict[Any, str] = {}
        for user in [*passive_users, *telegram_users]:
            user_id = getattr(user, "id", None)
            if user_id is None:
                continue
            users_by_id[user_id] = user
            stable_id_by_id[user_id] = str(getattr(user, "stable_id", ""))

        debts_by_card_and_stable_id: Dict[str, Dict[str, UserDebt]] = {}
        for debt in debts:
            card_link = getattr(debt, "coffee_card", None)
            debtor_link = getattr(debt, "debtor", None)
            if not isinstance(card_link, BeanieLink) or not isinstance(debtor_link, BeanieLink):
                continue

            card_id = str(card_link.ref.id)
            debtor_id = debtor_link.ref.id
            stable_id = stable_id_by_id.get(debtor_id, "")
            if not stable_id:
                continue

            debts_by_card_and_stable_id.setdefault(card_id, {})[stable_id] = debt

        logger.trace(
            f"Snapshot indexed: cards_with_debts={len(debts_by_card_and_stable_id)}",
        )

        payload_tasks = []
        for card in cards:
            purchaser_obj: Optional[object] = None
            purchaser = getattr(card, "purchaser", None)
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
        await loop.run_in_executor(_GSHEET_EXECUTOR, _write_payloads_to_gsheet, payloads)

        total_elapsed = time.perf_counter() - start_ts
        logger.debug(f"Sync finished: elapsed_s={total_elapsed:.2f}")


async def run_periodic_gsheet_sync(*, stop_event: asyncio.Event) -> None:
    logger.info("Starting periodic gsheet sync (admin-configured)")

    while not stop_event.is_set():
        try:
            settings = await AppSettings.find_one()
            if not settings:
                settings = AppSettings()
                await settings.insert()

            gsheet_settings = settings.gsheet
            enabled = bool(gsheet_settings.periodic_sync_enabled)
            interval_seconds = max(30, int(gsheet_settings.sync_period_minutes) * 60)
        except Exception as exc:
            # Fallback to env-based defaults if DB/settings are unavailable
            log_gsheet_sync_failed("Coffee Cards", f"Failed to load gsheet settings: {type(exc).__name__}: {exc!r}")
            enabled = bool(getattr(app_config, "GSHEET_SYNC_ENABLED", False))
            interval_seconds = max(30, int(getattr(app_config, "GSHEET_SYNC_INTERVAL_SECONDS", 600)))

        if enabled:
            try:
                await sync_all_cards_once()
            except Exception as exc:
                log_gsheet_sync_failed("Coffee Cards", f"{type(exc).__name__}: {exc!r}")

        try:
            # When disabled, still poll periodically so enabling it via admin UI works without restart.
            sleep_seconds = interval_seconds if enabled else 30
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_seconds)
        except TimeoutError:
            continue
