from google.oauth2 import service_account
import gspread
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime

from ..config import settings
from ..common.log import (
    log_gsheet_sync_started, 
    log_gsheet_sync_completed, 
    log_gsheet_sync_failed,
    log_gsheet_api_initialized,
    log_gsheet_api_initialization_failed,
    log_gsheet_worksheet_created,
    log_gsheet_backup_completed,
    log_gsheet_backup_failed,
    log_gsheet_summary_created,
    log_gsheet_summary_creation_failed
)

credentials_info = {
    "type": "service_account",
    "project_id": settings.PROJECT_ID,
    "private_key": settings.SERVICE_ACCOUNT_PRIVATE_KEY,
    "client_email": settings.SERVICE_ACCOUNT_EMAIL,
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
}


class GsheetAPI:
    def __init__(self):
        try:
            credentials = service_account.Credentials.from_service_account_info(credentials_info)
            self.client = gspread.authorize(credentials)
            self.spreadsheet = self.client.open_by_key(settings.GSHEET_SSID)
            log_gsheet_api_initialized()
        except Exception as e:
            log_gsheet_api_initialization_failed(str(e))
            raise

    def _get_or_create_worksheet(self, title: str) -> gspread.Worksheet:
        """Get a worksheet by title or create it if it doesn't exist."""
        try:
            worksheet = self.spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            worksheet = self.spreadsheet.add_worksheet(title=title, rows=1000, cols=20)
            log_gsheet_worksheet_created(title)
        return worksheet
    
    async def sync_users_to_sheet(self, users: List[Dict[str, Any]]) -> bool:
        """Sync user data to Google Sheets."""
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
            
            log_gsheet_sync_completed("Users", len(users))
            return True
            
        except Exception as e:
            log_gsheet_sync_failed("Users", str(e))
            return False
    
    async def sync_coffee_cards_to_sheet(self, cards: List[Dict[str, Any]]) -> bool:
        """Sync coffee card data to Google Sheets."""
        log_gsheet_sync_started("Coffee Cards")
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
            
            log_gsheet_sync_completed("Coffee Cards", len(cards))
            return True
            
        except Exception as e:
            log_gsheet_sync_failed("Coffee Cards", str(e))
            return False
    
    
    async def sync_coffee_orders_to_sheet(self, orders: List[Dict[str, Any]]) -> bool:
        """Sync coffee order data to Google Sheets."""
        log_gsheet_sync_started("Coffee Orders")
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
            
            log_gsheet_sync_completed("Coffee Orders", len(orders))
            return True
            
        except Exception as e:
            log_gsheet_sync_failed("Coffee Orders", str(e))
            return False
    
    async def sync_debts_to_sheet(self, debts: List[Dict[str, Any]]) -> bool:
        """Sync debt data to Google Sheets."""
        log_gsheet_sync_started("User Debts")
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
            
            log_gsheet_sync_completed("Debts", len(debts))
            return True
            
        except Exception as e:
            log_gsheet_sync_failed("Debts", str(e))
            return False
    
    async def sync_payments_to_sheet(self, payments: List[Dict[str, Any]]) -> bool:
        """Sync payment data to Google Sheets."""
        log_gsheet_sync_started("Payments")
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
            
            log_gsheet_sync_completed("Payments", len(payments))
            return True
            
        except Exception as e:
            log_gsheet_sync_failed("Payments", str(e))
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
            
            log_gsheet_summary_created()
            return True
            
        except Exception as e:
            log_gsheet_summary_creation_failed(str(e))
            return False
    
    async def backup_all_data(self, data: Dict[str, Any]) -> bool:
        """Backup all data to Google Sheets."""
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
            
            log_gsheet_backup_completed(success_count, total_operations)
            return success_count == total_operations
            
        except Exception as e:
            log_gsheet_backup_failed(str(e))
            return False