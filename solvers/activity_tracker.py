import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from playwright.sync_api import Page
from config import Config
from utils.score_parser import parse_score_value


class ActivityTracker:
    """
    Manages persistent tracking of completed LMS activities and their scores.
    Stores data in temp/completed_activities.json and temp/completed_activities.txt.
    """

    def __init__(self, temp_dir: Path = None):
        self.temp_dir = temp_dir or Config.TEMP_DIR
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.json_file = self.temp_dir / "completed_activities.json"
        self.txt_file = self.temp_dir / "completed_activities.txt"
        self.records: dict[str, dict] = self._load()

    def _normalize_url(self, url: str) -> str:
        """Strips URL fragment (#section-...) and trailing slashes for stable matching."""
        if not url:
            return ""
        return url.split("#")[0].rstrip("/").strip()

    def _load(self) -> dict[str, dict]:
        """Loads completed activities from JSON file."""
        if self.json_file.exists():
            try:
                data = json.loads(self.json_file.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception as e:
                print(f"[ActivityTracker Warning] Error loading {self.json_file}: {e}")
        return {}

    def _save(self):
        """Saves completed activities to JSON and ASCII table text file."""
        try:
            self.json_file.write_text(json.dumps(self.records, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[ActivityTracker Warning] Error saving JSON {self.json_file}: {e}")

        try:
            self._write_txt_summary()
        except Exception as e:
            print(f"[ActivityTracker Warning] Error saving TXT summary {self.txt_file}: {e}")

    def _write_txt_summary(self):
        """Writes a formatted ASCII table report to temp/completed_activities.txt."""
        lines = []
        lines.append("=" * 105)
        lines.append("POODLE LMS AUTOMATION - COMPLETED ACTIVITIES & SCORE REPORT")
        lines.append(f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 105)
        lines.append(f"{'TYPE':<12} | {'SCORE':<24} | {'STATUS':<12} | {'TITLE / URL':<50}")
        lines.append("-" * 105)

        for norm_url, data in self.records.items():
            act_type = str(data.get("type", "unknown")).upper()
            score = str(data.get("score", "Completed"))[:24]
            status = str(data.get("status", "Done"))[:12]
            title = str(data.get("title") or norm_url)[:48]
            lines.append(f"{act_type:<12} | {score:<24} | {status:<12} | {title:<50}")

        lines.append("=" * 105)
        self.txt_file.write_text("\n".join(lines), encoding="utf-8")

    def is_completed(self, url: str) -> bool:
        """
        Returns True if the activity URL is already recorded as genuinely completed.
        Never counts manual button marks or course marks as verified completion.
        """
        norm = self._normalize_url(url)
        if norm not in self.records:
            return False
        entry = self.records[norm]
        if entry.get("status") != "Completed":
            return False
        score = str(entry.get("score", "")).strip().lower()
        if "course marked" in score or score == "mark as done" or score == "done":
            return False

        # Passing score threshold check for all activity types
        act_type = str(entry.get("type", "")).lower()
        min_score = Config.get_threshold(act_type)
        val = parse_score_value(entry.get("score"))
        if val is not None and val < min_score:
            return False

        return True

    def purge_unverified_completions(self):
        """Purges any records that were marked done purely from manual checkmarks, course page, or unverified speaking activities."""
        to_del = []
        for norm_url, data in self.records.items():
            score = str(data.get("score", "")).strip().lower()
            act_type = str(data.get("type", "")).lower()
            if "course marked" in score or score == "done" or score == "mark as done" or score == "done (100%)":
                to_del.append(norm_url)
            elif (act_type in ("solo", "readaloud") or "/mod/solo/" in norm_url.lower() or "/mod/readaloud/" in norm_url.lower()) and score in ("completed", "done", ""):
                to_del.append(norm_url)
        if to_del:
            for u in to_del:
                del self.records[u]
            self._save()
            print(f"[Activity Tracker] Purged {len(to_del)} unverified/manual-check activity records.")

    def get_score(self, url: str) -> str:
        """Returns the recorded score for an activity, or empty string."""
        norm = self._normalize_url(url)
        return self.records.get(norm, {}).get("score", "")

    def record_activity(
        self,
        url: str,
        title: str = "",
        activity_type: str = "generic",
        score: str = "Completed",
        status: str = "Completed",
        details: dict = None
    ):
        """Records an activity as completed with its score and details."""
        norm = self._normalize_url(url)
        if not norm:
            return

        existing = self.records.get(norm, {})
        entry = {
            "url": norm,
            "title": title or existing.get("title", norm),
            "type": activity_type or existing.get("type", "generic"),
            "score": score or existing.get("score", "Completed"),
            "status": status,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "details": details or existing.get("details", {})
        }
        self.records[norm] = entry
        self._save()
        print(f"[Activity Tracker] Recorded: [{entry['type'].upper()}] \"{entry['title']}\" -> Score: {entry['score']}")

    def remove_activity(self, url: str):
        """Removes an activity from tracking if stale or uncompleted."""
        norm = self._normalize_url(url)
        if norm in self.records:
            del self.records[norm]
            self._save()

    def clear(self):
        """Clears all records."""
        self.records = {}
        self._save()

    def extract_score_from_page(self, page: Page) -> str:
        """
        Extracts score, grade, or completion details from the current page DOM.
        Handles attempt summary tables, grade headers, PoodLL ReadAloud/Solo evaluations, and results containers.
        NEVER generates a score from a manual completion button!
        """
        try:
            main_loc = page.locator("#region-main")
            main_text = main_loc.inner_text() if main_loc.count() > 0 else ""

            # 1. Inspect Moodle attempt summary table (generaltable)
            tables = page.locator("table.generaltable, .mod_readaloud_attemptsummary, .attempts-table").all()
            for table in tables:
                if not table.is_visible():
                    continue

                headers = [h.inner_text().strip().lower() for h in table.locator("th").all()]
                rows = table.locator("tbody tr").all()
                if rows:
                    last_row = rows[-1]  # Most recent attempt
                    cells = [c.inner_text().strip() for c in last_row.locator("td").all()]

                    # Check for Grade / Score / Accuracy / WPM / Marks column
                    target_cells = []
                    for idx, h in enumerate(headers):
                        if idx < len(cells) and cells[idx]:
                            if any(kw in h for kw in ["grade", "score", "mark", "accuracy", "wpm", "points"]):
                                target_cells.append(f"{h.capitalize()}: {cells[idx]}")

                    if target_cells:
                        return " | ".join(target_cells)

                    # Fallback: join cells if short
                    if len(cells) >= 2:
                        return " | ".join(cells[:4])

            # 2. PoodLL ReadAloud evaluation block
            if "your latest attempt has been received" in main_text.lower() or "evaluation is shown below" in main_text.lower():
                acc_match = re.search(r"ACCURACY\s*\n*\s*(\d+)\s*\n*\s*percent", main_text, re.IGNORECASE)
                wpm_match = re.search(r"WPM\s*\n*\s*(\d+)", main_text, re.IGNORECASE)
                parts = []
                if acc_match:
                    parts.append(f"Accuracy: {acc_match.group(1)}%")
                if wpm_match:
                    parts.append(f"WPM: {wpm_match.group(1)}")
                if parts:
                    return " | ".join(parts)

            # 3. PoodLL Solo speech details / results block
            if "your speech details" in main_text.lower() or "results of your speech below" in main_text.lower():
                ratio_match = re.search(r"(\d+\s*/\s*\d+)", main_text)
                pct_matches = re.findall(r"(\d+(?:\.\d+)?%)", main_text)
                parts = []
                if ratio_match:
                    parts.append(f"Words: {ratio_match.group(1)}")
                if pct_matches:
                    parts.append(f"Score: {pct_matches[0]}")
                if parts:
                    return " | ".join(parts)

            # 4. Results Screen containers (Wordcards, Solo, etc.)
            results_loc = page.locator("#wordcards-results, #results-inner, .mod_wordcards_congrats, .mod_solo_stepsubmit")
            if results_loc.count() > 0 and results_loc.first.is_visible():
                res_text = results_loc.first.inner_text()
                score_match = re.search(r"(?:grade|score|total|correct|points)[:\s]+([\d\.]+(?:\s*(?:/|out of)\s*[\d\.]+)?%?)", res_text, re.IGNORECASE)
                if score_match:
                    return score_match.group(0).strip()
                pct_match = re.search(r"\b(\d+(?:\.\d+)?%)\b", res_text)
                if pct_match:
                    return f"Score: {pct_match.group(1)}"
                ratio_match = re.search(r"\b(\d+\s*/\s*\d+)\b", res_text)
                if ratio_match:
                    return f"Score: {ratio_match.group(1)}"

            # 5. Search in #region-main text for explicit Grade / Score pattern
            match = re.search(r"(?:overall grade|grade|marks|final score)[:\s]+([\d\.]+(?:\s*(?:/|out of)\s*[\d\.]+)?%?)", main_text, re.IGNORECASE)
            if match and re.search(r"\d", match.group(1)):
                return match.group(0).strip()

        except Exception as e:
            print(f"[ActivityTracker Warning] Error extracting score: {e}")

        return "Completed"

    def check_live_page_completion(self, page: Page, act_type: str = "") -> tuple[bool, str, Optional[float], float]:
        """
        Inspects the live LMS activity or quiz page to determine real completion status based on marks.
        Returns:
            (is_passing: bool, score_str: str, marks_pct: Optional[float], threshold_pct: float)
        """
        from utils.score_parser import is_page_score_passing
        is_passing, score_str, marks_pct, threshold = is_page_score_passing(page, act_type)

        if is_passing:
            # Auto-click 'Mark as done' if pending and genuinely completed
            try:
                pending_btn = page.locator("button[data-action='toggle-manual-completion'][data-toggletype='manual:mark-done']")
                if pending_btn.count() > 0 and pending_btn.first.is_visible():
                    pending_btn.first.click()
                    page.wait_for_timeout(800)
            except Exception:
                pass

        return is_passing, score_str, marks_pct, threshold

    def check_if_page_is_completed(self, page: Page, act_type: str = "") -> tuple[bool, str]:
        """
        Inspects the activity page to check if it has ALREADY been completed with a passing grade.
        Returns (is_completed: bool, score: str).
        """
        is_passing, score_str, marks_pct, threshold = self.check_live_page_completion(page, act_type)
        return is_passing, score_str

    def print_summary(self):
        """Prints the completed activities table to stdout."""
        if not self.records:
            print("\n[Activity Tracker] No activities recorded yet.")
            return

        print("\n" + "=" * 80)
        print("POODLE AUTOMATION - COMPLETED ACTIVITIES SUMMARY")
        print("=" * 80)
        for norm_url, data in self.records.items():
            act_type = str(data.get("type", "generic")).upper()
            title = data.get("title", norm_url)
            score = data.get("score", "Completed")
            print(f"  [{act_type}] {title}")
            print(f"       Score : {score}")
            print(f"       URL   : {norm_url}")
        print("=" * 80 + "\n")
