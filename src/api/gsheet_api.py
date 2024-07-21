from google.oauth2 import service_account
import gspread

from ..config import settings

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
        
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
        
        self.client = gspread.authorize(credentials)
        
        self.spreadsheet = self.client.open_by_key(settings.GSHEET_SSID) 
        self.worksheet = self.spreadsheet.sheet1
        
        worksheet.update('A1', 'Hello, World!')