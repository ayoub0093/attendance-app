"""Make a consistent SQLite backup without overwriting an existing destination."""
import sqlite3
import sys
from pathlib import Path
from app import create_app

if len(sys.argv) != 2:
    raise SystemExit('Usage: python backup.py EXISTING_DIRECTORY/new-backup.db')
target = Path(sys.argv[1]).resolve()
if not target.parent.is_dir():
    raise SystemExit('Choose an existing backup directory.')
# Exclusive creation prevents silently overwriting an earlier backup.
with target.open('xb'):
    pass
app = create_app()
with sqlite3.connect(app.config['DATABASE']) as source, sqlite3.connect(target) as destination:
    source.backup(destination)
print('Backup saved to', target)
