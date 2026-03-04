# masjidal_to_drive

Automation pipeline starter:
1. Open website with Selenium
2. Login (if needed)
3. Download CSV
4. Clean CSV with pandas
5. Upload file to Google Drive folder (API)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run

```bash
python main.py
```

## Scheduled runs (macOS launchd)

- Primary run target: daily at `00:01 UTC` (GMT)
- Backup run target: daily at `12:00 UTC` (GMT), only if no successful primary run on the same UTC date
- `launchd` triggers hourly; `run_daily.sh` enforces the exact UTC time gate
- Log cleanup: rotates logs every 90 days

```bash
chmod +x run_daily.sh
mkdir -p logs
cp com.masjidal.to_drive.daily.plist ~/Library/LaunchAgents/
cp com.masjidal.to_drive.backup.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.masjidal.to_drive.daily.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.masjidal.to_drive.backup.plist
launchctl enable gui/$(id -u)/com.masjidal.to_drive.daily
launchctl enable gui/$(id -u)/com.masjidal.to_drive.backup
launchctl kickstart -k gui/$(id -u)/com.masjidal.to_drive.daily
launchctl kickstart -k gui/$(id -u)/com.masjidal.to_drive.backup
```

### Useful commands

```bash
launchctl kickstart -k gui/$(id -u)/com.masjidal.to_drive.daily
launchctl kickstart -k gui/$(id -u)/com.masjidal.to_drive.backup
launchctl print gui/$(id -u)/com.masjidal.to_drive.daily
launchctl print gui/$(id -u)/com.masjidal.to_drive.backup
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.masjidal.to_drive.daily.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.masjidal.to_drive.backup.plist
```

## Notes
- Set `LOGIN_URL` and `DONATION_URL` in `.env`.
- If `WEBSITE_USERNAME` and `WEBSITE_PASSWORD` are provided, the script attempts login automatically.
- Output filenames are timestamped each run so you can verify repeated automation runs.
- Set `GOOGLE_DRIVE_FOLDER_ID` to the destination folder ID in Google Drive.
- Choose auth mode with `GOOGLE_AUTH_MODE`:
  - `oauth` (default): put OAuth client file as `credentials.json` (or set `GOOGLE_CREDENTIALS_FILE`). First run opens browser consent and writes `token.json`.
  - `service_account`: set `GOOGLE_SERVICE_ACCOUNT_FILE` and (for Workspace) `GOOGLE_IMPERSONATE_USER`.

## Required `.env` for Drive upload

```dotenv
GOOGLE_DRIVE_FOLDER_ID=1AbCdEfGhIjKlMnOpQrStUvWxYz
GOOGLE_AUTH_MODE=oauth
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=token.json

# only for service_account mode
GOOGLE_SERVICE_ACCOUNT_FILE=
GOOGLE_IMPERSONATE_USER=
```

## Google Workspace admin setup (for `service_account` mode)

1. In Google Cloud Console, enable **Google Drive API** for your project.
2. Create a **Service Account** and download its JSON key file.
3. In the same project, configure OAuth consent as **Internal** (recommended for Workspace use).
4. In Admin Console: **Security → API controls → Domain-wide delegation → Add new**.
5. Use the service account **Client ID** and add scope:
	- `https://www.googleapis.com/auth/drive`
6. Set `.env`:
	- `GOOGLE_AUTH_MODE=service_account`
	- `GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/to/service-account.json`
	- `GOOGLE_IMPERSONATE_USER=someone@yourdomain.com`
7. Share the destination Drive folder with that impersonated user (or ensure they already have access). For Shared Drives, user must have permission in that Shared Drive.
