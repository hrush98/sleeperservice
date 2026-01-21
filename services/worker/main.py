import logging

from shared.config import settings
from shared.db import SessionLocal
from worker.poller import run_poll_loop


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    run_poll_loop(SessionLocal, settings.polymarket_base_url, settings.worker_poll_interval_seconds)


if __name__ == "__main__":
    main()
