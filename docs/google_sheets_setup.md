# Google Sheets Setup (Optional)

This backend can optionally back up data into a Google Spreadsheet.

It uses a **Google Cloud Service Account** and the **Google Sheets API**.

## 1) Create a Google Cloud project

1. Go to https://console.cloud.google.com/
2. Create a new project (or choose an existing one).

## 2) Enable the Google Sheets API

1. In the project, go to **APIs & Services → Library**.
2. Search for **Google Sheets API**.
3. Click **Enable**.

## 3) Create a Service Account + key

1. Go to **APIs & Services → Credentials**.
2. Click **Create Credentials → Service account**.
3. Create the service account.
4. Open the service account details and go to **Keys**.
5. Click **Add key → Create new key**.
6. Choose **JSON** and download it.

You will need the following values from that JSON:
- `client_email`
- `private_key`
- `project_id`

## 4) Create / choose a spreadsheet and share it

1. Create a Google Spreadsheet in Google Drive.
2. Copy the spreadsheet ID from its URL:
   - Example: `https://docs.google.com/spreadsheets/d/<THIS_PART_IS_THE_ID>/edit...`
3. Share the spreadsheet with your service account email (`client_email`) as an **Editor**.

## 5) Configure environment variables

Set these variables in your `.env` (see `.env.example` for the full list):

- `GSHEET_SSID`: the spreadsheet ID
- `SERVICE_ACCOUNT_EMAIL`: the service account `client_email`
- `SERVICE_ACCOUNT_PRIVATE_KEY`: the `private_key`
- `PROJECT_ID`: the `project_id`

### Important: private key formatting

In `.env`, newlines must be encoded as `\n`. The app converts them back to real newlines.

Example:

```env
SERVICE_ACCOUNT_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\nMIIE...\n...\n-----END PRIVATE KEY-----\n
```

## 6) Quick verification

After setting env vars and starting the container/app:

- Check the app logs for Google Sheets related messages.
- If you see auth errors, re-check:
  - the spreadsheet is shared with the service account email
  - the key was copied correctly
  - `SERVICE_ACCOUNT_PRIVATE_KEY` contains `\n` sequences (not literal newlines)
