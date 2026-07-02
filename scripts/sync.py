import argparse
import datetime
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from collector.config import get_kaken_appid
from collector.kaken import KakenAuthError, KakenClient, match_members, sync_kaken
from collector.metrics import compute_metrics
from collector.openalex import OpenAlexClient
from collector.sync import sync_authors, sync_works
from db.models import get_engine

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sync")


def main() -> None:
    parser = argparse.ArgumentParser(description="OMU researcher data sync")
    parser.add_argument("--db", default="db/researchers.db")
    args = parser.parse_args()

    today = datetime.date.today()
    engine = get_engine(args.db)
    client = OpenAlexClient()

    with Session(engine) as session:
        n_a = sync_authors(session, client, today=today)
        n_w = sync_works(session, client, today=today)
        appid = get_kaken_appid()
        if appid:
            try:
                n_k = sync_kaken(session, KakenClient(appid), today=today)
                n_match = match_members(session)
                logger.info("kaken: grants=%d matched=%d", n_k, n_match)
            except KakenAuthError:
                logger.warning("KAKEN appidが無効のためスキップ（有効化を待って再実行）")
        else:
            logger.warning("KAKEN_APPID未設定のためKAKEN同期をスキップ")
        n_m = compute_metrics(session, today)
        logger.info("done: authors=%d works=%d metrics=%d", n_a, n_w, n_m)


if __name__ == "__main__":
    main()
