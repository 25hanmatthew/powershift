import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

class Cache:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.getenv('CACHE_DIR', '.cache'))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path=self.directory/'powershift.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL)')
    def connect(self): return sqlite3.connect(self.path, timeout=30)
    def get(self, key):
        with self.connect() as db:
            row=db.execute('SELECT payload, created_at FROM cache WHERE key=?',(key,)).fetchone()
        return {'data':json.loads(row[0]), 'created_at':row[1]} if row else None
    def set(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',(key,json.dumps(value,allow_nan=False),datetime.now(timezone.utc).isoformat()))

def analysis_key(plan):
    # Priorities and hard-filter thresholds never invalidate physical measurements.
    files={}
    for key in ('HIFLD_GEOJSON','PADUS_GEOJSON'):
        p=Path(os.getenv(key, 'data/private/missing'))
        files[key]=[str(p),p.stat().st_size,p.stat().st_mtime_ns] if p.is_file() else None
    if plan.region=='us':
        from .national_ground import fingerprint
        files['national']=fingerprint()
    payload={'region':plan.region,'polygon':plan.polygon,'center':plan.center,'radius':plan.radius_km,
             'dates':[plan.start_date,plan.end_date],'mode':plan.mode,'version':4,'files':files}
    return 'analysis:'+hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
