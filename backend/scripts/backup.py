"""Ежедневная резервная копия БД (cron: 0 3 * * * python scripts/backup.py).
Использует онлайн-бэкап sqlite3, поэтому безопасна при работающем сервере."""
import os
import sqlite3
import sys
from datetime import datetime

src = os.environ.get("PARKCITY_DB", "parkcity.sqlite3")
dst_dir = sys.argv[1] if len(sys.argv) > 1 else "backups"
os.makedirs(dst_dir, exist_ok=True)
dst = os.path.join(dst_dir, f"parkcity-{datetime.now():%Y%m%d-%H%M%S}.sqlite3")
a, b = sqlite3.connect(src), sqlite3.connect(dst)
a.backup(b)
b.close(); a.close()
print("backup ->", dst)
