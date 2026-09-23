import sqlite3
import os
import sys

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")

db_paths = ["backend/diyorgroup.db", "diyorgroup.db"]

workplaces_to_seed = [
    (1, "Марказий дўкон (Бухоро)", "Бухоро ш., Ибн Сино кўчаси", 39.748992, 64.432118, 150.0, 1),
    (2, "Piramit Tower (Тошкент, Бобур кўчаси)", "Тошкент ш., Яккасарой т., Бобур кўчаси, 44B", 41.281213, 69.254539, 300.0, 1),
]

for db in db_paths:
    if not os.path.exists(db):
        continue
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    # Check if table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='workplaces'")
    if not cur.fetchone():
        conn.close()
        continue

    for wp_id, name, addr, lat, lon, rad, active in workplaces_to_seed:
        cur.execute("SELECT id FROM workplaces WHERE id = ?", (wp_id,))
        exists = cur.fetchone()
        if exists:
            cur.execute("""
                UPDATE workplaces 
                SET name = ?, address = ?, latitude = ?, longitude = ?, radius_meters = ?, is_active = ?
                WHERE id = ?
            """, (name, addr, lat, lon, rad, active, wp_id))
            print(f"[{db}] Updated workplace ID {wp_id}: {name}")
        else:
            cur.execute("""
                INSERT INTO workplaces (id, name, address, latitude, longitude, radius_meters, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """, (wp_id, name, addr, lat, lon, rad, active))
            print(f"[{db}] Inserted workplace ID {wp_id}: {name}")
    valid_ids = tuple(x[0] for x in workplaces_to_seed)
    cur.execute(f"DELETE FROM workplaces WHERE id NOT IN ({','.join('?' for _ in valid_ids)})", valid_ids)
    conn.commit()

    cur.execute("SELECT id, name, latitude, longitude, radius_meters, is_active FROM workplaces")
    print(f"--- Current workplaces in {db} ---")
    for row in cur.fetchall():
        print(" ", row)
    conn.close()
