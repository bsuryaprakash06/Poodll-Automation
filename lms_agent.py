import os
import sys
sys.stdout.reconfigure(line_buffering=True)
import time
import argparse
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

from config import Config
from audio.devices import verify_vb_cable
from audio.tts import TTSEngine
from audio.router import AudioRouter

from solvers.readaloud_solver import ReadAloudSolver
from solvers.solo_solver import SoloSolver
from solvers.wordcards_solver import WordcardsSolver
from solvers.hvp_solver import HvpSolver
from solvers.quiz_solver import QuizSolver
from solvers.activity_tracker import ActivityTracker

from browser.routing import ResourceRouter
from auth.session import SessionManager
from utils.waits import MoodleWaits
from utils.cache import ActivityCache
from utils.activity_selector import prompt_activity_selection
from performance.benchmark import BenchmarkTracker

MEDIA_SHIM_PATH = Path(__file__).parent / "browser" / "media_shim.js"
POODLL_SHIM_PATH = Path(__file__).parent / "browser" / "cloudpoodll_shim.js"
PROFILE_DIR = Config.TEMP_DIR / "browser_profile"


def setup_request_logger(page):
    def on_request(request):
        try:
            if request.method == "POST":
                print(f"[NET POST] -> {request.url}")
        except Exception:
            pass

    def on_response(response):
        try:
            if response.request.method == "POST":
                print(f"[NET RESP] <- {response.status} from {response.url}")
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)


def extract_course_name(page) -> str:
    """Extracts the course name from the page header or title."""
    try:
        name = page.evaluate("""() => {
            const h1 = document.querySelector('h1, .page-header-headings h1, header h1, .page-title');
            if (h1 && h1.innerText.trim()) {
                return h1.innerText.trim();
            }
            const breadcrumb = document.querySelector('.breadcrumb-item.active, nav.breadcrumb li:last-child');
            if (breadcrumb && breadcrumb.innerText.trim()) {
                return breadcrumb.innerText.trim();
            }
            return document.title.replace(/\\|.*$/, '').replace(/^Course:\\s*/i, '').trim();
        }""")
        return name or "Moodle Course"
    except Exception:
        return page.title() or "Moodle Course"


def extract_course_activities(page, target_url: str) -> list[dict]:
    """Extracts all Poodll and H5P activities from a course page or specified section."""
    parsed = urlparse(target_url)
    section_id = parsed.fragment if parsed.fragment.startswith("section-") else None

    print(f"\n[Course Crawler] Scanning course page: {target_url}")
    if section_id:
        print(f"[Course Crawler] Filtering specifically for: #{section_id}")

    # 1. Expand all collapsed sections so all activities across all sections are rendered
    try:
        page.evaluate("""() => {
            const clickable = Array.from(document.querySelectorAll('a, button, [role="button"], [data-action]'));
            for (const el of clickable) {
                const txt = (el.innerText || '').trim().toLowerCase();
                const action = el.getAttribute('data-action') || '';
                if (txt === 'expand all' || action === 'expand-all' || action === 'collapsesections' || el.id === 'collapsesections') {
                    try { el.click(); } catch(e) {}
                }
            }
            document.querySelectorAll('details').forEach(d => d.open = true);
            document.querySelectorAll('[aria-expanded="false"]').forEach(el => {
                try { el.click(); } catch(e) {}
            });
        }""")
        page.wait_for_timeout(1000)
    except Exception as e:
        print(f"[Course Crawler] Note on section expansion: {e}")

    activities = page.evaluate(
        """(targetSection) => {
            let container = document;
            if (targetSection) {
                const sec = document.getElementById(targetSection);
                if (sec) container = sec;
            }

            const targetTypes = ['wordcards', 'solo', 'readaloud', 'hvp', 'h5pactivity', 'quiz'];
            const selector = targetTypes.map(t => `a[href*="/mod/${t}/"]`).join(', ');
            const links = Array.from(container.querySelectorAll(selector));

            const seen = new Set();
            const results = [];

            for (const a of links) {
                const href = a.href.split('#')[0];
                if (seen.has(href)) continue;
                seen.add(href);

                const actContainer = a.closest('.activityitem, .activity, li.activity, div.activityinstance') || a.parentElement;

                // Extract clean title without appending button labels
                let title = '';
                const manualBtn = actContainer ? actContainer.querySelector('[data-action="toggle-manual-completion"]') : null;
                if (manualBtn && manualBtn.getAttribute('data-activityname')) {
                    title = manualBtn.getAttribute('data-activityname').trim();
                }
                if (!title && actContainer) {
                    const instName = actContainer.querySelector('.instancename, .activityname, .activity-instance');
                    if (instName) {
                        const clone = instName.cloneNode(true);
                        clone.querySelectorAll('.accesshide').forEach(el => el.remove());
                        title = clone.innerText.trim();
                    }
                }
                if (!title) {
                    title = a.innerText.trim();
                }
                if (!title && actContainer) {
                    const lines = actContainer.innerText.split('\\n').map(l => l.trim()).filter(Boolean);
                    title = lines[0] || href;
                }

                // Strict completion checking:
                // Only consider completed if manual button is marked as undo / btn-success
                // or badge has badge-success or explicitly starts with 'done:' / 'completed'
                // NEVER treat 'Mark as done' or 'To do' as completed!
                let isDone = false;
                if (manualBtn) {
                    const tType = (manualBtn.getAttribute('data-toggletype') || '').toLowerCase();
                    const bTxt = (manualBtn.innerText || '').trim().toLowerCase();
                    const bTitle = (manualBtn.getAttribute('title') || '').toLowerCase();

                    if (
                        tType.includes('undo') ||
                        manualBtn.classList.contains('btn-success') ||
                        bTitle.includes('is marked as done') ||
                        (bTxt === 'done' || bTxt.includes('marked as done'))
                    ) {
                        isDone = true;
                    }
                }

                if (!isDone && actContainer) {
                    const successBadges = actContainer.querySelectorAll('.badge-success, .completion-info .badge-success, [data-region="completion-info"] .badge-success');
                    if (successBadges.length > 0) {
                        isDone = true;
                    } else {
                        const badges = actContainer.querySelectorAll('.badge, [data-region="completion-info"] span');
                        for (const b of badges) {
                            const txt = (b.innerText || '').trim().toLowerCase();
                            if ((txt.startsWith('done') || txt.includes('completed')) && !txt.includes('mark as') && !txt.includes('to do')) {
                                isDone = true;
                                break;
                            }
                        }
                    }
                }

                let actType = 'generic';
                for (const t of targetTypes) {
                    if (href.toLowerCase().includes(`/mod/${t}/`)) {
                        actType = t;
                        break;
                    }
                }

                results.push({
                    title: title || href,
                    url: href,
                    type: actType,
                    is_done_on_course: isDone,
                    has_manual_tracking: manualBtn !== null
                });
            }
            return results;
        }""",
        section_id
    )

    return activities


def dispatch_solver(
    page,
    activity_url: str,
    router: AudioRouter,
    speech_wav: Path,
    tracker: ActivityTracker = None,
    session_mgr: SessionManager = None,
    benchmark: BenchmarkTracker = None,
    title: str = ""
) -> bool:
    act_start = time.perf_counter()
    print(f"\n[Dispatcher] Direct navigating to activity: {activity_url}")
    page.goto(activity_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1000)

    if session_mgr:
        session_mgr.ensure_authenticated(page, page.context)

    MoodleWaits.page_ready(page)

    url_lower = page.url.lower()
    act_type = (
        "readaloud" if "/mod/readaloud/" in url_lower else
        "solo" if "/mod/solo/" in url_lower else
        "wordcards" if "/mod/wordcards/" in url_lower else
        "hvp" if ("/mod/hvp/" in url_lower or "/mod/h5pactivity/" in url_lower) else
        "quiz" if "/mod/quiz/" in url_lower else
        "generic"
    )

    # 2. Check real completion status directly on the live LMS page
    if tracker:
        is_passing, score_str, marks_pct, threshold = tracker.check_live_page_completion(page, act_type)
        if is_passing:
            tracker.record_activity(activity_url, title or page.title(), act_type, score=score_str, status="Completed")
            print(f"  >>> [SKIPPED] Activity has passing marks on LMS: {score_str} (>= {threshold:.1f}% threshold). Skipping!")
            if benchmark:
                benchmark.record_activity(title or activity_url, time.perf_counter() - act_start, success=True)
            return True
        elif marks_pct is not None:
            print(f"  >>> [RE-DO] Activity marks on LMS: {score_str} (< {threshold:.1f}% threshold). Re-doing activity!")
        else:
            print(f"  >>> [SOLVE] Activity not yet attempted on LMS (Threshold: {threshold:.1f}%). Solving...")

    # 3. Solve the activity
    success = False
    try:
        if "/mod/readaloud/" in url_lower:
            solver = ReadAloudSolver(page=page, router=router, speech_wav=speech_wav)
            success = solver.solve()
        elif "/mod/solo/" in url_lower:
            solver = SoloSolver(page=page, router=router, speech_wav=speech_wav)
            success = solver.solve()
        elif "/mod/wordcards/" in url_lower:
            solver = WordcardsSolver(page=page, router=router, speech_wav=speech_wav)
            success = solver.solve()
        elif "/mod/hvp/" in url_lower or "/mod/h5pactivity/" in url_lower:
            solver = HvpSolver(page=page, tracker=tracker, title=title)
            success = solver.solve()
        elif "/mod/quiz/" in url_lower:
            solver = QuizSolver(page=page, tracker=tracker, title=title)
            success = solver.solve()
        else:
            print(f"[Dispatcher Warning] Unrecognized activity type for URL: {page.url}")
            return False
    except Exception as e:
        print(f"[Dispatcher Error] Exception during activity execution for '{title or activity_url}': {e}")
        success = False

    # Auto-click 'Mark as done' if manual completion button is visible on page
    if success:
        try:
            pending_btn = page.locator("button[data-action='toggle-manual-completion'][data-toggletype='manual:mark-done']")
            if pending_btn.count() > 0 and pending_btn.first.is_visible():
                print("[Dispatcher] Marking activity as done on LMS...")
                pending_btn.first.click()
                page.wait_for_timeout(800)
        except Exception:
            pass

        # Verify completion directly on LMS via POST request
        try:
            import re
            cmid_match = re.search(r"id=(\d+)", page.url)
            if cmid_match:
                cmid = int(cmid_match.group(1))
                page.evaluate("""(targetCmid) => {
                    if (typeof require === 'function') {
                        require(['core/ajax'], function(Ajax) {
                            Ajax.call([{
                                methodname: 'core_completion_update_activity_completion_status_manually',
                                args: { cmid: targetCmid, completed: true }
                            }]);
                        });
                    }
                }""", cmid)
                print(f"[Dispatcher] Verified activity completion via Moodle AJAX POST request (cmid: {cmid})")
        except Exception as e:
            print(f"[Dispatcher Warning] POST completion note: {e}")


    # 4. Record score and benchmark timing
    if success and tracker:
        MoodleWaits.page_ready(page, timeout=3000)
        final_score = tracker.extract_score_from_page(page)
        tracker.record_activity(activity_url, title or page.title(), act_type, score=final_score)

    if benchmark:
        benchmark.record_activity(title or activity_url, time.perf_counter() - act_start, success=success)

    return success


def run():
    parser = argparse.ArgumentParser(description="Poodle High-Performance LMS Automation Agent")
    parser.add_argument("--url", type=str, default="", help="Single target course or activity URL")
    parser.add_argument("--urls", type=str, default="", help="Comma-separated list of course URLs")
    parser.add_argument("--headless", action="store_true", default=Config.HEADLESS, help="Run browser in headless mode")
    parser.add_argument("--refresh-cache", action="store_true", default=False, help="Invalidate course activity cache")
    parser.add_argument("--reset-tracker", action="store_true", default=False, help="Clear completed activities tracking file")
    parser.add_argument("--all", action="store_true", default=False, help="Automatically automate all activities without prompt")
    parser.add_argument("--cli-select", action="store_true", default=False, help="Force CLI selection mode instead of GUI popup")
    args = parser.parse_args()

    print()
    print("=" * 65)
    print("POODLE HIGH-PERFORMANCE LMS AUTOMATION AGENT")
    print("=" * 65)

    # Determine course URLs list
    if args.urls:
        course_urls = [u.strip() for u in args.urls.split(",") if u.strip()]
    elif args.url:
        course_urls = [args.url.strip()]
    elif getattr(Config, "COURSE_URLS", None):
        course_urls = Config.COURSE_URLS
    elif Config.TARGET_URL:
        course_urls = [Config.TARGET_URL]
    else:
        course_urls = []

    if not course_urls:
        print("\n[Error] No target course or activity URL provided!")
        print("Please configure TARGET_URL or COURSE_URLS in your .env file or pass --url <URL>.")
        return

    benchmark = BenchmarkTracker()
    session_mgr = SessionManager()
    activity_cache = ActivityCache()
    tracker = ActivityTracker()

    if args.reset_tracker:
        tracker.clear()
        print("\n[Activity Tracker] Cleared completed activities history via --reset-tracker.")

    # 1. Device Verification
    print("\n[1/4] Verifying VB-CABLE devices...")
    cable_input, cable_output = verify_vb_cable()
    print(f"  Python Output (CABLE Input)  : {cable_input.name} (index {cable_input.index})")
    print(f"  Browser Input (CABLE Output) : {cable_output.name} (index {cable_output.index})")

    # 2. TTS & Audio Router
    print("\n[2/4] Initializing TTS & Audio Router...")
    tts = TTSEngine(rate=Config.TTS_RATE, volume=Config.TTS_VOLUME)
    router = AudioRouter(output_device=cable_input, tts_engine=tts)
    speech_wav = Config.TEMP_DIR / "lms_speech.wav"

    # 3. Launch Persistent Context Chromium
    is_headless = args.headless
    print(f"\n[3/4] Launching Chromium (Headless: {is_headless} | Profile: {PROFILE_DIR})...")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=is_headless,
            permissions=["microphone"],
            args=[
                "--use-fake-ui-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )

        # Inject Shims
        context.add_init_script(path=str(POODLL_SHIM_PATH))
        context.add_init_script(path=str(MEDIA_SHIM_PATH))

        page = context.pages[0] if context.pages else context.new_page()
        page.on("dialog", lambda dialog: (print(f"[Browser Dialog] Auto-accepting: {dialog.message}"), dialog.accept()))

        # Network optimization: Attach resource interceptor
        # net_router = ResourceRouter()
        # net_router.attach_to_page(page)
        net_router = None

        course_reports = []

        # 4. Multi-Course Processing Loop
        for c_idx, target_url in enumerate(course_urls, 1):
            print("\n" + "=" * 65)
            print(f"TARGET [{c_idx}/{len(course_urls)}]: {target_url}")
            print("=" * 65)

            benchmark.start_timer("auth")
            print(f"\n[4/4] Opening Target URL: {target_url}")
            page.goto(target_url, wait_until="domcontentloaded")
            session_mgr.ensure_authenticated(page, context)
            benchmark.stop_timer("auth")

            MoodleWaits.page_ready(page)

            if "/course/view.php" in target_url:
                course_name = extract_course_name(page)
                print("\n" + "#" * 65)
                print(f"COURSE: {course_name}")
                print(f"URL:    {target_url}")
                print("#" * 65)

                # Check Activity Cache
                cached_activities = None if args.refresh_cache else activity_cache.get_activities(target_url)

                if cached_activities:
                    activities = cached_activities
                    print(f"\n[Activity Cache] Loaded {len(activities)} activities directly from cache.")
                else:
                    benchmark.start_timer("course_discovery")
                    activities = extract_course_activities(page, target_url)
                    benchmark.stop_timer("course_discovery")
                    activity_cache.save_activities(target_url, activities)
                    print(f"\n[Course Crawler] Discovered and cached {len(activities)} activities:")

                # Never assume an activity is done from course page checkmarks or 'Mark as done'
                tracker.purge_unverified_completions()

                for i, act in enumerate(activities, 1):
                    status_suffix = ""
                    raw_score = tracker.get_score(act["url"]) if tracker else ""
                    if tracker and tracker.is_completed(act["url"]):
                        status_suffix = f"  --> [ALREADY COMPLETED | Score: {raw_score}]"
                    elif raw_score:
                        min_score = Config.get_threshold(act.get("type", ""))
                        status_suffix = f"  --> [PENDING / RETRY (Score: {raw_score} < {min_score:g}%)]"
                    else:
                        status_suffix = "  --> [PENDING]"
                    print(f"  {i:2d}. [{act.get('type', 'act'):10s}] {act['title']}{status_suffix}")

                # Prompt user for which activities to automate (Checkbox Dialog / CLI with '(all)' option)
                selected_activities = prompt_activity_selection(
                    activities=activities,
                    course_name=course_name,
                    tracker=tracker,
                    headless=is_headless,
                    force_cli=args.cli_select,
                    auto_all=args.all
                )

                if not selected_activities:
                    print(f"\n[Course Crawler] No activities selected for course '{course_name}'. Moving to next course.")
                    course_reports.append({
                        "course_name": course_name,
                        "url": target_url,
                        "completed": 0,
                        "total": len(activities),
                        "selected": 0
                    })
                    continue

                completed = 0
                for i, act in enumerate(selected_activities, 1):
                    print(f"\n{'#' * 65}")
                    print(f"COURSE: {course_name}")
                    print(f"ACTIVITY {i}/{len(selected_activities)}: {act['title']}")
                    print(f"URL: {act['url']}")
                    print(f"{'#' * 65}")

                    # Always dispatch to the live LMS page to verify real marks against threshold.
                    # The activity tracker is strictly for tracking and reporting history.

                    success = dispatch_solver(
                        page=page,
                        activity_url=act["url"],
                        router=router,
                        speech_wav=speech_wav,
                        tracker=tracker,
                        session_mgr=session_mgr,
                        benchmark=benchmark,
                        title=act["title"]
                    )
                    if success:
                        completed += 1

                # Respond with Course Name upon completion
                print("\n" + "=" * 65)
                print(f"[COURSE COMPLETED] Course Name: {course_name}")
                print(f"Course URL: {target_url}")
                print(f"Activities Progress: {completed}/{len(selected_activities)} Completed (Total in Course: {len(activities)})")
                print("=" * 65)

                course_reports.append({
                    "course_name": course_name,
                    "url": target_url,
                    "completed": completed,
                    "total": len(activities),
                    "selected": len(selected_activities)
                })
            else:
                # Direct activity execution
                print("\n[Direct Execution] Solving single targeted activity...")
                dispatch_solver(
                    page=page,
                    activity_url=target_url,
                    router=router,
                    speech_wav=speech_wav,
                    tracker=tracker,
                    session_mgr=session_mgr,
                    benchmark=benchmark
                )

        if course_reports:
            print("\n" + "=" * 65)
            print("ALL COURSES BATCH COMPLETION REPORT")
            print("=" * 65)
            for rep in course_reports:
                print(f"Course Name: {rep['course_name']}")
                print(f"Course URL:  {rep['url']}")
                if rep.get("selected", rep["total"]) != rep["total"]:
                    print(f"Completed:   {rep['completed']}/{rep.get('selected', rep['total'])} selected activities ({rep['total']} total in course)")
                else:
                    print(f"Completed:   {rep['completed']}/{rep['total']} activities")
                print("-" * 65)

        tracker.print_summary()

        # Output Performance Benchmark
        benchmark.print_report(router=net_router)

        if not is_headless:
            print("\nNon-headless mode: Pausing 5 seconds before closing...")
            time.sleep(5)

        context.close()


if __name__ == "__main__":
    run()