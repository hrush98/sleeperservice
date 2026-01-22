import logging

from shared.config import settings
from shared.db import SessionLocal
from worker.mapping_resolver import resolve_mappings
from worker.poller import poll_once as polymarket_poll_once
from worker.reference_ingest import poll_fixtures_and_odds
from worker.shadow_engine import emit_shadow_signals


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    from time import sleep

    while True:
        try:
            with SessionLocal() as session:
                polymarket_poll_once(session, base_url=settings.polymarket_base_url)
                poll_fixtures_and_odds(
                    session=session,
                    base_url=settings.oddspapi_base_url,
                    api_key=settings.odds_api_key,
                )
                resolve_mappings(session=session)
                emit_shadow_signals(
                    session=session,
                    gap_threshold=settings.shadow_gap_threshold,
                    min_event_interval_seconds=settings.shadow_min_event_interval_seconds,
                    min_gap_delta=settings.shadow_min_gap_delta,
                )
                session.commit()
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("Worker iteration failed")
        logging.getLogger(__name__).info("Sleeping for %s seconds", settings.worker_poll_interval_seconds)
        sleep(settings.worker_poll_interval_seconds)


if __name__ == "__main__":
    main()
