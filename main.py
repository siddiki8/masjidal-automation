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


def navigate_to_donation_page(driver: webdriver.Chrome, donation_url: Optional[str] = None) -> None:
    timeout_seconds = int(os.getenv("DONATION_PAGE_TIMEOUT", "180"))
    wait = WebDriverWait(driver, 20)
    end_time = time.time() + timeout_seconds
    last_error = None
    donation_menu_xpath = "/html/body/div[2]/div/div/aside/div[2]/ul/li[3]/ul/li[4]/a"
    donation_date_filter_xpath = "/html/body/div/div/div/main/div/div/div/div[2]/div/select"

    while time.time() < end_time:
        try:
            original_handles = set(driver.window_handles)
            menu_link = wait.until(EC.element_to_be_clickable((By.XPATH, donation_menu_xpath)))
            menu_link.click()

            WebDriverWait(driver, 15).until(
                lambda d: len(d.window_handles) >= len(original_handles)
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
            if donation_url:
                try:
                    driver.get(donation_url)
                    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
                    wait.until(EC.presence_of_element_located((By.XPATH, donation_date_filter_xpath)))
                    return
                except Exception as fallback_exc:
                    last_error = fallback_exc
            time.sleep(3)

    detail = donation_url if donation_url else "menu click only"
    save_debug_artifacts(driver, "navigate_to_donation_failed")
    raise TimeoutException(
        f"Failed to navigate to donation page within {timeout_seconds}s using {detail}."
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
        save_debug_artifacts(driver, "date_filter_not_found")
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

    save_debug_artifacts(driver, "csv_download_timeout")
    raise TimeoutException("CSV download did not complete within 60 seconds after export click.")


def clean_csv(input_csv: Path, output_dir: Path) -> Path:
    df = pd.read_csv(input_csv)
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cleaned_path = output_dir / f"cleaned_{input_csv.stem}_{run_timestamp}.csv"
    df.to_csv(cleaned_path, index=False)
    return cleaned_path


def save_debug_artifacts(driver: webdriver.Chrome, name_prefix: str) -> None:
    debug_dir = Path("debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = debug_dir / f"{name_prefix}_{timestamp}.png"
    html_path = debug_dir / f"{name_prefix}_{timestamp}.html"
    driver.save_screenshot(str(screenshot_path))
    html_path.write_text(driver.page_source, encoding="utf-8")
    print(f"Saved debug screenshot: {screenshot_path}")
    print(f"Saved debug HTML: {html_path}")


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

        navigate_to_donation_page(driver, donation_url)

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
