import os, random, time, csv, datetime as dt
from pathlib import Path

def ensure_dir(p:str):
    Path(p).mkdir(parents=True, exist_ok=True)

def ts():
    return dt.datetime.utcnow().isoformat()

def rand_dwell(dwell_min:int, dwell_max:int):
    return random.randint(dwell_min, dwell_max)

def out_paths(platform:str, persona:str):
    day = dt.date.today().isoformat()
    root = f"data/logs/{platform}/{persona}/{day}"
    ensure_dir(root)
    return (os.path.join(root, "watched.csv"), os.path.join(root, "recs.csv"))

def write_rows(path, header, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if new: w.writeheader()
        for r in rows: w.writerow(r)

