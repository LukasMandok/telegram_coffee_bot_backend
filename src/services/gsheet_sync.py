from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from beanie import Link as BeanieLink
from gspread.utils import ValueInputOption

from src.api.gsheet_api import GsheetAPI
from src.common.log import Logger, log_gsheet_sync_failed, log_gsheet_sync_started
from src.config import app_config
from src.models.coffee_models import CoffeeCard, UserDebt
from src.models.beanie_models import PassiveUser, TelegramUser
from src.models.beanie_models import AppSettings


logger = Logger("GsheetSync")


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


def _format_percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.00%"
    return f"{(numerator / denominator) * 100:.2f}%"


def _calculate_missing_coffee_corrections(
    *,
    card: CoffeeCard,
    correction_method: str,
    correction_threshold: int,
) -> Dict[str, float]:
    """Return mapping stable_id -> correction amount for this card."""
    remaining_coffees = max(0, int(card.remaining_coffees))
    if remaining_coffees <= 0:
        return {}

    remaining_cost = float(remaining_coffees) * float(card.cost_per_coffee)

    correction_method = (correction_method or "absolute").strip().lower()

    eligible_coffees: Dict[str, int] = {}
    for stable_id, stats in card.consumer_stats.items():
        if int(stats.total_coffees or 0) <= 0:
            continue
        if int(stats.total_coffees or 0) >= int(correction_threshold):
            eligible_coffees[stable_id] = int(stats.total_coffees or 0)

    if not eligible_coffees:
        return {}

    if correction_method == "absolute":
        per_user = remaining_cost / len(eligible_coffees)
        return {stable_id: per_user for stable_id in eligible_coffees.keys()}

    if correction_method == "proportional":
        total_eligible_coffees = sum(eligible_coffees.values())
        if total_eligible_coffees <= 0:
            return {}
        return {
            stable_id: remaining_cost * (coffees / total_eligible_coffees)
            for stable_id, coffees in eligible_coffees.items()
        }

    return {}


async def _build_payload_for_card(card: CoffeeCard) -> CardSheetPayload:
    await card.fetch_link("purchaser")
    purchaser = card.purchaser  # type: ignore

    purchaser_stable_id = getattr(purchaser, "stable_id", "")
    purchaser_name = getattr(purchaser, "display_name", "")
    paypal_link = getattr(purchaser, "paypal_link", None) or ""

    total_consumed = sum(max(0, stats.total_coffees) for stats in card.consumer_stats.values())

    # Calculate expected corrections for active cards if no debts exist.
    # Uses the same settings source as the DebtManager (AppSettings.debt).
    app_settings = await AppSettings.find_one()
    if not app_settings:
        app_settings = AppSettings()
        await app_settings.insert()
    correction_method = app_settings.debt.correction_method
    correction_threshold = int(app_settings.debt.correction_threshold)
    corrections_by_user = _calculate_missing_coffee_corrections(
        card=card,
        correction_method=correction_method,
        correction_threshold=correction_threshold,
    )

    # Prefer persisted debts (corrections/paid amounts) whenever they exist.
    # Even for active cards, debt docs can exist (e.g. previews/manual updates),
    # so we always try to load them.
    debts_by_stable_id: Dict[str, UserDebt] = {}
    debts = await UserDebt.find(UserDebt.coffee_card == card, fetch_links=True).to_list()  # type: ignore
    for debt in debts:
        debtor = debt.debtor  # type: ignore
        if isinstance(debtor, BeanieLink):
            debtor = await PassiveUser.get(debtor.ref.id) or await TelegramUser.get(debtor.ref.id)

        stable_id = getattr(debtor, "stable_id", "")
        if stable_id:
            debts_by_stable_id[stable_id] = debt

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
                # Active cards (no debts yet) or missing debt record
                correction = float(corrections_by_user.get(stable_id, 0.0))
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
        updated_at_iso=datetime.now().isoformat(timespec="seconds"),
        rows=user_rows,
    )


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


def _find_existing_chart_ids(spreadsheet: Any, sheet_id: int) -> List[int]:
    """Return chart IDs already attached to the given sheet."""
    metadata = spreadsheet.fetch_sheet_metadata()
    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("sheetId") == sheet_id:
            charts = sheet.get("charts", [])
            return [int(c.get("chartId")) for c in charts if c.get("chartId") is not None]
    return []


def _find_existing_protected_range_ids(spreadsheet: Any, sheet_id: int) -> List[int]:
    """Return protectedRange IDs already attached to the given sheet."""
    metadata = spreadsheet.fetch_sheet_metadata()
    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("sheetId") == sheet_id:
            protected_ranges = sheet.get("protectedRanges", [])
            return [int(r.get("protectedRangeId")) for r in protected_ranges if r.get("protectedRangeId") is not None]
    return []


def _apply_layout_chart_and_protection(*, api: GsheetAPI, worksheet: Any, payload: CardSheetPayload) -> None:
    sheet_id = int(worksheet._properties.get("sheetId"))

    data_row_count = len(payload.rows)

    # Delete existing charts on this worksheet to avoid duplicates.
    delete_chart_requests = [
        {"deleteEmbeddedObject": {"objectId": chart_id}}
        for chart_id in _find_existing_chart_ids(api.spreadsheet, sheet_id)
    ]

    # Delete existing protections on this worksheet to avoid duplicates.
    delete_protection_requests = [
        {"deleteProtectedRange": {"protectedRangeId": pr_id}}
        for pr_id in _find_existing_protected_range_ids(api.spreadsheet, sheet_id)
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

    for payload in payloads_sorted:
        worksheet = api._get_or_create_worksheet(payload.worksheet_title)
        worksheet.clear()
        grid = _payload_to_grid(payload)
        if not grid:
            continue

        worksheet.update(values=grid, range_name="A1", value_input_option=ValueInputOption.user_entered)

        # Apply layout styling, chart and protection after values exist
        _apply_layout_chart_and_protection(api=api, worksheet=worksheet, payload=payload)

    _reorder_card_worksheets(api=api, card_titles=[p.worksheet_title for p in payloads_sorted])


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
    log_gsheet_sync_started("Coffee Cards")

    cards = await CoffeeCard.find(fetch_links=False).to_list()
    payloads: List[CardSheetPayload] = []

    for card in cards:
        payloads.append(await _build_payload_for_card(card))

    await asyncio.to_thread(_write_payloads_to_gsheet, payloads)


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
