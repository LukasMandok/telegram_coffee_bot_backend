from google.oauth2 import service_account
import gspread
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from gspread.utils import ValueInputOption
import threading

from ..config import app_config
from ..common.log import Logger


logger = Logger("GsheetAPI")

credentials_info = {
    "type": "service_account",
    "project_id": app_config.PROJECT_ID,
    "private_key": app_config.SERVICE_ACCOUNT_PRIVATE_KEY,
    "client_email": app_config.SERVICE_ACCOUNT_EMAIL,
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
}


GSHEET_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GsheetAPI:
    _instance: Optional["GsheetAPI"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        # Singleton pattern similar to BeanieRepository: one instance per process.
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False  # type: ignore[attr-defined]
        return cls._instance

    def __init__(self):
        # Avoid re-initialization when GsheetAPI() is called multiple times.
        if getattr(self, "_initialized", False):
            return
        self.logger = logger
        try:
            credentials = service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=GSHEET_SCOPES,
            )
            self.client = gspread.authorize(credentials)
            self.spreadsheet = self.client.open_by_key(app_config.GSHEET_SSID)
            self.logger.info("Google Sheets API initialized", extra_tag="GSHEET")
            self._initialized = True
        except Exception as e:
            self.logger.error("Google Sheets API initialization failed", extra_tag="GSHEET", exc=e)
            raise

    def ping(self) -> Dict[str, Any]:
        """Validate credentials + spreadsheet access.

        Returns basic spreadsheet metadata without performing any writes.
        """
        spreadsheet = self.spreadsheet
        worksheets = spreadsheet.worksheets()
        return {
            "spreadsheet_title": spreadsheet.title,
            "spreadsheet_id": spreadsheet.id,
            "worksheet_titles": [ws.title for ws in worksheets],
        }

    def append_debug_row(self, message: str, worksheet_title: str = "Debug") -> Dict[str, Any]:
        """Append a single debug row to a worksheet.

        This is intended for smoke-testing write access.
        It does not clear or overwrite any existing data.
        """
        worksheet = self._get_or_create_worksheet(worksheet_title)

        values = [
            datetime.now().isoformat(timespec="seconds"),
            message,
        ]

        worksheet.append_row(values, value_input_option=ValueInputOption.user_entered)

        return {
            "worksheet_title": worksheet.title,
            "appended_values": values,
        }

    def _get_or_create_worksheet(self, title: str) -> gspread.Worksheet:
        """Get a worksheet by title or create it if it doesn't exist."""
        try:
            worksheet = self.spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            worksheet = self.spreadsheet.add_worksheet(title=title, rows=1000, cols=20)
            self.logger.info(f"Worksheet created (title={title})", extra_tag="GSHEET")
        return worksheet
    
    async def sync_users_to_sheet(self, users: List[Dict[str, Any]]) -> bool:
        """Sync user data to Google Sheets."""
        self.logger.debug("Sync started: Users", extra_tag="GSHEET")
        try:
            worksheet = self._get_or_create_worksheet("Users")
            
            # Set headers if worksheet is empty
            if not worksheet.get_all_records():
                headers = [
                    "User ID", "Username", "First Name", "Last Name", 
                    "Phone", "Created At", "Last Login", "Is Admin"
                ]
                worksheet.insert_row(headers, 1)
            
            # Clear existing data (except headers)
            worksheet.clear()
            
            # Insert headers
            headers = [
                "User ID", "Username", "First Name", "Last Name", 
                "Phone", "Created At", "Last Login", "Is Admin"
            ]
            worksheet.insert_row(headers, 1)
            
            # Insert user data
            for i, user in enumerate(users, start=2):
                row = [
                    user.get('id', ''),
                    user.get('username', ''),
                    user.get('first_name', ''),
                    user.get('last_name', ''),
                    user.get('phone', ''),
                    user.get('created_at', ''),
                    user.get('last_login', ''),
                    user.get('is_admin', False)
                ]
                worksheet.insert_row(row, i)

            self.logger.info(f"Sync completed: Users (records={len(users)})", extra_tag="GSHEET")
            return True
            
        except Exception as e:
            self.logger.error("Sync failed: Users", extra_tag="GSHEET", exc=e)
            return False
    
    async def sync_coffee_cards_to_sheet(self, cards: List[Dict[str, Any]]) -> bool:
        """Sync coffee card data to Google Sheets."""
        self.logger.debug("Sync started: Coffee Cards", extra_tag="GSHEET")
        try:
            worksheet = self._get_or_create_worksheet("Coffee Cards")
            
            # Clear and set headers
            worksheet.clear()
            headers = [
                "Card ID", "Name", "Total Coffees", "Remaining Coffees", 
                "Cost per Coffee", "Total Cost", "Purchaser", "Created At", "Is Active"
            ]
            worksheet.insert_row(headers, 1)
            
            # Insert card data
            for i, card in enumerate(cards, start=2):
                row = [
                    str(card.get('id', '')),
                    card.get('name', ''),
                    card.get('total_coffees', 0),
                    card.get('remaining_coffees', 0),
                    float(card.get('cost_per_coffee', 0)),
                    float(card.get('total_cost', 0)),
                    card.get('purchaser_name', ''),
                    str(card.get('created_at', '')),
                    card.get('is_active', True)
                ]
                worksheet.insert_row(row, i)
            
            self.logger.info(f"Sync completed: Coffee Cards (records={len(cards)})", extra_tag="GSHEET")
            return True
            
        except Exception as e:
            self.logger.error("Sync failed: Coffee Cards", extra_tag="GSHEET", exc=e)
            return False
    
    
    async def sync_coffee_orders_to_sheet(self, orders: List[Dict[str, Any]]) -> bool:
        """Sync coffee order data to Google Sheets."""
        self.logger.debug("Sync started: Coffee Orders", extra_tag="GSHEET")
        try:
            worksheet = self._get_or_create_worksheet("Coffee Orders")
            
            # Clear and set headers
            worksheet.clear()
            headers = [
                "Order ID", "Card Name", "Consumer", "Quantity", 
                "Order Date", "Cost", "Notes"
            ]
            worksheet.insert_row(headers, 1)
            
            # Insert order data
            for i, order in enumerate(orders, start=2):
                row = [
                    str(order.get('id', '')),
                    order.get('card_name', ''),
                    order.get('consumer_name', ''),
                    order.get('quantity', 0),
                    str(order.get('order_date', '')),
                    float(order.get('cost', 0)),
                    order.get('notes', '')
                ]
                worksheet.insert_row(row, i)
            
            self.logger.info(f"Sync completed: Coffee Orders (records={len(orders)})", extra_tag="GSHEET")
            return True
            
        except Exception as e:
            self.logger.error("Sync failed: Coffee Orders", extra_tag="GSHEET", exc=e)
            return False
    
    async def sync_debts_to_sheet(self, debts: List[Dict[str, Any]]) -> bool:
        """Sync debt data to Google Sheets."""
        self.logger.debug("Sync started: User Debts", extra_tag="GSHEET")
        try:
            worksheet = self._get_or_create_worksheet("User Debts")
            
            # Clear and set headers
            worksheet.clear()
            headers = [
                "Debt ID", "Debtor", "Creditor", "Amount", 
                "Coffee Card", "Created At", "Is Settled"
            ]
            worksheet.insert_row(headers, 1)
            
            # Insert debt data
            for i, debt in enumerate(debts, start=2):
                row = [
                    str(debt.get('id', '')),
                    debt.get('debtor_name', ''),
                    debt.get('creditor_name', ''),
                    float(debt.get('total_amount', 0)),
                    debt.get('card_name', ''),
                    str(debt.get('created_at', '')),
                    debt.get('is_settled', False)
                ]
                worksheet.insert_row(row, i)
            
            self.logger.info(f"Sync completed: Debts (records={len(debts)})", extra_tag="GSHEET")
            return True
            
        except Exception as e:
            self.logger.error("Sync failed: Debts", extra_tag="GSHEET", exc=e)
            return False
    
    async def sync_payments_to_sheet(self, payments: List[Dict[str, Any]]) -> bool:
        """Sync payment data to Google Sheets."""
        self.logger.debug("Sync started: Payments", extra_tag="GSHEET")
        try:
            worksheet = self._get_or_create_worksheet("Payments")
            
            # Clear and set headers
            worksheet.clear()
            headers = [
                "Payment ID", "Payer", "Recipient", "Amount", 
                "Payment Method", "Status", "Created At", "Completed At"
            ]
            worksheet.insert_row(headers, 1)
            
            # Insert payment data
            for i, payment in enumerate(payments, start=2):
                row = [
                    str(payment.get('id', '')),
                    payment.get('payer_name', ''),
                    payment.get('recipient_name', ''),
                    float(payment.get('amount', 0)),
                    payment.get('payment_method', ''),
                    payment.get('payment_status', ''),
                    str(payment.get('created_at', '')),
                    str(payment.get('completed_at', '') if payment.get('completed_at') else '')
                ]
                worksheet.insert_row(row, i)
            
            self.logger.info(f"Sync completed: Payments (records={len(payments)})", extra_tag="GSHEET")
            return True
            
        except Exception as e:
            self.logger.error("Sync failed: Payments", extra_tag="GSHEET", exc=e)
            return False
    
    async def create_summary_sheet(self, statistics: Dict[str, Any]) -> bool:
        """Create a summary sheet with overall statistics."""
        try:
            worksheet = self._get_or_create_worksheet("Summary")
            worksheet.clear()
            
            # Add title and timestamp
            worksheet.update('A1', f'Coffee Bot Summary - {datetime.now().strftime("%Y-%m-%d %H:%M")}')
            
            # Add statistics
            row = 3
            for category, stats in statistics.items():
                worksheet.update(f'A{row}', category.title())
                row += 1
                
                if isinstance(stats, dict):
                    for key, value in stats.items():
                        worksheet.update(f'B{row}', key.replace('_', ' ').title())
                        worksheet.update(f'C{row}', str(value))
                        row += 1
                else:
                    worksheet.update(f'B{row}', str(stats))
                    row += 1
                
                row += 1  # Empty row between categories
            
            self.logger.info("Summary sheet created", extra_tag="GSHEET")
            return True
            
        except Exception as e:
            self.logger.error("Summary sheet creation failed", extra_tag="GSHEET", exc=e)
            return False
    
    async def backup_all_data(self, data: Dict[str, Any]) -> bool:
        """Backup all data to Google Sheets."""
        self.logger.debug("Backup started", extra_tag="GSHEET")
        try:
            success_count = 0
            total_operations = 0
            
            if 'users' in data:
                total_operations += 1
                if await self.sync_users_to_sheet(data['users']):
                    success_count += 1
            
            if 'coffee_cards' in data:
                total_operations += 1
                if await self.sync_coffee_cards_to_sheet(data['coffee_cards']):
                    success_count += 1
            
            if 'coffee_orders' in data:
                total_operations += 1
                if await self.sync_coffee_orders_to_sheet(data['coffee_orders']):
                    success_count += 1
            
            if 'debts' in data:
                total_operations += 1
                if await self.sync_debts_to_sheet(data['debts']):
                    success_count += 1
            
            if 'payments' in data:
                total_operations += 1
                if await self.sync_payments_to_sheet(data['payments']):
                    success_count += 1
            
            if 'statistics' in data:
                total_operations += 1
                if await self.create_summary_sheet(data['statistics']):
                    success_count += 1
            
            self.logger.info(
                f"Backup completed (success={success_count}/{total_operations})",
                extra_tag="GSHEET",
            )
            return success_count == total_operations
            
        except Exception as e:
            self.logger.error("Backup failed", extra_tag="GSHEET", exc=e)
            return False