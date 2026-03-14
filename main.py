from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

SCOPES = ["https://www.googleapis.com/auth/drive"]

IMPACT_AREA_MAPPING = {
    "Ramadan Fund": "Ramadan 2026",
    "Sadaqa (For the Needy)": "Sadaqa (For the Needy)",
    "Fitra": "Zakatul Fitr",
    "Zakat": "Zakat",
    "DI Services Campaign": "Ramadan 2026",
    "Masjid Operations": "Masjid Operations",
    "DI Schools Campaign": "Ramadan 2026",
}


def get_env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def create_dirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def build_driver(download_dir: Path) -> webdriver.Chrome:
    chrome_options = Options()

    chrome_options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)


def _find_first(driver: webdriver.Chrome, selectors: list[tuple[str, str]]):
    for by, value in selectors:
        found = driver.find_elements(by, value)
        if found:
            return found[0]
    return None


def login_if_needed(driver: webdriver.Chrome, login_url: str, username: str, password: str) -> None:
    driver.get(login_url)
    wait = WebDriverWait(driver, 30)

    username_input = None
    password_input = None

    username_selectors = [
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[name='email']"),
        (By.CSS_SELECTOR, "input[name='username']"),
        (By.CSS_SELECTOR, "input[id*='email']"),
        (By.CSS_SELECTOR, "input[id*='user']"),
        (By.CSS_SELECTOR, "input[autocomplete='username']"),
        (By.CSS_SELECTOR, "input[type='text']"),
    ]
    password_selectors = [
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.CSS_SELECTOR, "input[name='password']"),
        (By.CSS_SELECTOR, "input[id*='password']"),
        (By.CSS_SELECTOR, "input[autocomplete='current-password']"),
    ]

    try:
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "input")))
        username_input = _find_first(driver, username_selectors)
        password_input = _find_first(driver, password_selectors)
    except TimeoutException as exc:
        raise TimeoutException("Login form did not load in time.") from exc

    if username_input is None or password_input is None:
        raise RuntimeError(
            "Could not find login inputs automatically. "
            "Please share login form selectors (username, password, submit button)."
        )

    username_input.clear()
    username_input.send_keys(username)
    password_input.clear()
    password_input.send_keys(password)

    submit_btn = _find_first(
        driver,
        [
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.CSS_SELECTOR, "input[type='submit']"),
            (By.XPATH, "//button[contains(translate(., 'LOGINSGNIN', 'loginsgnin'), 'login') or contains(translate(., 'LOGINSGNIN', 'loginsgnin'), 'sign in') or contains(translate(., 'LOGINSGNIN', 'loginsgnin'), 'log in')]") ,
        ],
    )

    if submit_btn is not None:
        submit_btn.click()
    else:
        password_input.submit()

    try:
        WebDriverWait(driver, 10).until(lambda d: d.current_url != login_url)
    except TimeoutException:
        pass
    time.sleep(2)


def navigate_to_donation_page(driver: webdriver.Chrome) -> None:
    timeout_seconds = int(os.getenv("DONATION_PAGE_TIMEOUT", "180"))
    wait = WebDriverWait(driver, 20)
    end_time = time.time() + timeout_seconds
    last_error = None
    donation_menu_xpaths = [
        "/html/body/div/div/div/aside/div[2]/ul/li[3]/ul/li[4]/a",
        "/html/body/div[2]/div/div/aside/div[2]/ul/li[3]/ul/li[4]/a",
    ]
    donation_date_filter_xpath = "/html/body/div/div/div/main/div/div/div/div[2]/div/select"

    while time.time() < end_time:
        try:
            original_handles = set(driver.window_handles)
            menu_link = None
            for xpath in donation_menu_xpaths:
                found = driver.find_elements(By.XPATH, xpath)
                if found:
                    menu_link = found[0]
                    break

            if menu_link is None:
                raise TimeoutException("Donation menu link not found in sidebar.")

            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", menu_link)
            try:
                wait.until(EC.element_to_be_clickable((By.XPATH, donation_menu_xpaths[0])))
                menu_link.click()
            except Exception:
                driver.execute_script("arguments[0].click();", menu_link)

            WebDriverWait(driver, 15).until(
                lambda d: len(d.window_handles) > len(original_handles)
            )
            new_handles = [handle for handle in driver.window_handles if handle not in original_handles]
            if new_handles:
                driver.switch_to.window(new_handles[-1])
            else:
                driver.switch_to.window(driver.window_handles[-1])

            wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
            wait.until(EC.presence_of_element_located((By.XPATH, donation_date_filter_xpath)))
            return
        except Exception as exc:
            last_error = exc
            time.sleep(3)

    raise TimeoutException(
        f"Failed to navigate to donation page within {timeout_seconds}s via sidebar link click."
    ) from last_error


def download_csv(driver: webdriver.Chrome, target_url: str, download_dir: Path) -> Path:
    _ = target_url
    wait = WebDriverWait(driver, 90)

    date_select_xpath = "/html/body/div/div/div/main/div/div/div/div[2]/div/select"
    export_button_id = "dropdown-basic"
    export_columns_by_text_xpath = (
        "//a[contains(@class,'dropdown-item') and normalize-space()='Export All Columns']"
    )

    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")

    last_error = None
    select_element = None
    for _ in range(3):
        try:
            select_element = wait.until(EC.presence_of_element_located((By.XPATH, date_select_xpath)))
            break
        except TimeoutException as exc:
            last_error = exc
            driver.refresh()
            time.sleep(2)

    if select_element is None:
        raise TimeoutException(
            f"Date filter not found at XPath: {date_select_xpath}. Current URL: {driver.current_url}"
        ) from last_error

    date_filter = Select(select_element)
    try:
        date_filter.select_by_visible_text("Yesterday")
    except Exception:
        yesterday = datetime.now() - timedelta(days=1)
        fallback_values = [
            yesterday.strftime("%Y-%m-%d"),
            yesterday.strftime("%m/%d/%Y"),
            yesterday.strftime("%d/%m/%Y"),
            "yesterday",
        ]
        options = select_element.find_elements(By.TAG_NAME, "option")
        matched = False
        for option in options:
            option_text = option.text.strip().lower()
            option_value = (option.get_attribute("value") or "").strip().lower()
            if any(v.lower() in option_text or v.lower() == option_value for v in fallback_values):
                date_filter.select_by_visible_text(option.text)
                matched = True
                break
        if not matched:
            raise RuntimeError("Could not set date filter to yesterday.")

    before_files = {p.name for p in download_dir.glob("*.csv")}

    export_button = wait.until(EC.element_to_be_clickable((By.ID, export_button_id)))
    try:
        export_button.click()
    except Exception:
        driver.execute_script("arguments[0].click();", export_button)

    export_columns_button = WebDriverWait(driver, 30).until(
        EC.visibility_of_element_located((By.XPATH, export_columns_by_text_xpath))
    )
    try:
        export_columns_button.click()
    except Exception:
        driver.execute_script("arguments[0].click();", export_columns_button)

    timeout_seconds = 60
    start = time.time()
    while time.time() - start < timeout_seconds:
        if list(download_dir.glob("*.crdownload")):
            time.sleep(1)
            continue

        csv_files = sorted(download_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if csv_files:
            newest = csv_files[0]
            if newest.name not in before_files:
                return newest
        time.sleep(1)

    raise TimeoutException("CSV download did not complete within 60 seconds after export click.")


def _normalize_recurring(value: object) -> str:
    text = str(value).strip().lower()
    if text in {"recurring", "y", "yes", "true", "1"}:
        return "Y"
    return "N"


def _normalize_payment_type(value: object) -> str:
    text = str(value).strip().lower()
    mapping = {
        "card": "Credit Card",
        "credit card": "Credit Card",
        "cash": "Cash",
        "bank transfer": "Bank Transfer/EFT",
        "eft": "Bank Transfer/EFT",
        "bank transfer/eft": "Bank Transfer/EFT",
        "bank": "Bank Transfer/EFT",
    }
    return mapping.get(text, str(value).strip())


def _build_keela_dataframe(df: pd.DataFrame, mapping_csv: Path) -> pd.DataFrame:
    if not mapping_csv.exists():
        raise FileNotFoundError(f"Field mapping file not found: {mapping_csv}")

    mapping_df = pd.read_csv(mapping_csv).fillna("")
    if "MasjidAl Field" not in mapping_df.columns or "Keela Field" not in mapping_df.columns:
        raise ValueError("Mapping CSV must contain 'MasjidAl Field' and 'Keela Field' columns.")

    output_columns: dict[str, pd.Series] = {}
    keela_to_source: dict[str, str] = {}
    for _, row in mapping_df.iterrows():
        source_col = str(row["MasjidAl Field"]).strip()
        keela_col = str(row["Keela Field"]).strip()
        if not source_col or not keela_col:
            continue
        keela_to_source[keela_col] = source_col
        if source_col in df.columns:
            output_columns[keela_col] = df[source_col]
        else:
            output_columns[keela_col] = pd.Series([""] * len(df), index=df.index)

    keela_df = pd.DataFrame(output_columns)

    if "Recurring" in keela_df.columns:
        keela_df["Recurring"] = keela_df["Recurring"].apply(_normalize_recurring)

    if "Payment Type" in keela_df.columns:
        keela_df["Payment Type"] = keela_df["Payment Type"].apply(_normalize_payment_type)

    if "Date of Gift" in keela_df.columns:
        source_col = keela_to_source.get("Date of Gift", "created_at")
        raw_series = df[source_col] if source_col in df.columns else keela_df["Date of Gift"]
        normalized = raw_series.astype(str).str.replace(r"\s+\(.*\)$", "", regex=True)
        parsed = pd.to_datetime(
            normalized,
            format="%a %b %d %Y %H:%M:%S GMT%z",
            errors="coerce",
            utc=True,
        )
        needs_fallback = parsed.isna()
        if needs_fallback.any():
            fallback_parsed = pd.to_datetime(raw_series[needs_fallback], errors="coerce", utc=True)
            parsed.loc[needs_fallback] = fallback_parsed
        eastern = parsed.dt.tz_convert("America/New_York")
        keela_df["Date of Gift"] = eastern.dt.strftime("%m/%d/%Y").fillna("")

    if "Transaction Item Amount" in keela_df.columns:
        numeric_amount = pd.to_numeric(keela_df["Transaction Item Amount"], errors="coerce")
        keela_df["Transaction Item Amount"] = numeric_amount.apply(
            lambda value: f"{value:.2f}" if pd.notna(value) else ""
        )

    return keela_df


def clean_csv(input_csv: Path, output_dir: Path) -> Path:
    df = pd.read_csv(input_csv)

    campaign_title_series: Optional[pd.Series] = None
    if "campaign_title" in df.columns:
        campaign_titles = df["campaign_title"].astype(str).str.strip()
        df = df.loc[~campaign_titles.str.contains(r"\bfees?\b", case=False, na=False)].copy()
        campaign_title_series = df["campaign_title"].astype(str).str.strip()

    mapping_file_value = os.getenv("FIELD_MAPPING_FILE", "Keela Field Mapping - Sheet1.csv").strip()
    mapping_csv = Path(mapping_file_value).expanduser()
    if not mapping_csv.is_absolute():
        mapping_csv = Path(__file__).resolve().with_name(mapping_file_value)

    df = _build_keela_dataframe(df, mapping_csv)

    df["Source"] = "Masjidal"
    if campaign_title_series is not None:
        df["Impact Area"] = campaign_title_series.map(IMPACT_AREA_MAPPING).fillna("")
    else:
        df["Impact Area"] = ""

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cleaned_path = output_dir / f"cleaned_{input_csv.stem}_{run_timestamp}.csv"
    df.to_csv(cleaned_path, index=False)
    return cleaned_path




def get_drive_service(
    auth_mode: str,
    credentials_file: Path,
    token_file: Path,
    service_account_file: Optional[Path] = None,
    impersonate_user: Optional[str] = None,
):
    if auth_mode == "service_account":
        if service_account_file is None:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_FILE is required for GOOGLE_AUTH_MODE=service_account")
        creds = service_account.Credentials.from_service_account_file(
            str(service_account_file),
            scopes=SCOPES,
        )
        if impersonate_user:
            creds = creds.with_subject(impersonate_user)
        return build("drive", "v3", credentials=creds)

    if auth_mode != "oauth":
        raise ValueError("GOOGLE_AUTH_MODE must be either 'oauth' or 'service_account'")

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)


def upload_to_google_drive(service, file_path: Path, folder_id: Optional[str]) -> str:
    metadata = {"name": file_path.name}
    if folder_id:
        metadata["parents"] = [folder_id]

    media = MediaFileUpload(str(file_path), mimetype="text/csv")
    uploaded = (
        service.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id,name",
            supportsAllDrives=True,
        )
        .execute()
    )
    return uploaded["id"]


def main() -> None:
    env_path = Path(__file__).resolve().with_name(".env")
    load_dotenv(dotenv_path=env_path, override=True)

    login_url = get_env("LOGIN_URL")
    donation_url = os.getenv("DONATION_URL")
    username = os.getenv("WEBSITE_USERNAME", "")
    password = os.getenv("WEBSITE_PASSWORD", "")

    download_dir = Path(get_env("DOWNLOAD_DIR", "downloads"))
    output_dir = Path(get_env("OUTPUT_DIR", "output"))
    create_dirs(download_dir, output_dir)

    auth_mode = os.getenv("GOOGLE_AUTH_MODE", "oauth").strip().lower()
    credentials_file = Path(get_env("GOOGLE_CREDENTIALS_FILE", "credentials.json"))
    token_file = Path(get_env("GOOGLE_TOKEN_FILE", "token.json"))
    service_account_file_value = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    service_account_file = Path(service_account_file_value).expanduser() if service_account_file_value else None
    impersonate_user = os.getenv("GOOGLE_IMPERSONATE_USER", "").strip() or None
    folder_id = get_env("GOOGLE_DRIVE_FOLDER_ID")

    driver = build_driver(download_dir)
    try:
        if username and password:
            login_if_needed(driver, login_url, username, password)
        else:
            driver.get(login_url)

        navigate_to_donation_page(driver)

        csv_path = download_csv(driver, donation_url, download_dir)
        print(f"Downloaded CSV: {csv_path}")
    finally:
        driver.quit()

    cleaned_csv = clean_csv(csv_path, output_dir)
    print(f"Cleaned CSV: {cleaned_csv}")

    drive_service = get_drive_service(
        auth_mode=auth_mode,
        credentials_file=credentials_file,
        token_file=token_file,
        service_account_file=service_account_file,
        impersonate_user=impersonate_user,
    )
    file_id = upload_to_google_drive(drive_service, cleaned_csv, folder_id)
    print(f"Uploaded {cleaned_csv.name} to Drive folder {folder_id} with id: {file_id}")


if __name__ == "__main__":
    main()
