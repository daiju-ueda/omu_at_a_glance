import argparse
import datetime
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from collector.config import get_kaken_appid
from collector.dedup import apply_dedup
from collector.kaken import KakenAuthError, KakenClient, match_members, sync_kaken
from collector.metrics import compute_metrics
from collector.openalex import OpenAlexClient
from collector.roster import RosterClient, match_roster, sync_profiles, sync_roster
from collector.sync import sync_authors, sync_works
from db.models import get_engine

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpxはINFOでURL全体（クエリのappid含む）をログに出すため抑制する
logging.getLogger("httpx").setLevel(logging.WARNING)
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
        try:
            n_dedup = apply_dedup(session, today=today)
            logger.info("dedup: aliases=%d", n_dedup)
        except Exception:
            session.rollback()
            logger.exception("dedupに失敗（他ステージは継続）")
        appid = get_kaken_appid()
        if appid:
            try:
                n_k = sync_kaken(session, KakenClient(appid), today=today)
                n_match = match_members(session)
                logger.info("kaken: grants=%d matched=%d", n_k, n_match)
            except KakenAuthError:
                session.rollback()
                logger.warning("KAKEN appidが無効のためスキップ（有効化を待って再実行）")
            except Exception:
                session.rollback()
                logger.exception("KAKEN同期に失敗（他ステージは継続）")
        else:
            logger.warning("KAKEN_APPID未設定のためKAKEN同期をスキップ")
        try:
            n_r = sync_roster(session, RosterClient(), today=today)
            n_rm = match_roster(session)
            n_p = sync_profiles(session, RosterClient(), today=today)
            logger.info("profiles: %d件", n_p)
            logger.info("roster: %d人 matched=%d", n_r, n_rm)
        except Exception:
            session.rollback()
            logger.exception("roster同期に失敗（他ステージは継続）")
        n_m = compute_metrics(session, today)
        logger.info("done: authors=%d works=%d metrics=%d", n_a, n_w, n_m)


if __name__ == "__main__":
    main()
