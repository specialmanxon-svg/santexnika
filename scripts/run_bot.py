"""Diyor Group — Telegram GPS Attendance Bot Runner.

Run standalone polling:
    python scripts/run_bot.py
"""
import sys
import os
import asyncio

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr:
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from config import settings
from services.telegram_bot_service import create_bot_and_dispatcher

async def main():
    print("=" * 60)
    print("🚀 Diyor Group — Telegram GPS Attendance Bot ишга тушмоқда...")
    print(f"📍 Дўкон координаталари: {settings.STORE_LAT}, {settings.STORE_LON}")
    print(f"📏 Максимал масофа: {int(settings.MAX_DISTANCE_METERS)} метр")
    print(f"⏰ Иш бошланиш вақти: {settings.store_work_start_hour}:00")
    print(f"🤖 Bot Token: {settings.telegram_bot_token[:12]}...")
    print("=" * 60)

    bot, dp = create_bot_and_dispatcher()
    while True:
        try:
            me = await bot.get_me()
            print(f"✅ Telegram Bot уланди: @{me.username} ({me.full_name})")
            print("📡 GPS давомад қабул қилишга тайёр...")
            await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
        except asyncio.CancelledError:
            print("🛑 Бот тўхтатилди.")
            break
        except Exception as e:
            print(f"⚠️ Тармоқ ёки Telegram хатолиги: {e}. 5 сониядан кейин қайта уланади...")
            await asyncio.sleep(5)
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())

