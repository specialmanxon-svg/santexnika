"""
Proxy module to expose root bot.py inside backend/ directory.
Ensures compatibility when uvicorn is launched with `--app-dir backend`.
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import bot
from bot import *
from bot import run_bot_polling
