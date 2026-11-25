import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import Page, BrowserContext
from config import Config


class SessionManager:
    """
    Manages persistent Moodle authentication state using storage/auth.json.
    Three states:
    1. NO AUTH STATE -> Perform login -> Save storage/auth.json
    2. VALID AUTH STATE -> Fast path: continue directly to course/activity
    3. EXPIRED AUTH STATE -> Relogin -> Refresh storage/auth.json
    """

    def __init__(self, auth_file: Path = None):
        self.auth_file = auth_file or Config.AUTH_STATE_FILE
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)

    def has_saved_auth(self) -> bool:
        """Returns True if auth.json exists and has content."""
        return self.auth_file.exists() and self.auth_file.stat().st_size > 50

    def is_logged_in(self, page: Page) -> bool:
        """Checks whether the current page is an authenticated Moodle session."""
        url_lower = page.url.lower()
        if "login/index.php" in url_lower or "login.php" in url_lower:
            return False

        # If login form elements are present on screen, definitely not logged in
        try:
            if page.locator("#username, #loginbtn").count() > 0:
                return False
        except Exception:
            pass

        try:
            # Positive indicator: user menu or profile
            user_menu = page.locator(".userbutton, #action-menu-toggle-0, .usermenu, a[href*='logout.php'], .userpicture")
            if user_menu.count() > 0:
                return True
            # Main course or activity container
            if page.locator("#region-main, .course-content, .activityitem, iframe.h5p-iframe").count() > 0:
                return True
        except Exception:
            pass

        return False

    def _resolve_login_url(self, page: Page) -> str:
        """Derives the Moodle login URL from the current page URL or Config."""
        candidates = [page.url, Config.TARGET_URL] + list(getattr(Config, "COURSE_URLS", []) or [])
        for candidate in candidates:
            if not candidate:
                continue
            parsed = urlparse(candidate)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}/login/index.php"
        return ""

    def perform_login(self, page: Page, context: BrowserContext = None) -> bool:
        """Performs Moodle login using credentials from Config and saves auth.json."""
        print("[Auth] Performing automated Moodle login...")

        if "login" not in page.url.lower() and page.locator("#username").count() == 0:
            login_url = self._resolve_login_url(page)
            if not login_url:
                print("[Auth Error] Could not determine Moodle login URL. Configure TARGET_URL or COURSE_URLS.")
                return False
            page.goto(login_url, wait_until="domcontentloaded")

        try:
            page.wait_for_selector("#username", timeout=10000)
            page.fill("#username", Config.LMS_USERNAME)
            page.fill("#password", Config.LMS_PASSWORD)
            page.click("#loginbtn")

            # Wait for redirect away from login
            page.wait_for_selector("#region-main, .course-content, .usermenu, .userbutton, a[href*='logout.php']", timeout=15000)
            page.wait_for_load_state("domcontentloaded")
            print("[Auth] Login successful!")

            # Save persistent auth state
            ctx = context or page.context
            if ctx and Config.PERSISTENT_AUTH:
                self.save_auth_state(ctx)

            return True
        except Exception as e:
            print(f"[Auth Error] Login failed: {e}")
            return False

    def save_auth_state(self, context: BrowserContext):
        """Exports browser cookies and local storage to auth.json."""
        try:
            context.storage_state(path=str(self.auth_file))
            print(f"[Auth] Saved persistent session to: {self.auth_file}")
        except Exception as e:
            print(f"[Auth Warning] Could not save storage state: {e}")

    def ensure_authenticated(self, page: Page, context: BrowserContext = None) -> bool:
        """
        Fast-path authentication:
        Checks if current session is already valid. If not, performs login.
        """
        # If already on login page or form visible
        if "login" in page.url.lower() or page.locator("#username, #loginbtn").count() > 0:
            target = page.url
            success = self.perform_login(page, context)
            if success and "mod/" in target and "login" not in target.lower():
                page.goto(target, wait_until="domcontentloaded")
            return success

        if self.is_logged_in(page):
            print("[Auth] Session is already authenticated. Fast-path active!")
            return True

        target = page.url
        success = self.perform_login(page, context)
        if success and "mod/" in target and "login" not in target.lower():
            page.goto(target, wait_until="domcontentloaded")
        return success
