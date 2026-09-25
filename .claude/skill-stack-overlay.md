# Skill stack overlay

> Stack-specific substitutions for the `agent-skills` / `pandax54/skils` repository in this project.
> Skills and agents should prefer the commands, conventions, and constraints below over any defaults
> baked into skill texts. Generated for Diyor Group Dashboard on 2026-09-25.

## Stack

- **Backend**: Python 3.11, FastAPI (`backend/main.py`), SQLAlchemy 2.0 (async with `aiosqlite`), Pydantic v2, APScheduler (`backend/workers/task_worker.py`), aiogram 3.31.0 (`bot.py` / `backend/bot.py`), Whisper/Faster-Whisper (`backend/services/task_parser.py`)
- **Frontend**: Vanilla JavaScript (ES6+), Tailwind CSS (CDN), FontAwesome 6, Lucide Icons, SheetJS (XLSX), Chart.js
- **Database**: SQLite (`diyorgroup.db` / `backend/diyorgroup.db` via async SQLAlchemy), PostgreSQL ready
- **External Integrations**: MoySklad JSON API 1.2 (`backend/services/moysklad_client.py`), Telegram Bot API (aiogram 3.x), Bitrix24
- **Dual HTML Rule**: `index.html` and `dashboard.html` are parallel entrypoints and MUST be kept 100% byte-for-byte synchronized!
- **Runtime**: Windows PowerShell, Python virtualenv at `.\.venv\Scripts\python.exe`, Node.js v24+

---

## Canonical commands

| Action | Command | Notes |
|---|---|---|
| **Python Env** | `.\.venv\Scripts\python.exe` | Always run Python tools via this virtualenv |
| **Install backend deps** | `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` | Core packages in root / `backend/requirements.txt` |
| **Run backend server** | `.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload` | Or `run_dashboard.bat` |
| **Run Telegram bot** | `.\.venv\Scripts\python.exe bot.py` | Or `run_bot.bat` |
| **Check HTML/JS syntax** | `node -c <file.js>` or `node scripts/verify_html_sync.js` | Validate syntax without full build step |
| **Verify HTML Parity** | `node -e "const fs=require('fs'); if(fs.readFileSync('index.html','utf8')!==fs.readFileSync('dashboard.html','utf8')){console.error('MISMATCH!'); process.exit(1);} else console.log('OK: in sync');"` | Must pass after EVERY frontend edit |
| **Typecheck (TMA)** | `cd tma && npm run build` | Only for `tma/` subproject |
| **Format / Lint** | Python: `.\.venv\Scripts\python.exe -m ruff check .` (or standard AST check `python -m py_compile <file>`) | Verify syntax validity |

---

## Testing Conventions

- **Python Tests**: Run single-file or custom verification scripts with `.\.venv\Scripts\python.exe <script.py>`.
- **Syntax Verification**: Always run `python -m py_compile <path_to_file>` after modifying any `.py` file.
- **Frontend Verification**: Always test with `node -c` (for standalone scripts) and execute DOM parity checks between `index.html` and `dashboard.html`.
- **Mocking / Integrations**: Never perform live destructive writes against MoySklad or Telegram APIs during automated verification; use mocked responses or dedicated test entity IDs.

---

## Core Architecture Protocols & Safety Rules

### 1. Backend Endpoint Safety (Zero Downtime / Graceful Degradation)
- **Always wrap in `try ... except Exception as e`**: Every FastAPI route handler MUST catch exceptions, log the stack trace with `logger.error`, and return a graceful fallback response (e.g. `{"success": false, "error": str(e), "data": []}`) instead of throwing an unhandled 500.
- **Never block the event loop**:
  - Heavy synchronous computations (ABC/XYZ matrix calculations, large Excel parsing) MUST be dispatched to thread pools via `asyncio.to_thread` or Celery/background workers.
  - Network requests to external services (MoySklad, Telegram, Bitrix) MUST use `httpx.AsyncClient` with explicit timeouts (10s connect, 30s read).

### 2. MoySklad API Protection (Rate Limits & Caching)
- **Strict Rate Limiting**: MoySklad enforces a maximum of 5 requests per second. Always throttle bursts.
- **Aggressive Caching**:
  - Heavy queries (stock balances, 180-day sales history, product catalog) MUST utilize in-memory or disk caches with TTL (e.g. 5–15 minutes).
  - Use background worker sync or warmup routines rather than synchronous live fetches on page load.
- **Pagination & Batching**: Never request more than 1,000 items in a single request; iterate with `limit=1000&offset=N`.

### 3. Telegram Bot (aiogram 3.x) Stability
- **Dispatcher & Router Architecture**: All handlers belong to routers included in the main dispatcher.
- **FSM State Safety**: Ensure `state.clear()` or `state.set_state()` is called cleanly; handle cancellation commands (`/cancel`) at all steps.
- **Multi-language & NLP**: Support Uzbek (Latin and Cyrillic) and Russian in text and Whisper voice transcription (`backend/services/task_parser.py`).

### 4. Frontend UI/UX & CSS Layering Rules
- **Sticky Header Integrity**:
  - Header, navigation tabs, and period filters are grouped in `.sticky-top-container`.
  - `.sticky-top-container` has `z-index: 1000` (or `z-40`).
- **Modal Dialogs & Drawers**:
  - Modals MUST have `z-index: 2000` (or higher) to sit completely above sticky headers and floating badges.
  - Modals MUST support closing via backdrop click, the `Esc` key, and an accessible close button (`pointer-events: auto`).
- **Universal Pagination & Filtering**:
  - Any table or data list supporting large volume must implement the standard pagination widget (10, 50, 100 items per page with Prev/Next/Numbered controls).
  - ABC/XYZ matrix filter chips (`AX`, `AY`, `AZ`, `BX`, `BY`, `BZ`, `CX`, `CY`, `CZ`) must filter rows without triggering redundant network requests.

### 5. `index.html` <-> `dashboard.html` Parity Rule
- Every change made to `index.html` MUST be copied simultaneously to `dashboard.html` (or vice-versa).
- A diff between the two files MUST always be completely empty: `git diff --no-index index.html dashboard.html` must return 0 lines.

---

## Per-skill Notes

- **adapt-skills-to-stack**: Consult this overlay for all substitutions.
- **tdd**: Replace npm/Vitest with Python verification scripts and AST compilation checks.
- **verify / task-review / do-work**:
  1. Check Python syntax: `.\.venv\Scripts\python.exe -m py_compile <edited_files>`
  2. Verify dual-HTML parity: `fc index.html dashboard.html` or Node.js comparison script.
  3. Validate endpoints return valid JSON and never raise unhandled 500s.
- **ui-ux-pro-max**: Follow Tailwind CSS patterns already present in `index.html`; respect `.sticky-top-container` (`z-index: 1000`) and modal (`z-index: 2000`) hierarchies.
- **command-safety**: Windows PowerShell environment. Never issue Linux-specific paths or chained `cd` commands. Use Windows paths or forward slashes.
