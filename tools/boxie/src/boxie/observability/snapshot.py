"""One telemetry connection and one MVCC statement; never queried by a scrape."""

import asyncio
from pathlib import Path

from psycopg.rows import dict_row

from boxie.bootstrap.config import read_credential
from boxie.observability.metrics import KINDS, STATES, Metrics, OperationalSnapshot
from boxie.persistence.postgres import Pool

QUERY = """
WITH sample AS (SELECT statement_timestamp() AS now),
 states AS (SELECT state,count(*) AS n FROM sandboxes GROUP BY state),
 cleanup AS (SELECT count(*) AS n, count(*) FILTER (WHERE cleanup_pending_since IS NULL) AS unknown,
 min(cleanup_pending_since) AS oldest FROM sandboxes WHERE cleanup_pending),
 capacity AS (SELECT count(*) AS total,count(sandbox_id) AS reserved FROM capacity_slots),
 pending AS (SELECT kind,count(*) AS n,min(created_at) AS oldest FROM outbox WHERE delivered_at IS NULL GROUP BY kind)
SELECT EXTRACT(EPOCH FROM sample.now) AS timestamp,
 COALESCE((SELECT jsonb_object_agg(state,n) FROM states),'{}') AS states,
 capacity.total,capacity.reserved,cleanup.n AS cleanup_pending,cleanup.unknown,
 COALESCE(EXTRACT(EPOCH FROM sample.now-cleanup.oldest),0) AS cleanup_age,
 COALESCE((SELECT jsonb_object_agg(kind,jsonb_build_array(n, GREATEST(0,EXTRACT(EPOCH FROM sample.now-oldest)))) FROM pending),'{}') AS outbox
FROM sample,cleanup,capacity
"""


async def operational_snapshot(pool: Pool) -> OperationalSnapshot:
    async with asyncio.timeout(2), pool.connection(timeout=0.5) as conn, conn.transaction():
        await conn.execute("SET LOCAL statement_timeout='1s'")
        row = await (await conn.execute(QUERY)).fetchone()
        assert row is not None
        return OperationalSnapshot(
            float(row["timestamp"]),
            tuple(int(row["states"].get(s, 0)) for s in STATES),
            row["reserved"],
            row["total"],
            row["cleanup_pending"],
            max(0, float(row["cleanup_age"])),
            row["unknown"],
            tuple(
                (int(row["outbox"].get(k, [0, 0])[0]), float(row["outbox"].get(k, [0, 0])[1]))
                for k in KINDS
            ),
        )


async def refresh_snapshots(metrics: Metrics, dsn_file: Path) -> None:
    async with Pool(
        read_credential(dsn_file, allow_spaces=True).decode(),
        min_size=0,
        max_size=1,
        timeout=0.5,
        kwargs={"row_factory": dict_row, "connect_timeout": 1},
    ) as pool:
        while True:
            try:
                metrics.refresh(await operational_snapshot(pool))
                metrics.dependency("postgres", True)
            except Exception:
                metrics.refresh_failed()
                metrics.dependency("postgres", False)
            await asyncio.sleep(5)
