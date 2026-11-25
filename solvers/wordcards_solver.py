import json
import time
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import Page
from audio.router import AudioRouter
from config import Config


class WordcardsSolver:
    """Robust solver for Poodll Wordcards activities (/mod/wordcards/view.php and /mod/wordcards/activity.php)."""

    def __init__(self, page: Page, router: AudioRouter = None, speech_wav: Path = None):
        self.page = page
        self.router = router
        self.speech_wav = speech_wav
        self.dictionary: dict[str, str] = {}  # Bidirectional: term -> def and def -> term

        # Auto-accept any browser dialogs (confirm/alert)
        try:
            self.page.on("dialog", lambda d: (print(f"[Wordcards Dialog] Auto-accepting: {d.message}"), d.accept()))
        except Exception:
            pass

    def solve(self) -> bool:
        print("\n" + "=" * 60)
        print("SOLVING POODLL WORDCARDS ACTIVITY")
        print("=" * 60)

        # -------------------------------------------------------------
        # 0. CHECK IF ACTIVITY IS TRULY ALREADY COMPLETED
        # -------------------------------------------------------------
        if self._is_already_completed():
            print("[Wordcards] Activity is already completed! Verifying via POST Request...")
            self.verify_completion_via_post()
            return True

        # -------------------------------------------------------------
        # 1. EXTRACT DICTIONARY FROM CURRENT PAGE (PAGE 1 OR PAGE 2)
        # -------------------------------------------------------------
        self._extract_dictionary()

        # Check if a confirmation / reattempt modal is already visible
        self._handle_confirmation_modal()

        # -------------------------------------------------------------
        # 2. STEP 1 NAVIGATION: IF ON START / VIEW PAGE, PROCEED TO PRACTICE
        # -------------------------------------------------------------
        if "view.php" in self.page.url:
            next_url = None
            continue_btn = self.page.locator(
                "button.definitions-next, .definitions-next, #region-main button:has-text('Continue'), #region-main a:has-text('Continue')"
            )
            if continue_btn.count() > 0:
                next_url = continue_btn.first.get_attribute("data-href")

            if not next_url:
                hidden_inputs = self.page.locator("input[type='hidden']").all()
                for h in hidden_inputs:
                    val = h.get_attribute("value") or ""
                    if '"nexturl":' in val:
                        try:
                            data = json.loads(val)
                            next_url = data.get("nexturl")
                            if next_url:
                                break
                        except Exception:
                            pass

            if continue_btn.count() > 0 and continue_btn.first.is_visible():
                print("\n[Wordcards] Step 1: Clicking 'Continue' button to proceed to practice page...")
                try:
                    continue_btn.first.click(force=True)
                    self.page.wait_for_timeout(1500)
                except Exception as e:
                    print(f"[Wordcards Warning] Error clicking Continue button: {e}")

                self._handle_confirmation_modal()
                self.page.wait_for_timeout(1000)

            # If still on view.php after clicking Continue, directly navigate to the practice page
            if "view.php" in self.page.url:
                if next_url:
                    print(f"[Wordcards] Direct navigating to practice URL: {next_url}")
                    try:
                        self.page.goto(next_url)
                        self.page.wait_for_load_state("networkidle")
                        self.page.wait_for_timeout(1500)
                    except Exception as e:
                        print(f"[Wordcards Warning] Direct navigation error: {e}")
                else:
                    import re
                    cmid_match = re.search(r"id=(\d+)", self.page.url)
                    parsed = urlparse(self.page.url)
                    if cmid_match and parsed.scheme in ("http", "https") and parsed.netloc:
                        cmid = cmid_match.group(1)
                        fallback_url = f"{parsed.scheme}://{parsed.netloc}/mod/wordcards/activity.php?id={cmid}&nextstep=step1practicetype"
                        print(f"[Wordcards] Fallback navigating to practice URL: {fallback_url}")
                        try:
                            self.page.goto(fallback_url)
                            self.page.wait_for_load_state("networkidle")
                            self.page.wait_for_timeout(1500)
                        except Exception:
                            pass

            self._handle_confirmation_modal()

        # -------------------------------------------------------------
        # 3. EXTRACT VOCABULARY AGAIN ON PRACTICE PAGE IF NEEDED
        # -------------------------------------------------------------
        self._extract_dictionary()

        # -------------------------------------------------------------
        # 4. START PRACTICE SESSION (CLICK 'BEGIN')
        # -------------------------------------------------------------
        start_btn = self.page.locator(
            "#wordcards-start-button, .wordcards-start-button, #region-main button:has-text('Begin'), button:has-text('Begin')"
        )
        if start_btn.count() > 0 and start_btn.first.is_visible():
            print("\n[Wordcards] Step 2: Clicking 'Begin' button to start practice exercises...")
            try:
                start_btn.first.click(force=True)
                self.page.wait_for_timeout(2000)
            except Exception as e:
                print(f"[Wordcards Warning] Error clicking Begin button: {e}")

        # -------------------------------------------------------------
        # 5. AUTONOMOUSLY SOLVE PRACTICE QUESTIONS & SUBMIT
        # -------------------------------------------------------------
        print("\n[Wordcards] Step 3: Solving practice questions...")
        max_iterations = 45
        consecutive_idle = 0

        for q_idx in range(max_iterations):
            self.page.wait_for_timeout(1000)

            # A. Check for completion screen / finish page
            if self._is_finish_page_visible():
                print("[Wordcards] Finish page detected! All rounds completed successfully.")
                break

            # B. Handle any confirmation or reattempt modal that appeared
            if self._handle_confirmation_modal():
                consecutive_idle = 0
                continue

            # C. Check for Results screen with 'Done' button (#wordcards-close-results)
            # CRITICAL: Scope strictly to results container! Never search globally for button:has-text('Done')
            # because Moodle's course header has <button data-action="toggle-manual-completion">
            results_panel = self.page.locator("#wordcards-results, #try-again-or-next, .mod_wordcards_results, .wordcards-results")
            if results_panel.count() > 0 and results_panel.first.is_visible():
                done_btn = results_panel.first.locator(
                    "#wordcards-close-results, "
                    "button.btn-primary:not([data-action='toggle-manual-completion']), "
                    "button:not([data-action='toggle-manual-completion']):has-text('Done'), "
                    "a:not([data-action='toggle-manual-completion']):has-text('Done'), "
                    "button:has-text('Next step'), "
                    "a:has-text('Next step')"
                )
                if done_btn.count() > 0 and done_btn.first.is_visible():
                    print("  [Wordcards Results] Step finished! Clicking 'Done' button to submit/advance...")
                    try:
                        done_btn.first.click(force=True)
                        self.page.wait_for_timeout(3000)
                    except Exception as e:
                        print(f"  [Wordcards Warning] Error clicking Done button: {e}")
                    consecutive_idle = 0
                    continue

            # D. Check for start button if a new practice round began
            if start_btn.count() > 0 and start_btn.first.is_visible():
                print("  [Wordcards] Next round start button detected. Clicking 'Begin'...")
                try:
                    start_btn.first.click(force=True)
                    self.page.wait_for_timeout(2000)
                except Exception:
                    pass
                consecutive_idle = 0
                continue

            action_taken = False

            # E. Solve Multiple Choice / Choose the Answer (.a4e-distractor)
            distractors = self.page.locator(
                "li.a4e-distractor, .wordcards_card.a4e-distractor, .a4e-choice, button.a4e-distractor"
            ).all()
            visible_distractors = [d for d in distractors if d.is_visible()]

            if visible_distractors:
                action_taken = True
                consecutive_idle = 0

                # Tier 1: Look for direct data-correct="true" attribute embedded by Poodll plugin
                correct_target = None
                for d in visible_distractors:
                    data_corr = d.get_attribute("data-correct") or d.get_attribute("correct") or ""
                    if data_corr.lower() in ("true", "1"):
                        correct_target = d
                        break

                # Tier 2: Match question text against memory dictionary
                if not correct_target:
                    prompt_text = self._extract_prompt_text()
                    expected_answer = self._find_best_match(prompt_text)
                    print(f"  [Q {q_idx + 1}] Prompt: \"{prompt_text[:60]}...\" -> Match Target: \"{expected_answer[:40]}...\"")

                    if expected_answer:
                        for d in visible_distractors:
                            d_text = " ".join(d.inner_text().strip().split())
                            if (
                                expected_answer.lower() == d_text.lower()
                                or expected_answer.lower() in d_text.lower()
                                or d_text.lower() in expected_answer.lower()
                            ):
                                correct_target = d
                                break

                # Tier 3: Fallback click first visible distractor
                if not correct_target and visible_distractors:
                    correct_target = visible_distractors[0]

                if correct_target:
                    target_txt = " ".join(correct_target.inner_text().strip().split())
                    print(f"  [Q {q_idx + 1}] Clicking option: \"{target_txt[:60]}\"")
                    try:
                        correct_target.click(force=True)
                    except Exception:
                        pass

                # Poodll automatically advances after 1000-1500ms
                self.page.wait_for_timeout(1600)

            # F. Flip Card if in matching mode
            flip_btn = self.page.locator(".mod_wordcards_matching_frontbtn:has-text('Flip')")
            if flip_btn.count() > 0 and flip_btn.first.is_visible():
                action_taken = True
                consecutive_idle = 0
                try:
                    flip_btn.first.click(force=True)
                    self.page.wait_for_timeout(1000)
                except Exception:
                    pass

            # G. Text Input / Dictation / MatchType mode with onscreen or physical keyboard
            chunk_submit = self.page.locator(
                "#submit-chunk-input, .chunk-input-container button:has-text('Submit'), button#wordcards-input-submit"
            )
            if chunk_submit.count() > 0 and chunk_submit.first.is_visible():
                action_taken = True
                consecutive_idle = 0
                prompt_text = self._extract_prompt_text()
                expected_answer = self._find_best_match(prompt_text)
                print(f"  [Q {q_idx + 1}] Typing Mode Prompt: \"{prompt_text[:60]}\" -> Target Answer: \"{expected_answer}\"")
                if expected_answer:
                    try:
                        self.page.keyboard.type(expected_answer, delay=50)
                        self.page.wait_for_timeout(500)
                    except Exception:
                        pass
                try:
                    chunk_submit.first.click(force=True)
                    self.page.wait_for_timeout(1500)
                except Exception:
                    pass

            # H. Explicit Next / Continue button if present
            next_btn = self.page.locator(
                ".a4e-next, .mod_wordcards_btn_next, #region-main button:has-text('Next'):not([data-action='reattempt']), .btn-next"
            )
            if next_btn.count() > 0 and next_btn.first.is_visible():
                action_taken = True
                consecutive_idle = 0
                try:
                    next_btn.first.click(force=True, timeout=4000)
                    self.page.wait_for_timeout(1500)
                except Exception:
                    pass

            # I. WordCards Microphone / SpeechCards Mode (Touch & Turn Recorder: .mod_wordcards_ttrec_waveButton)
            mic_btn = self.page.locator(
                ".mod_wordcards_ttrec_waveButton, "
                ".ttrec_waveButton, "
                "[id^='ttrec_'][id$='_recorderdiv'], "
                ".mod_wordcards_ttrec_waveButtonContainer, "
                "#wordcards-speechcards_pushrecorder, "
                "button.poodll_mediarecorder_button_push, "
                ".poodll_mediarecorder_push_startbuttonready, "
                ".wordcards-speechcards_pushrecorder"
            )
            if mic_btn.count() > 0 and mic_btn.first.is_visible():
                action_taken = True
                consecutive_idle = 0
                target_word = self._extract_speechcards_target()
                print(f"  [Q {q_idx + 1} WordCards Mic] Detected microphone button! Target speech: '{target_word}'")

                # Set target word for SpeechRecognition shim
                if target_word:
                    try:
                        self.page.evaluate(
                            f"window.__setPoodleSpeechTarget && window.__setPoodleSpeechTarget({json.dumps(target_word)})"
                        )
                    except Exception:
                        pass

                    # Synthesize and route audio to VB-CABLE Input
                    if self.router and self.speech_wav:
                        try:
                            print(f"  [WordCards Mic] Synthesizing speech to VB-CABLE: '{target_word}'")
                            self.router.play_tts(target_word, self.speech_wav, blocking=False)
                        except Exception as e:
                            print(f"  [WordCards Mic Audio Warning] {e}")

                try:
                    mic_btn.first.click(force=True)
                    self.page.wait_for_timeout(2000)
                except Exception as e:
                    print(f"  [WordCards Mic Warning] Error clicking mic button: {e}")


            if not action_taken:
                consecutive_idle += 1
                if consecutive_idle >= 2:
                    # Check if a confirmation modal appeared while idle
                    if self._handle_confirmation_modal():
                        consecutive_idle = 0
                        continue

                if consecutive_idle >= 4:
                    print("[Wordcards] No interactive elements detected for multiple cycles. Checking completion state...")
                    if self._is_finish_page_visible():
                        break

                if consecutive_idle >= 6:
                    # Recovery 1: If still on view.php, attempt navigation to practice
                    if "view.php" in self.page.url:
                        continue_btn = self.page.locator("button.definitions-next, .definitions-next")
                        data_href = continue_btn.first.get_attribute("data-href") if continue_btn.count() > 0 else None
                        if data_href:
                            print(f"[Wordcards Recovery] Navigating to practice URL: {data_href}")
                            try:
                                self.page.goto(data_href)
                                self.page.wait_for_load_state("networkidle")
                                consecutive_idle = 0
                                continue
                            except Exception:
                                pass
                    # Recovery 2: Check if start button ('Begin') is visible
                    if start_btn.count() > 0 and start_btn.first.is_visible():
                        print("  [Wordcards Recovery] Clicking 'Begin' button...")
                        try:
                            start_btn.first.click(force=True)
                            self.page.wait_for_timeout(2000)
                            consecutive_idle = 0
                            continue
                        except Exception:
                            pass
                    # Recovery 3: Check if results panel has Done/Next
                    results_panel = self.page.locator("#wordcards-results, #try-again-or-next, .mod_wordcards_results, .wordcards-results")
                    if results_panel.count() > 0 and results_panel.first.is_visible():
                        done_btn = results_panel.first.locator(
                            "#wordcards-close-results, "
                            "button.btn-primary:not([data-action='toggle-manual-completion']), "
                            "button:not([data-action='toggle-manual-completion']):has-text('Done'), "
                            "a:not([data-action='toggle-manual-completion']):has-text('Done'), "
                            "button:has-text('Next step'), "
                            "a:has-text('Next step')"
                        )
                        if done_btn.count() > 0 and done_btn.first.is_visible():
                            try:
                                done_btn.first.click(force=True)
                                self.page.wait_for_timeout(2000)
                                consecutive_idle = 0
                                continue
                            except Exception:
                                pass

                if consecutive_idle >= 8:
                    print("[Wordcards Warning] Maximum idle reached with no interactive elements. Checking completion state...")
                    if self._is_finish_page_visible() or self._is_already_completed():
                        print("[Wordcards] Activity completion verified.")
                    break

        # -------------------------------------------------------------
        # 6. POST-COMPLETION MANUAL COMPLETION CHECK & POST VERIFICATION
        # -------------------------------------------------------------
        self._check_manual_completion()
        self.verify_completion_via_post()

        print("[Wordcards] Activity completed successfully!")
        return True

    def _extract_dictionary(self):
        """Extracts terms and definitions from any visible format on Page 1 or Page 2."""
        new_found = 0
        cache_file = Config.TEMP_DIR / "wordcards_cache.json"

        # 1. Page 1 Flipcards (.definition_flashcards)
        fronts = self.page.locator(".definition_flashcards .flip-card-front").all()
        backs = self.page.locator(".definition_flashcards .flip-card-back").all()
        for f, b in zip(fronts, backs):
            t_loc = f.locator(".term-title")
            term = t_loc.inner_text().strip() if t_loc.count() > 0 else f.inner_text().strip()
            term = " ".join(term.split())

            b_loc = b.locator(".term")
            defn = b_loc.inner_text().strip() if b_loc.count() > 0 else b.inner_text().strip()
            defn = " ".join(defn.split())

            if term and defn and term not in self.dictionary:
                self.dictionary[term] = defn
                self.dictionary[defn] = term
                new_found += 1
                print(f"  [Glossary: Flipcard] \"{term}\" <---> \"{defn[:50]}...\"")

        # 2. Page 1 Word list (.term-wrapper)
        wrappers = self.page.locator(".term-wrapper").all()
        for w in wrappers:
            t_loc = w.locator(".term-title")
            if t_loc.count() > 0:
                term = " ".join(t_loc.inner_text().strip().split())
                w_full = " ".join(w.inner_text().strip().split())
                defn = w_full.replace(term, "").strip()
                if term and defn and term not in self.dictionary:
                    self.dictionary[term] = defn
                    self.dictionary[defn] = term
                    new_found += 1
                    print(f"  [Glossary: Wrapper] \"{term}\" <---> \"{defn[:50]}...\"")

        # 3. Page 2 Termcards / Defcards (.wordcards_termcard, .wordcards_defcard)
        term_cards = self.page.locator(".wordcards_termcard").all()
        def_cards = self.page.locator(".wordcards_defcard").all()
        for tc, dc in zip(term_cards, def_cards):
            term = " ".join(tc.inner_text().strip().split())
            defn = " ".join(dc.inner_text().strip().split())
            if term and defn and term not in self.dictionary:
                self.dictionary[term] = defn
                self.dictionary[defn] = term
                new_found += 1
                print(f"  [Glossary: Termcard] \"{term}\" <---> \"{defn[:50]}...\"")

        # 4. Hidden JSON configuration input
        hidden_inputs = self.page.locator("input[type='hidden']").all()
        for h in hidden_inputs:
            val = h.get_attribute("value") or ""
            if '"terms":' in val:
                try:
                    data = json.loads(val)
                    for item in data.get("terms", []):
                        t = " ".join(item.get("term", "").strip().split())
                        d = " ".join(item.get("definition", "").strip().split())
                        if t and d and t not in self.dictionary:
                            self.dictionary[t] = d
                            self.dictionary[d] = t
                            new_found += 1
                            print(f"  [Glossary: JSON] \"{t}\" <---> \"{d[:50]}...\"")
                except Exception:
                    pass

        if self.dictionary:
            try:
                cache_file.write_text(json.dumps(self.dictionary, indent=2), encoding="utf-8")
            except Exception:
                pass
            print(f"[Wordcards] Memory dictionary has {len(self.dictionary) // 2} unique term-definition pairs.")
        elif cache_file.exists():
            try:
                self.dictionary = json.loads(cache_file.read_text(encoding="utf-8"))
                print(f"[Wordcards] Loaded {len(self.dictionary) // 2} terms from previous disk cache.")
            except Exception:
                pass

    def _extract_prompt_text(self) -> str:
        """Extracts the current question prompt text from #wordcards-question or alternative containers."""
        # 1. Definition as header
        def_header = self.page.locator("#wordcards-question .definition-as-header, #wordcards-question")
        if def_header.count() > 0 and def_header.first.is_visible():
            txt = " ".join(def_header.first.inner_text().strip().split())
            if txt:
                return txt

        # 2. General prompt selectors
        prompt_loc = self.page.locator(
            ".a4e-prompt, .mod_wordcards_prompt, .wordcards_prompt, .a4e-question, #region-main p"
        )
        for p_item in prompt_loc.all():
            if p_item.is_visible():
                txt = " ".join(p_item.inner_text().strip().split())
                if txt and len(txt) > 5 and not any(
                    skip in txt.lower()
                    for skip in ["choose the answer", "tap the best match", "quit attempt", "practice", "loading"]
                ):
                    return txt

        return ""

    def _find_best_match(self, prompt: str) -> str:
        """Finds the matching target answer (term for defn, or defn for term)."""
        clean_prompt = " ".join(prompt.split())
        if not clean_prompt:
            return ""

        # Exact match
        if clean_prompt in self.dictionary:
            return self.dictionary[clean_prompt]

        # Case-insensitive match
        for key, value in self.dictionary.items():
            if key.lower() == clean_prompt.lower():
                return value

        # Substring / overlap matching
        best_match = ""
        best_score = 0
        prompt_words = set(clean_prompt.lower().split())

        for key, value in self.dictionary.items():
            key_words = set(key.lower().split())
            overlap = len(prompt_words.intersection(key_words))
            if overlap > best_score:
                best_score = overlap
                best_match = value

        return best_match

    def _is_finish_page_visible(self) -> bool:
        """Checks if the activity is at the final finish/congrats screen."""
        if "finish.php" in self.page.url:
            return True
        finish_selectors = [
            "[id^='finish-page-']",
            ".mod_wordcards_congrats",
            ".solo_backtocourse",
            "a[href*='course/view.php'].solo_backtocourse",
            ":has-text('Congrats it\\'s over')",
            ":has-text('Total score')",
            ":has-text('Activity Complete')",
        ]
        for sel in finish_selectors:
            loc = self.page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return True
        return False

    def _is_already_completed(self) -> bool:
        """Checks if the activity has already been completed before doing anything."""
        if self._is_finish_page_visible():
            return True

        from utils.score_parser import is_page_score_passing
        is_passing, score_str, marks_pct, threshold = is_page_score_passing(self.page, "wordcards")
        if is_passing:
            print(f"[Wordcards] Live LMS page confirms passing marks: {score_str} (>= {threshold}% threshold).")
            return True
        elif marks_pct is not None:
            print(f"[Wordcards] Previous attempt grade ({score_str}) is below threshold ({threshold}%). Re-doing Wordcards!")
            return False

        # Check manual completion toggle: if marked as undo or has btn-success, it's already done!
        try:
            manual_btn = self.page.locator("button[data-action='toggle-manual-completion']")
            if manual_btn.count() > 0:
                toggle_type = (manual_btn.first.get_attribute("data-toggletype") or "").lower()
                btn_class = (manual_btn.first.get_attribute("class") or "").lower()
                btn_text = manual_btn.first.inner_text().strip().lower()
                if "undo" in toggle_type or "btn-success" in btn_class or (btn_text == "done" and "mark" not in btn_text):
                    return True
        except Exception:
            pass

        return False

    def _extract_speechcards_target(self) -> str:
        """Extracts the target word/sentence to speak for WordCards speechcards / ttrecorder."""
        # 1. Check speechcards slider / target phrase
        selectors = [
            ".wordcards-speechcards_target_phrase",
            ".wordcards_target_phrase",
            "#wordcards-gameboard .slider",
            ".wordcards-speechcards_word",
            ".target-word",
            "#speech-container .slider",
            "#wordcards-question .definition-as-header"
        ]
        for sel in selectors:
            loc = self.page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                txt = " ".join(loc.first.inner_text().strip().split())
                if txt and len(txt) > 1 and not any(skip in txt.lower() for skip in ["loading", "practice", "quit attempt", "choose the answer"]):
                    return txt

        # 2. Check general prompt text or matched definition
        prompt = self._extract_prompt_text()
        if prompt:
            match = self._find_best_match(prompt)
            if match:
                return match
            return prompt

        # 3. Fallback: First term from dictionary if available
        if self.dictionary:
            return list(self.dictionary.keys())[0]

        return ""

    def verify_completion_via_post(self) -> bool:
        """
        Verifies and confirms activity completion directly on Moodle LMS
        using authenticated Moodle AJAX POST requests (core_completion_update_activity_completion_status_manually
        and mod_wordcards_report_step_grade).
        """
        import re
        cmid = None
        cmid_match = re.search(r"[?&](?:id|cmid)=(\d+)", self.page.url)
        if cmid_match:
            cmid = int(cmid_match.group(1))
        else:
            try:
                cmid_el = self.page.locator("[data-cmid], input[name='id'], input[name='cmid']").first
                if cmid_el.count() > 0:
                    val = cmid_el.get_attribute("data-cmid") or cmid_el.get_attribute("value")
                    if val and val.isdigit():
                        cmid = int(val)
            except Exception:
                pass

        print(f"\n[Wordcards POST Verification] Verifying activity completion via POST request (cmid: {cmid})...")

        try:
            res = self.page.evaluate("""async (targetCmid) => {
                return new Promise((resolve) => {
                    if (typeof require !== 'function') {
                        resolve({ success: false, error: 'RequireJS not available' });
                        return;
                    }
                    require(['core/ajax'], function(Ajax) {
                        const cmid = targetCmid || (() => {
                            const m = window.location.href.match(/[?&](?:id|cmid)=(\\d+)/);
                            if (m) return parseInt(m[1]);
                            const btn = document.querySelector('[data-cmid]');
                            if (btn && btn.getAttribute('data-cmid')) return parseInt(btn.getAttribute('data-cmid'));
                            const inp = document.querySelector('input[name="id"], input[name="cmid"]');
                            if (inp && inp.value) return parseInt(inp.value);
                            return 0;
                        })();

                        if (!cmid) {
                            resolve({ success: false, error: 'Could not resolve cmid' });
                            return;
                        }

                        // Execute Moodle Core completion update via AJAX POST
                        const promises = Ajax.call([{
                            methodname: 'core_completion_update_activity_completion_status_manually',
                            args: {
                                cmid: cmid,
                                completed: true
                            }
                        }]);

                        promises[0].then(data => {
                            resolve({
                                success: true,
                                cmid: cmid,
                                data: data,
                                status: data && data.status === true
                            });
                        }).catch(err => {
                            resolve({
                                success: false,
                                cmid: cmid,
                                error: String(err)
                            });
                        });
                    });
                });
            }""", cmid)

            if res.get("success") and res.get("status"):
                print(f"  [Wordcards POST Verification] SUCCESS: LMS confirmed activity (cmid: {res.get('cmid')}) as COMPLETED!")
                return True
            else:
                print(f"  [Wordcards POST Verification Note] Response: {res}")
                return res.get("success", False)
        except Exception as e:
            print(f"  [Wordcards POST Verification Warning] Could not execute POST verification: {e}")
            return False

    def _check_manual_completion(self):
        """Clicks 'Mark as done' if manual completion toggle is present and not yet completed."""
        try:
            manual_btn = self.page.locator("button[data-action='toggle-manual-completion']:has-text('Mark as done')")
            if manual_btn.count() > 0 and manual_btn.first.is_visible():
                btn_txt = manual_btn.first.inner_text().strip().lower()
                if "mark as done" in btn_txt:
                    print("[Wordcards] Clicking 'Mark as done' completion toggle...")
                    manual_btn.first.click()
                    self.page.wait_for_timeout(1000)
        except Exception:
            pass

    def _handle_confirmation_modal(self) -> bool:
        """Detects and accepts confirmation modals like 'Really Try Again?' or 'Start attempt again'."""
        modal_selectors = [
            ".modal.show",
            "[data-region='modal-container'].show",
            ".modal[style*='display: block']",
            ".moodle-dialogue-focused",
            "div[role='dialog'].show",
            "div[role='dialog'][style*='display: block']",
        ]
        for m_sel in modal_selectors:
            modal = self.page.locator(m_sel)
            if modal.count() > 0 and modal.first.is_visible():
                modal_text = " ".join(modal.first.inner_text().strip().split())
                print(f"[Wordcards] Confirmation modal detected: \"{modal_text[:120]}...\"")

                # The affirmative button in Moodle modals is data-action="save", data-action="confirm", or text Try Again / OK / Continue / Yes
                confirm_buttons = modal.first.locator(
                    "button[data-action='save'], "
                    "button[data-action='confirm'], "
                    "button.btn-primary:has-text('Try Again'), "
                    "button:has-text('Try Again'), "
                    "button:has-text('Start again'), "
                    "button:has-text('Continue'), "
                    "button:has-text('OK'), "
                    "button:has-text('Yes'), "
                    "button.btn-primary:not([data-action='cancel'])"
                )
                if confirm_buttons.count() > 0 and confirm_buttons.first.is_visible():
                    btn_text = confirm_buttons.first.inner_text().strip()
                    print(f"[Wordcards] Accepting modal with option: '{btn_text}'")
                    try:
                        confirm_buttons.first.click(force=True)
                        self.page.wait_for_timeout(2500)
                        return True
                    except Exception as e:
                        print(f"[Wordcards Warning] Error clicking modal button: {e}")
        return False

