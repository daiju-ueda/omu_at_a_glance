import argparse
import datetime
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from collector.metrics import compute_metrics
from collector.openalex import OpenAlexClient
from collector.sync import sync_authors, sync_works
from db.models import SyncState, get_engine

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sync")


def main() -> None:
    parser = argparse.ArgumentParser(description="OMU researcher data sync")
    parser.add_argument("mode", choices=["full", "incremental"])
    parser.add_argument("--db", default="db/researchers.db")
    args = parser.parse_args()

    today = datetime.date.today()
    engine = get_engine(args.db)
    client = OpenAlexClient()

    with Session(engine) as session:
        since = None
        if args.mode == "incremental":
            state = session.get(SyncState, "works")
            if state and state.last_synced_at:
                since = state.last_synced_at
            else:
                logger.info("sync_stateが無いためfullにフォールバック")
        n_a = sync_authors(session, client, today=today, since=since)
        n_w = sync_works(session, client, today=today, since=since)
        n_m = compute_metrics(session, today)
        logger.info("done: authors=%d works=%d metrics=%d", n_a, n_w, n_m)


if __name__ == "__main__":
    main()
