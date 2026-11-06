-- migrations/schema.sql — ajout

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id       INTEGER NOT NULL,
    workflow_id     INTEGER,                  -- NULL si discovery sans save
    mode            TEXT NOT NULL,            -- 'discovery' | 'replay'
    task_name       TEXT,                     -- 'saucedemo_checkout', etc.
    status          TEXT NOT NULL,            -- 'success' | 'failed' | 'cancelled'
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP,
    steps_executed  INTEGER DEFAULT 0,
    llm_calls       INTEGER DEFAULT 0,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    error_message   TEXT,
    FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_domain_time
    ON runs(domain_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status
    ON runs(status, started_at DESC);
