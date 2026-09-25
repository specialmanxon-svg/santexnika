"""Verification test script for all Diyor Group Dashboard API endpoints."""
import asyncio
import sys
from pathlib import Path

# Add backend and root to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))
sys.path.insert(0, str(root_dir))

from httpx import AsyncClient, ASGITransport
from backend.main import app

ENDPOINTS = [
    ("GET", "/health"),
    ("GET", "/api/v1/status"),
    ("GET", "/dashboard"),
    ("GET", "/api/v1/warehouse/brands"),
    ("GET", "/api/v1/warehouse/non-liquid"),
    ("GET", "/api/v1/warehouse/low-stock"),
    ("GET", "/api/v1/warehouse/low-stock-by-suppliers"),
    ("GET", "/api/v1/debts/overview"),
    ("GET", "/api/v1/debts/debtors"),
    ("GET", "/api/v1/debts/creditors"),
    ("GET", "/api/v1/debts/blocked"),
    ("GET", "/api/v1/cashflow/summary"),
    ("GET", "/api/v1/finance/cashflow-summary"),
    ("GET", "/api/v1/finance/expenses-by-category"),
    ("GET", "/api/v1/finance/brand-margin"),
    ("GET", "/api/v1/hr/overview"),
    ("GET", "/api/v1/hr/timesheet"),
    ("GET", "/api/v1/hr/sales-kpi"),
    ("GET", "/api/v1/partners/overview"),
    ("GET", "/api/v1/tasks"),
    ("GET", "/api/v1/tasks/list"),
]

async def main():
    print("=" * 70)
    print("STARTING FULL ENDPOINT VERIFICATION")
    print("=" * 70)
    
    transport = ASGITransport(app=app)
    results = []
    
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for method, endpoint in ENDPOINTS:
            try:
                if method == "GET":
                    resp = await client.get(endpoint, timeout=15.0)
                else:
                    resp = await client.post(endpoint, timeout=15.0)
                
                status = resp.status_code
                content_type = resp.headers.get("content-type", "")
                is_json = "application/json" in content_type
                
                body_preview = ""
                if is_json:
                    try:
                        data = resp.json()
                        if isinstance(data, dict):
                            keys = list(data.keys())[:6]
                            body_preview = f"Keys: {keys}"
                        elif isinstance(data, list):
                            body_preview = f"List of {len(data)} items"
                    except Exception:
                        body_preview = resp.text[:60]
                else:
                    body_preview = f"Size: {len(resp.content)} bytes"
                
                ok = 200 <= status < 300
                res_str = f"[{'PASS' if ok else 'FAIL'}] {method:4} {endpoint:45} Status: {status} | {body_preview}"
                print(res_str)
                results.append((endpoint, status, ok, None))
            except Exception as e:
                res_str = f"[ERR ] {method:4} {endpoint:45} Error: {str(e)}"
                print(res_str)
                results.append((endpoint, 500, False, str(e)))

    print("=" * 70)
    passed = sum(1 for _, _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"SUMMARY: {passed} passed, {failed} failed out of {len(results)} tested endpoints.")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
