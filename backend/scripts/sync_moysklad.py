"""CLI script for running MoySklad stock & product synchronization.

Usage:
    python scripts/sync_moysklad.py
"""
import sys
import os
import asyncio
import json

# Force UTF-8 stdout for Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.moysklad_sync import sync_moysklad_stock_and_products
from services.moysklad_client import MoySkladClient

async def main():
    print("=" * 70)
    print("  DIYORGROUP (TZ v2.4) - СИНХРОНИЗАЦИЯ МОЙСКЛАД (MDM & ОСТАТКИ)")
    print("=" * 70)
    
    client = MoySkladClient()
    conn_info = await client.test_connection()
    await client.close()
    
    status_icon = "[ПОДКЛЮЧЕНО]" if conn_info.get("success") else "[ОЖИДАНИЕ ДАННЫХ В .ENV]"
    print(f"Статус соединения: {status_icon}")
    print(f"Тип авторизации: {conn_info.get('auth_type')}")
    if conn_info.get("organization_name"):
        print(f"Организация: {conn_info.get('organization_name')}")
    if conn_info.get("error"):
        print(f"Примечание: {conn_info.get('error')}")
    print("-" * 70)
    
    print("Запуск процесса синхронизации товаров и остатков...")
    result = await sync_moysklad_stock_and_products(dry_run=False)
    
    print("\nРЕЗУЛЬТАТ СИНХРОНИЗАЦИИ:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
