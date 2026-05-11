"""
Scheduler — runs all regulatory document workers on their configured intervals.

Usage:
    python run_all.py            # run all workers continuously
    python run_all.py --once     # run all workers once and exit
    python run_all.py --worker cbu   # run only one worker
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

import config
from workers.cbu_worker    import CBUWorker
from workers.lex_worker    import LexWorker
from workers.eurlex_worker import EurLexWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_all")

WORKERS = {
    "cbu":    (CBUWorker,    config.CBU_INTERVAL_HOURS),
    "lex":    (LexWorker,    config.LEX_INTERVAL_HOURS),
    "eurlex": (EurLexWorker, config.EURLEX_INTERVAL_HOURS),
}


def _run_loop(name: str, cls, interval_hours: float) -> None:
    logger.info("Starting worker: %s (interval=%.1fh)", name, interval_hours)
    worker = cls()
    worker.run_loop(interval_hours=interval_hours)


def run_all_continuous(selected: list[str]) -> None:
    threads = []
    for name, (cls, interval) in WORKERS.items():
        if selected and name not in selected:
            continue
        t = threading.Thread(
            target=_run_loop,
            args=(name, cls, interval),
            name=f"worker-{name}",
            daemon=True,
        )
        t.start()
        threads.append(t)
        time.sleep(2)  # stagger startup

    logger.info("All workers started. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down.")


def run_all_once(selected: list[str]) -> None:
    for name, (cls, _) in WORKERS.items():
        if selected and name not in selected:
            continue
        logger.info("Running %s once...", name)
        try:
            worker = cls()
            count = worker.run_once()
            logger.info("%s: %d new/updated documents", name, count)
        except Exception as exc:
            logger.error("%s failed: %s", name, exc, exc_info=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Regulatory document workers")
    parser.add_argument("--once",   action="store_true", help="Run once and exit")
    parser.add_argument("--worker", action="append",     help="Run specific worker(s)", dest="workers")
    args = parser.parse_args()

    selected = args.workers or []
    if selected:
        invalid = [w for w in selected if w not in WORKERS]
        if invalid:
            print(f"Unknown workers: {invalid}. Valid: {list(WORKERS)}")
            sys.exit(1)

    if args.once:
        run_all_once(selected)
    else:
        run_all_continuous(selected)


if __name__ == "__main__":
    main()
