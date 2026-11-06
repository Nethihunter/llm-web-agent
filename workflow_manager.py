"""Workflow persistence: SQLite (default) or Postgres (production).

Choose the backend with the DB_BACKEND env var:
- "sqlite"   (default) : single file, zero setup, single-user.
- "postgres"           : requires POSTGRES_* env vars (see docker-compose.yml).

Both backends implement the same interface, so the agent does not care.

Design notes:
    - The module is the only file that touches the database. Swapping to a
      third backend (MySQL, DuckDB) means adding a ``_DB`` branch and a
      ``SCHEMA_*`` constant; nothing else changes.
    - Every method opens a short-lived connection and closes it in a
      ``finally``. This is deliberate: SQLite is not thread-safe by default,
      and holding a connection across a Playwright call (which can take
      seconds) would block the workflow cache for the whole run.
    - Postgres and SQLite differ on three points: the placeholder (``%s``
      vs ``?``), the way to get an inserted id (``RETURNING id`` vs
      ``lastrowid``), and the datetime format returned by ``CURRENT_TIMESTAMP``.
      Each difference is isolated behind a conditional in the method that
      needs it, so the SQL stays readable.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = Path("workflows.db")
STALE_AFTER_DAYS = 7
MAX_FAIL_COUNT = 3


# ======================================================================
# Schema (compatible SQLite / Postgres)
# ======================================================================
SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS workflows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    goal TEXT NOT NULL,
    success_indicator TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    use_count INTEGER DEFAULT 0,
    UNIQUE(domain_id, name),
    FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id INTEGER NOT NULL,
    step_order INTEGER NOT NULL,
    action TEXT NOT NULL,
    selector TEXT NOT NULL,
    value_template TEXT,
    validation_hint TEXT,
    last_success_at TIMESTAMP,
    fail_count INTEGER DEFAULT 0,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE,
    UNIQUE(workflow_id, step_order)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL,
    workflow_id INTEGER,
    task_name TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    steps_executed INTEGER DEFAULT 0,
    llm_calls INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    error_message TEXT,
    FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_workflows_domain ON workflows(domain_id, name);
CREATE INDEX IF NOT EXISTS idx_steps_workflow ON steps(workflow_id, step_order);
CREATE INDEX IF NOT EXISTS idx_runs_domain_time ON runs(domain_id, started_at DESC);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS domains (
    id SERIAL PRIMARY KEY,
    hostname TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS workflows (
    id SERIAL PRIMARY KEY,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    goal TEXT NOT NULL,
    success_indicator TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    use_count INTEGER DEFAULT 0,
    UNIQUE(domain_id, name)
);
CREATE TABLE IF NOT EXISTS steps (
    id SERIAL PRIMARY KEY,
    workflow_id INTEGER NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    action TEXT NOT NULL,
    selector TEXT NOT NULL,
    value_template TEXT,
    validation_hint TEXT,
    last_success_at TIMESTAMP,
    fail_count INTEGER DEFAULT 0,
    UNIQUE(workflow_id, step_order)
);
CREATE TABLE IF NOT EXISTS runs (
    id SERIAL PRIMARY KEY,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    workflow_id INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
    task_name TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    steps_executed INTEGER DEFAULT 0,
    llm_calls INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflows_domain ON workflows(domain_id, name);
CREATE INDEX IF NOT EXISTS idx_steps_workflow ON steps(workflow_id, step_order);
CREATE INDEX IF NOT EXISTS idx_runs_domain_time ON runs(domain_id, started_at DESC);
"""


# ======================================================================
# Dataclasses
# ======================================================================
@dataclass
class Step:
    """One recorded action in a workflow.

    ``value_template`` is the template, not the rendered value: it stores
    ``{password}``, never the actual password. This is what makes the
    cache safe to commit, share, or inspect (see docs/USAGE.md, "Passing
    variables").
    """

    order: int
    action: str
    selector: str
    value_template: str | None = None
    validation_hint: str | None = None


@dataclass
class Workflow:
    """A cached workflow: the steps plus what is needed to replay it safely.

    ``success_indicator`` is a selector that must be visible at the end of
    the replay. When set, ``_replay`` verifies it before returning success;
    when ``None``, replay trusts the last step's absence of error.
    ``domain`` and ``id`` are populated by the manager on load, not by the
    caller on save.
    """

    name: str
    goal: str
    steps: list[Step] = field(default_factory=list)
    success_indicator: str | None = None
    domain: str | None = None
    id: int | None = None


# ======================================================================
# Connection wrapper (SQLite or Postgres)
# ======================================================================
class _DB:
    """Small abstraction over sqlite3 / psycopg2.

    Provides the same methods to WorkflowManager, with the right placeholder
    (``?`` for SQLite, ``%s`` for Postgres) and the right dict cursor.

    Why not use an ORM: the schema is four tables and the queries are all
    flat. SQLAlchemy would add a dependency and a layer of indirection for
    no gain here. The abstraction point is this class, and it is small
    enough to read in one screen.
    """

    def __init__(self, backend: str, db_path: Path | None = None):
        self.backend = backend
        self._db_path = db_path or DB_PATH

    def connect(self):
        """Open a new connection, configured for dict-like row access.

        Callers must ``close()`` it in a ``finally`` block: connections
        are short-lived by design (see the module docstring).
        """
        if self.backend == "postgres":
            import psycopg2
            import psycopg2.extras
            conn = psycopg2.connect(
                host=os.environ.get("POSTGRES_HOST", "localhost"),
                port=int(os.environ.get("POSTGRES_PORT", "5432")),
                dbname=os.environ.get("POSTGRES_DB", "agent"),
                user=os.environ.get("POSTGRES_USER", "agent"),
                password=os.environ.get("POSTGRES_PASSWORD", ""),
            )
            conn.cursor_factory = psycopg2.extras.RealDictCursor
            return conn
        # SQLite: force dict-like rows so callers can use row["col"] uniformly
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @property
    def placeholder(self) -> str:
        """The parameter placeholder used by the active backend."""
        return "%s" if self.backend == "postgres" else "?"

    def init_schema(self) -> None:
        """Create the tables and indexes if they do not exist yet.

        Idempotent: safe to call on every ``WorkflowManager()``
        construction. Postgres executes the whole script in one call;
        SQLite needs ``executescript`` to run multiple statements.
        """
        schema = SCHEMA_POSTGRES if self.backend == "postgres" else SCHEMA_SQLITE
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(schema) if self.backend == "postgres" else cur.executescript(schema)
            conn.commit()
        finally:
            conn.close()


# ======================================================================
# WorkflowManager
# ======================================================================
class WorkflowManager:
    """Read and write the workflow cache and the run history.

    One instance is created per agent run (see ``WebAgent.__init__``). It
    is not thread-safe: SQLite in default mode does not allow concurrent
    writers, and the agent runs one task at a time anyway.

    The backend is selected at construction time from ``DB_BACKEND``
    (default ``"sqlite"``), or from the ``backend`` argument. All public
    methods behave identically on both backends.
    """

    def __init__(self, db_path: Path = DB_PATH, backend: str | None = None):
        self.backend = backend or os.environ.get("DB_BACKEND", "sqlite")
        self.db = _DB(self.backend, db_path)
        self.db.init_schema()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_domain(url: str) -> str:
        """Return the cache key for a URL: hostname without ``www.``.

        Two URLs that differ only by scheme, path, query, or ``www.``
        prefix map to the same domain. This is what makes the cache hit
        on ``https://saucedemo.com/inventory`` after a run on
        ``https://www.saucedemo.com/``.
        """
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    def _get_or_create_domain(self, cur, hostname: str) -> int:
        """Return the id of ``hostname``, inserting it if missing.

        Must be called inside an open transaction: the caller owns the
        connection and is responsible for ``commit()``.
        """
        p = self.db.placeholder
        cur.execute(f"SELECT id FROM domains WHERE hostname = {p}", (hostname,))
        row = cur.fetchone()
        if row:
            return row["id"]
        if self.backend == "postgres":
            cur.execute(
                f"INSERT INTO domains (hostname) VALUES ({p}) RETURNING id",
                (hostname,),
            )
            return cur.fetchone()["id"]
        cur.execute(f"INSERT INTO domains (hostname) VALUES ({p})", (hostname,))
        return cur.lastrowid

    # ------------------------------------------------------------------
    # Workflow CRUD
    # ------------------------------------------------------------------
    def load_workflow(self, url: str, name: str) -> Workflow | None:
        """Load a cached workflow, or return ``None`` if it does not exist.

        The ``url`` is normalized to a domain before the lookup; the
        returned ``Workflow`` has its ``domain`` and ``id`` populated.
        """
        domain = self._normalize_domain(url)
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT w.id, w.name, w.goal, w.success_indicator
                FROM workflows w JOIN domains d ON d.id = w.domain_id
                WHERE d.hostname = {p} AND w.name = {p}
                """,
                (domain, name),
            )
            row = cur.fetchone()
            if not row:
                return None
            wf_id, wf_name, wf_goal, wf_success = (
                (row["id"], row["name"], row["goal"], row["success_indicator"])
                if isinstance(row, dict)
                else row
            )
            cur.execute(
                f"""
                SELECT step_order, action, selector, value_template, validation_hint
                FROM steps WHERE workflow_id = {p} ORDER BY step_order
                """,
                (wf_id,),
            )
            steps = [
                Step(
                    order=s["step_order"] if isinstance(s, dict) else s[0],
                    action=s["action"] if isinstance(s, dict) else s[1],
                    selector=s["selector"] if isinstance(s, dict) else s[2],
                    value_template=s["value_template"] if isinstance(s, dict) else s[3],
                    validation_hint=s["validation_hint"] if isinstance(s, dict) else s[4],
                )
                for s in cur.fetchall()
            ]
            return Workflow(
                id=wf_id, name=wf_name, goal=wf_goal,
                success_indicator=wf_success, domain=domain, steps=steps,
            )
        finally:
            conn.close()

    def save_workflow(self, url: str, workflow: Workflow) -> int:
        """Insert or update a workflow, replacing all its steps.

        Returns the workflow id. Existing steps are deleted and re-inserted
        rather than diffed: a workflow has a handful of steps, and a full
        replace is simpler and cannot leave orphaned rows behind.

        Side effect: every saved step gets ``last_success_at = now``, so a
        freshly discovered workflow is never considered stale on its first
        replay.
        """
        domain = self._normalize_domain(url)
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            domain_id = self._get_or_create_domain(cur, domain)
            if self.backend == "postgres":
                cur.execute(
                    f"""
                    INSERT INTO workflows (domain_id, name, goal, success_indicator)
                    VALUES ({p}, {p}, {p}, {p})
                    ON CONFLICT (domain_id, name) DO UPDATE SET
                        goal = EXCLUDED.goal,
                        success_indicator = EXCLUDED.success_indicator
                    RETURNING id
                    """,
                    (domain_id, workflow.name, workflow.goal, workflow.success_indicator),
                )
                wf_id = cur.fetchone()["id"]
            else:
                cur.execute(
                    f"""
                    INSERT INTO workflows (domain_id, name, goal, success_indicator)
                    VALUES ({p}, {p}, {p}, {p})
                    ON CONFLICT(domain_id, name) DO UPDATE SET
                        goal = excluded.goal,
                        success_indicator = excluded.success_indicator
                    """,
                    (domain_id, workflow.name, workflow.goal, workflow.success_indicator),
                )
                cur.execute(
                    f"SELECT id FROM workflows WHERE domain_id = {p} AND name = {p}",
                    (domain_id, workflow.name),
                )
                wf_id = cur.fetchone()[0]
            cur.execute(f"DELETE FROM steps WHERE workflow_id = {p}", (wf_id,))
            for step in workflow.steps:
                cur.execute(
                    f"""
                    INSERT INTO steps
                        (workflow_id, step_order, action, selector,
                         value_template, validation_hint, last_success_at)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, CURRENT_TIMESTAMP)
                    """,
                    (wf_id, step.order, step.action, step.selector,
                     step.value_template, step.validation_hint),
                )
            conn.commit()
            return wf_id
        finally:
            conn.close()

    def delete_workflow(self, url: str, name: str) -> bool:
        """Delete a workflow and (by cascade) its steps.

        Returns ``True`` if a row was deleted, ``False`` if none matched.
        The ``runs`` history is kept: runs reference the workflow with
        ``ON DELETE SET NULL``, so past runs remain visible in the
        dashboard after the workflow itself is gone.
        """
        domain = self._normalize_domain(url)
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                DELETE FROM workflows
                WHERE name = {p} AND domain_id = (
                    SELECT id FROM domains WHERE hostname = {p}
                )
                """,
                (name, domain),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Step feedback
    # ------------------------------------------------------------------
    def mark_step_success(self, workflow_id: int, step_order: int) -> None:
        """Reset a step's failure counter and stamp ``last_success_at``.

        Called by ``_replay`` after each step that did not raise.
        """
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE steps SET last_success_at = CURRENT_TIMESTAMP, fail_count = 0
                WHERE workflow_id = {p} AND step_order = {p}
                """,
                (workflow_id, step_order),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_step_failure(self, workflow_id: int, step_order: int) -> None:
        """Increment a step's failure counter.

        The counter is what ``is_step_stale`` uses to decide that a step
        should trigger a fallback to discovery.
        """
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE steps SET fail_count = fail_count + 1 "
                f"WHERE workflow_id = {p} AND step_order = {p}",
                (workflow_id, step_order),
            )
            conn.commit()
        finally:
            conn.close()

    def is_step_stale(self, workflow_id: int, step_order: int) -> bool:
        """Decide whether a step should no longer be trusted.

        A step is stale if any of the following holds:

        - it does not exist (unknown ``workflow_id`` / ``step_order``),
        - it has failed ``MAX_FAIL_COUNT`` consecutive times,
        - it has never succeeded (``last_success_at`` is NULL),
        - its last success is older than ``STALE_AFTER_DAYS``,
        - its ``last_success_at`` cannot be parsed (defensive: a corrupted
          value must not make a broken step look fresh).

        The datetime handling normalizes to naive UTC on both sides,
        because SQLite and Postgres return different formats and some
        drivers attach a timezone. Comparing aware and naive datetimes
        raises in Python, so we strip tzinfo after converting to UTC.
        """
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT last_success_at, fail_count FROM steps "
                f"WHERE workflow_id = {p} AND step_order = {p}",
                (workflow_id, step_order),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return True
        last_ok = row["last_success_at"] if isinstance(row, dict) else row[0]
        fail_count = row["fail_count"] if isinstance(row, dict) else row[1]
        if fail_count >= MAX_FAIL_COUNT:
            return True
        if last_ok is None:
            return True
        try:
            last_dt = datetime.fromisoformat(str(last_ok))
        except (ValueError, TypeError):
            return True
        if last_dt.tzinfo is not None:
            last_dt = last_dt.astimezone(timezone.utc).replace(tzinfo=None)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return (now - last_dt) > timedelta(days=STALE_AFTER_DAYS)

    def touch_workflow_usage(self, workflow_id: int) -> None:
        """Increment ``use_count`` and stamp ``last_used_at``.

        Called once per successful replay, so the dashboard can show which
        workflows actually save money and which are dead weight.
        """
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE workflows SET last_used_at = CURRENT_TIMESTAMP, "
                f"use_count = use_count + 1 WHERE id = {p}",
                (workflow_id,),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------
    def log_run_start(self, url: str, workflow_id: int | None,
                      task_name: str | None, mode: str) -> int:
        """Insert a run with status ``"running"`` and return its id.

        The caller must call ``log_run_end`` with the returned id, even on
        failure. ``agent._discover`` does this in a ``finally`` block.
        """
        domain = self._normalize_domain(url)
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            domain_id = self._get_or_create_domain(cur, domain)
            if self.backend == "postgres":
                cur.execute(
                    f"""INSERT INTO runs (domain_id, workflow_id, task_name, mode, status)
                        VALUES ({p}, {p}, {p}, {p}, 'running') RETURNING id""",
                    (domain_id, workflow_id, task_name, mode),
                )
                run_id = cur.fetchone()["id"]
            else:
                cur.execute(
                    f"""INSERT INTO runs (domain_id, workflow_id, task_name, mode, status)
                        VALUES ({p}, {p}, {p}, {p}, 'running')""",
                    (domain_id, workflow_id, task_name, mode),
                )
                run_id = cur.lastrowid
            conn.commit()
            return run_id
        finally:
            conn.close()

    def log_run_end(self, run_id: int, status: str, steps: int, llm_calls: int,
                    tokens_in: int, tokens_out: int, error: str | None = None) -> None:
        """Close a run: set the final status, timings, and token counts.

        ``status`` is one of ``"success"``, ``"failed"``, or whatever the
        caller decided. ``error`` is stored verbatim (truncated by the
        dashboard template, not here) so the user can see the real
        exception message.
        """
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE runs SET status = {p}, finished_at = CURRENT_TIMESTAMP,
                    steps_executed = {p}, llm_calls = {p},
                    tokens_in = {p}, tokens_out = {p}, error_message = {p}
                WHERE id = {p}
                """,
                (status, steps, llm_calls, tokens_in, tokens_out, error, run_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def list_runs(self, limit: int = 50, domain: str | None = None) -> list[dict]:
        """Return the most recent runs, newest first.

        If ``domain`` is given, only runs for that domain are returned.
        The domain is normalized the same way as everywhere else, so
        ``"saucedemo.com"`` and ``"https://www.saucedemo.com"`` match the
        same rows.
        """
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            if domain:
                dnorm = self._normalize_domain(f"https://{domain}")
                cur.execute(
                    f"""
                    SELECT r.id, d.hostname, r.task_name, r.mode, r.status,
                           r.started_at, r.finished_at, r.steps_executed,
                           r.llm_calls, r.tokens_in, r.tokens_out, r.error_message
                    FROM runs r JOIN domains d ON d.id = r.domain_id
                    WHERE d.hostname = {p}
                    ORDER BY r.started_at DESC LIMIT {p}
                    """,
                    (dnorm, limit),
                )
            else:
                cur.execute(
                    f"""
                    SELECT r.id, d.hostname, r.task_name, r.mode, r.status,
                           r.started_at, r.finished_at, r.steps_executed,
                           r.llm_calls, r.tokens_in, r.tokens_out, r.error_message
                    FROM runs r JOIN domains d ON d.id = r.domain_id
                    ORDER BY r.started_at DESC LIMIT {p}
                    """,
                    (limit,),
                )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [self._row_to_run(r) for r in rows]

    def list_workflows(self, domain: str | None = None) -> list[dict]:
        """Return cached workflows as flat dicts, sorted by domain then name.

        Each row is what the dashboard renders; the full step list is only
        loaded by ``load_workflow``, to keep this query cheap even with
        many cached workflows.
        """
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            if domain:
                dnorm = self._normalize_domain(f"https://{domain}")
                cur.execute(
                    f"""
                    SELECT d.hostname, w.name, w.goal, w.use_count, w.last_used_at
                    FROM workflows w JOIN domains d ON d.id = w.domain_id
                    WHERE d.hostname = {p} ORDER BY w.name
                    """,
                    (dnorm,),
                )
            else:
                cur.execute(
                    """
                    SELECT d.hostname, w.name, w.goal, w.use_count, w.last_used_at
                    FROM workflows w JOIN domains d ON d.id = w.domain_id
                    ORDER BY d.hostname, w.name
                    """
                )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [self._row_to_workflow_row(r) for r in rows]

    def get_stats(self) -> dict:
        """Aggregate counters for the dashboard overview page.

        ``total_cost`` is an estimate: it applies the DeepSeek prices
        (0.14 $/M input, 0.28 $/M output) to the recorded token counts,
        whatever the actual provider was. The dashboard labels it as
        indicative, not exact. ``cache_savings`` counts successful replays
        — each one is an LLM call that was not made.
        """
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM domains")
            total_domains = self._scalar(cur)
            cur.execute("SELECT COUNT(*) FROM workflows")
            total_workflows = self._scalar(cur)
            cur.execute("SELECT COUNT(*) FROM runs")
            total_runs = self._scalar(cur)
            cur.execute("SELECT COUNT(*) FROM runs WHERE status='success'")
            success_runs = self._scalar(cur)
            cur.execute("SELECT COUNT(*) FROM runs WHERE mode='replay' AND status='success'")
            replay_runs = self._scalar(cur)
            # Aliases are required: with Postgres both columns would otherwise be
            # called "coalesce" and collapse into one dict key.
            cur.execute(
                "SELECT COALESCE(SUM(tokens_in),0) AS tin, "
                "COALESCE(SUM(tokens_out),0) AS tout FROM runs"
            )
            row = cur.fetchone()
            tokens_in, tokens_out = row["tin"], row["tout"]
        finally:
            conn.close()
        total_cost = (tokens_in * 0.14 + tokens_out * 0.28) / 1_000_000
        success_rate = round(100 * success_runs / total_runs, 1) if total_runs else 0
        return {
            "total_domains": total_domains,
            "total_workflows": total_workflows,
            "total_runs": total_runs,
            "success_rate": success_rate,
            "cache_savings": replay_runs,
            "total_cost": f"{total_cost:.4f}",
        }

    def get_recent_runs_for_chart(self, hours: int = 24) -> list[dict]:
        """Per-hour run counts (total and successful) for the last N hours.

        The two backends use different date functions (``to_char`` vs
        ``strftime``) but return the same shape, so the chart code in
        ``dashboard/templates/index.html`` is backend-agnostic.
        """
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            if self.backend == "postgres":
                cur.execute(
                    f"""
                    SELECT to_char(started_at, 'YYYY-MM-DD HH24:00') AS hour,
                           COUNT(*) AS total,
                           SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok
                    FROM runs
                    WHERE started_at >= NOW() - INTERVAL '{int(hours)} hours'
                    GROUP BY 1 ORDER BY 1
                    """
                )
            else:
                cur.execute(
                    f"""
                    SELECT strftime('%Y-%m-%d %H:00', started_at) AS hour,
                           COUNT(*) AS total,
                           SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok
                    FROM runs
                    WHERE started_at >= datetime('now', {p})
                    GROUP BY hour ORDER BY hour
                    """,
                    (f"-{hours} hours",),
                )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [
            {"hour": (r["hour"] if isinstance(r, dict) else r[0]),
             "total": (r["total"] if isinstance(r, dict) else r[1]),
             "success": (r["ok"] if isinstance(r, dict) else r[2])}
            for r in rows
        ]

    def get_token_usage_for_chart(self, hours: int = 24) -> list[dict]:
        """Per-hour token sums (input and output) for the last N hours.

        Same dual-backend pattern as ``get_recent_runs_for_chart``. NULL
        sums (no runs in the hour) are normalized to 0 so the chart does
        not draw gaps.
        """
        p = self.db.placeholder
        conn = self.db.connect()
        try:
            cur = conn.cursor()
            if self.backend == "postgres":
                cur.execute(
                    f"""
                    SELECT to_char(started_at, 'YYYY-MM-DD HH24:00') AS hour,
                           COALESCE(SUM(tokens_in), 0) AS tin,
                           COALESCE(SUM(tokens_out), 0) AS tout
                    FROM runs
                    WHERE started_at >= NOW() - INTERVAL '{int(hours)} hours'
                    GROUP BY 1 ORDER BY 1
                    """
                )
            else:
                cur.execute(
                    f"""
                    SELECT strftime('%Y-%m-%d %H:00', started_at) AS hour,
                           SUM(tokens_in) AS tin,
                           SUM(tokens_out) AS tout
                    FROM runs
                    WHERE started_at >= datetime('now', {p})
                    GROUP BY hour ORDER BY hour
                    """,
                    (f"-{hours} hours",),
                )
            rows = cur.fetchall()
        finally:
            conn.close()
        return [
            {"hour": (r["hour"] if isinstance(r, dict) else r[0]),
             "tin": (r["tin"] if isinstance(r, dict) else r[1]) or 0,
             "tout": (r["tout"] if isinstance(r, dict) else r[2]) or 0}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Row helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _scalar(cur):
        """Return the single value of the current row, whatever the backend.

        Postgres returns a one-key dict (``{"count": 5}``), SQLite returns
        a one-element Row (``Row(5)``). Both are collapsed to the value.
        """
        row = cur.fetchone()
        if isinstance(row, dict):
            return next(iter(row.values()))
        return row[0]

    @staticmethod
    def _row_to_run(r) -> dict:
        """Convert a raw run row to the flat dict the dashboard expects."""
        # sqlite3.Row supports dict() conversion
        if not isinstance(r, dict):
            r = dict(r)
        return {
            "id": r["id"], "domain": r["hostname"], "task": r["task_name"],
            "mode": r["mode"], "status": r["status"],
            "started_at": r["started_at"], "finished_at": r["finished_at"],
            "steps": r["steps_executed"], "llm_calls": r["llm_calls"],
            "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"],
            "error": r["error_message"],
        }

    @staticmethod
    def _row_to_workflow_row(r) -> dict:
        """Convert a raw workflow row to the flat dict the dashboard expects."""
        if not isinstance(r, dict):
            r = dict(r)
        return {
            "hostname": r["hostname"], "name": r["name"],
            "goal": r["goal"], "use_count": r["use_count"],
            "last_used_at": r["last_used_at"],
        }
