# Diyor Group Dashboard - Claude Assistant Guide

<!-- BEGIN my-skills:stack-overlay -->
## Skill stack overlay

This project's stack-specific command and idiom substitutions for the `my-skills`
plugin live in [.claude/skill-stack-overlay.md](.claude/skill-stack-overlay.md).
Before following any skill from that plugin, read that file and prefer its commands
and conventions over any stack-specific defaults written into the skill itself.
<!-- END my-skills:stack-overlay -->

## Project Overview

- **Name**: Diyor Group Dashboard (`diyorgroup.uz` / `santexnika.onrender.com`)
- **Core Stack**: FastAPI, SQLAlchemy 2.0 (async SQLite), Vanilla JS, Tailwind CSS, aiogram 3.x, MoySklad JSON API 1.2
- **Key Rules**:
  1. `index.html` and `dashboard.html` must remain 100% byte-for-byte identical at all times.
  2. All FastAPI endpoints must be wrapped in `try ... except` blocks with non-blocking error responses.
  3. MoySklad API requests must strictly adhere to rate limits (max 5 req/s) with caching.
  4. Modals require `z-index: 2000` to sit above `.sticky-top-container` (`z-index: 1000`).
  5. Read and obey [PROJECT_RULES.md](PROJECT_RULES.md) before writing any code.
