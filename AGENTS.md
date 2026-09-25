# Diyor Group — AI Agent Workspace Guide (AGENTS.md)

This workspace contains specialized agent skills, protocols, and architectural rules for working on the Diyor Group Dashboard project.

---

## 1. Agent Skills Library (`.agent/skills/`)

The repository integrates curated development skills located in `.agent/skills/`:
- **`adapt-skills-to-stack`**: Guides adapting general skills to this project's stack.
- **`command-safety`**: Safety guidelines for running shell commands in Windows environments.
- **`ui-ux-pro-max`**: Design and styling guidelines for responsive, accessible dashboards.
- **`verify` / `task-review` / `do-work`**: Rigorous multi-step execution and verification workflows.
- **`tdd`**: Test-driven and verification-first methodology.

> **CRITICAL**: Never edit files inside `.agent/skills/` directly. All adaptations must be made via `.claude/skill-stack-overlay.md`, `PROJECT_RULES.md`, or project-local configurations.

---

## 2. Core Operational Constraints for AI Agents

Whenever you are assigned a task on this codebase, you MUST follow these constraints:

1. **Dual HTML Parity**:
   `index.html` and `dashboard.html` must always be identical byte-for-byte. If you modify one, you MUST apply identical changes to the other. Verify using:
   ```powershell
   node -e "const fs=require('fs'); if(fs.readFileSync('index.html','utf8')!==fs.readFileSync('dashboard.html','utf8')){console.error('MISMATCH!'); process.exit(1);} else console.log('OK: in sync');"
   ```

2. **Endpoint Fault-Tolerance**:
   Every FastAPI route in `backend/api/v1/` MUST be wrapped in a `try ... except Exception as e` block and return a safe JSON structure (with `success: false` on error), never allowing an unhandled 500 or freezing the frontend.

3. **MoySklad Rate Limits & Caching**:
   MoySklad API requests must not exceed 5 req/s. Always use caching for heavy queries (stock levels, 180-day sales, product matrix) and prevent synchronous event-loop blocks.

4. **UI Z-Index Hierarchy**:
   - Sticky top container (`header`, `navbar`, `period-filter`): `z-index: 1000` (`z-40`).
   - Modals and dialog overlays: `z-index: 2000` (`z-50`). Buttons inside modals must be interactive (`pointer-events: auto`).

5. **Python Verification**:
   Always verify edited Python files with `.\.venv\Scripts\python.exe -m py_compile <path>` before finishing.

---

## 3. Reference Files

- [PROJECT_RULES.md](PROJECT_RULES.md): Complete engineering rules and standards in Uzbek.
- [.claude/skill-stack-overlay.md](.claude/skill-stack-overlay.md): Canonical stack commands and idioms.
- [CLAUDE.md](CLAUDE.md): Claude Assistant entrypoint and managed overlay block.
