# Poodll LMS Automation

![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square&logo=python)
![Playwright](https://img.shields.io/badge/playwright-v1.40%2B-green?style=flat-square&logo=playwright)
![AI](https://img.shields.io/badge/AI-Google%20Gemini-orange?style=flat-square&logo=google-gemini)
![Audio](https://img.shields.io/badge/audio-VB--Cable-purple?style=flat-square)
![Issues](https://img.shields.io/github/issues/bsuryaprakash06/Poodll-Automation?style=flat-square)
![Pull Requests](https://img.shields.io/github/issues-pr/bsuryaprakash06/Poodll-Automation?style=flat-square)
![Stars](https://img.shields.io/github/stars/bsuryaprakash06/Poodll-Automation?style=flat-square)

An automated Moodle & Poodll Learning Management System (LMS) automation suite utilizing **Python**, **Playwright**, **VB-Audio Virtual Cable**, and the **Google Gemini API** for speech recognition, interactive video, flashcards, and reading assessments.

> [!IMPORTANT]
> **Quizzes are not supported in this repository.**  
> Standard Moodle quizzes will not work with this automation framework. For comprehensive quiz automation, please refer to the [Moodle-Quiz-Automation](https://github.com/bsuryaprakash06/Moodle-Quiz-Automation) repository.

---

## Support & Sponsorship

If you find this project helpful and want to support its ongoing development, consider giving it a star on GitHub or sponsoring the maintainer!

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Donate-yellow.svg?style=flat-square&logo=buy-me-a-coffee)](https://buymeacoffee.com/shanez)
[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub-ea4aaa.svg?style=flat-square&logo=github-sponsors)](https://github.com/sponsors/bsuryaprakash06)

---

## Key Features

* **Virtual Audio Injection:** Streams synthesized speech directly into Chromium (via Playwright) over **VB-Audio Virtual Cable** on Windows without altering system-wide microphone settings.
* **CloudPoodll SDK Interceptor:** Injects runtime shims (`cloudpoodll_shim.js`) to capture recorder events and route audio directly to Poodll AI evaluators.
* **Broad Activity Coverage:**
  - 📖 **Poodll Read-Aloud (`readaloud`):** Automatic passage extraction, punctuation normalization, speech synthesis, and scoring.
  - 🎥 **H5P & Interactive Video (`hvp`, `h5pactivity`):** Cross-iframe query solving, drag-and-drops, video milestone quizzes, and question completion.
  - 🗂️ **Poodll WordCards (`wordcards`):** Multistage flashcard drills, definition pairing, and vocabulary testing.
  - 🎙️ **Poodll Solo Speaking (`solo`):** AI-powered continuous monologue formulation and audio submission.
  - ⚠️ **Moodle Quizzes (`quiz`):** *Not supported in this suite.* Quizzes will not work with this tool; please refer to [Moodle-Quiz-Automation](https://github.com/bsuryaprakash06/Moodle-Quiz-Automation).
* **Interactive Activity Selector with Multi-Theme Engine:**
  - Grouped view with real-time text filter and master selection toggles.
  - Quick action: **Select Pending Only** to avoid re-attempting finished activities.
  - **Dynamic Theme Switcher (🎨 Theme):**
    - 🌙 **Dark (Default):** Deep charcoal & slate (`#181825` / `#1e1e2e`) with soft blue accents (`#89b4fa`) and dark pill badges.
    - ☀️ **Light:** Clean white & cool gray (`#f8f9fa` / `#ffffff`) with crisp dark text (`#212529`).
    - 🪟 **Transparent (Frosted Glass):** Acrylic glass (`#11111b`) with window opacity `0.85` allowing wallpaper to subtly show through while preserving full contrast.
    - **Instant Runtime Switching:** Updates all widgets, canvas, scroll regions, badges, and window alpha in real time without restarting.
* **Session Persistence:** Securely re-uses authenticated browser cookies to eliminate redundant logins.
* **Performance Telemetry:** Comprehensive benchmark report detailing execution times, AI latencies, and passing scores.

---

## Architecture

```
                    WINDOWS HOST
                         │
                         │
                 ┌───────▼────────┐
                 │   Python TTS   │
                 │ pyttsx3/SAPI5  │
                 └───────┬────────┘
                         │
                         │ WAV / PCM (Direct stream)
                         ▼
                ┌─────────────────┐
                │   AudioRouter   │
                │   sounddevice   │
                └────────┬────────┘
                         │
                         ▼
              CABLE Input (PLAYBACK)
                  VB-Audio Cable
                         │
                ─────────┼─────────
                         │
                         ▼
             CABLE Output (RECORDING)
                  VB-Audio Cable
                         │
                         ▼
              ┌─────────────────────┐
              │      Chromium       │
              │ Playwright Controls │
              │      Browser        │
              └──────────┬──────────┘
                         │
                         │ getUserMedia({ deviceId: { exact: cableOutputId } })
                         ▼
                   Target Website
                         │
                         ▼
               Microphone Recording /
                    Speech AI
```

### Device Mapping Table
| Component | Device Name | Direction | Role |
| :--- | :--- | :--- | :--- |
| **Python Output** | `CABLE Input (VB-Audio Virtual Cable)` | Playback / Output | Python plays synthesized WAV |
| **Browser Input** | `CABLE Output (VB-Audio Virtual Cable)` | Recording / Input | Chromium listens as microphone |
| **Physical Mic** | *Untouched* | - | Real microphone is never captured |

---

## Prerequisites

1. **Python 3.9+** installed on your system.
2. **VB-Audio Virtual Cable** installed on Windows:
   - Download free from [VB-Audio](https://vb-audio.com/Cable/).
   - Ensure `CABLE Input` and `CABLE Output` appear in your Sound Settings.
3. **Google Gemini API Key:**
   - Obtain a free API key from [Google AI Studio](https://aistudio.google.com/api-keys).

---

## Setup and Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/bsuryaprakash06/Poodll-Automation.git
   cd Poodll-Automation
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright browser binaries:**
   ```bash
   playwright install chromium
   ```

4. **Configure environment variables:**
   Copy `.env.example` to `.env`:
   ```bash
   copy .env.example .env
   ```
   Edit `.env` with your credentials:
   ```ini
   LMS_USERNAME=your_username
   LMS_PASSWORD=your_password
   TARGET_URL=https://moodle.your-institution.edu/course/view.php?id=123
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

---

## Verification & Usage

### 1. Verify Audio Pipeline
Run the built-in 6-stage end-to-end audio injection test:
```powershell
python test_audio_pipeline.py
```
This test asserts that:
- `CABLE Input` and `CABLE Output` are recognized.
- SAPI5 synthesizes test WAV audio cleanly.
- Chromium navigates to the WebAudio Analyser page.
- Browser `AnalyserNode` registers audio signal above threshold (`PASS: AUDIO REACHED CHROMIUM`).

### 2. Run Automation Agent
Execute the master orchestration agent:
```powershell
python main.py
```

### Useful CLI Flags:
```powershell
# Specify a target course directly
python main.py --url https://moodle.your-institution.edu/course/view.php?id=123

# Run in headless mode (automatically falls back to interactive CLI selector)
python main.py --headless

# Automate all activities without prompting dialog
python main.py --all

# Force interactive console selector instead of GUI dialog
python main.py --cli
```

---

## Project Structure

```
Poodll-Automation/
├── .github/
│   ├── DISCUSSION_TEMPLATE/     # Ideas & Q&A discussion templates
│   ├── ISSUE_TEMPLATE/          # Bug report & feature request templates
│   ├── FUNDING.yml              # GitHub Sponsors & Buy Me a Coffee configuration
│   └── PULL_REQUEST_TEMPLATE.md # Standardized PR checklist
├── audio/
│   ├── devices.py               # Audio device enumeration & VB-Cable locator
│   ├── router.py                # sounddevice playback engine
│   └── tts.py                   # SAPI5 TTS speech synthesizer
├── auth/
│   └── session.py               # Session storage & cookie persistence
├── browser/
│   ├── automation.py            # Playwright browser controller
│   ├── cloudpoodll_shim.js      # CloudPoodll SDK audio injection shim
│   ├── launcher.py              # Browser process launcher with permission flags
│   ├── media_shim.js            # getUserMedia device constraint shim
│   └── routing.py               # Audio routing coordinator
├── performance/
│   └── benchmark.py             # Telemetry & execution profiling
├── solvers/
│   ├── activity_tracker.py      # Moodle activity progress tracker
│   ├── gemini_generator.py      # Google Gemini AI answer engine
│   ├── hvp_solver.py            # H5P & Interactive Video solver
│   ├── quiz_solver.py           # Standard quiz solver (Deprecated - Use Moodle-Quiz-Automation)
│   ├── readaloud_solver.py      # Poodll Read-Aloud speech solver
│   ├── solo_solver.py           # Poodll Solo speaking solver
│   └── wordcards_solver.py      # Poodll WordCards flashcard drill solver
├── utils/
│   ├── activity_selector.py     # Checkbox dialog with multi-theme switcher (Dark/Light/Glass)
│   ├── cache.py                 # Disk caching layer
│   ├── score_parser.py          # Activity score & mark status parser
│   └── waits.py                 # Resilient DOM condition wait utilities
├── web/
│   └── static/mic_test.html     # WebAudio AnalyserNode test verification harness
├── .env.example                 # Environment configuration template
├── config.py                    # Application configuration loader
├── DISCLAIMER.md                # Legal & educational disclaimer
├── FEATURES.md                  # Comprehensive features documentation
├── LICENSE.md                   # MIT License
├── lms_agent.py                 # Master LMS automation orchestrator
├── main.py                      # CLI entry point
├── requirements.txt             # Project dependencies
├── test_audio_pipeline.py       # Audio pipeline verification suite
└── README.md                    # Project documentation
```

---

## Disclaimer

This software is strictly intended for educational, research, and technical evaluation purposes. Please review [DISCLAIMER.md](DISCLAIMER.md) before using this tool.

---

## License

This project is licensed under the [MIT License](LICENSE.md) - see the LICENSE file for details.
