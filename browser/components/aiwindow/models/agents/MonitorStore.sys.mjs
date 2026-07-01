/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

const lazy = {};
ChromeUtils.defineESModuleGetters(lazy, {
  Sqlite: "resource://gre/modules/Sqlite.sys.mjs",
});

ChromeUtils.defineLazyGetter(lazy, "log", () =>
  console.createInstance({
    prefix: "MonitorStore",
    maxLogLevelPref: "browser.smartwindow.monitorStore.logLevel",
  })
);

const CURRENT_SCHEMA_VERSION = 1;
const DB_FILE_NAME = "monitor-store.sqlite";
const PREF_BRANCH = "browser.smartwindow.monitorStore";

const MONITOR_TABLE = `
CREATE TABLE monitor (
  monitor_id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  monitor_prompt TEXT NOT NULL,
  watch_urls_json TEXT NOT NULL,
  schedule_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_run_time TEXT NOT NULL,
  next_run_time TEXT
) WITHOUT ROWID;
`;

const MONITOR_HISTORY_TABLE = `
CREATE TABLE monitor_history (
  history_id TEXT PRIMARY KEY,
  monitor_id TEXT NOT NULL REFERENCES monitor(monitor_id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  checked_at TEXT NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT NOT NULL,
  condition_met INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
`;

const MONITOR_HISTORY_MONITOR_INDEX = `
CREATE INDEX monitor_history_monitor_idx ON monitor_history(monitor_id, ordinal);
`;

const MONITOR_UPSERT = `
INSERT INTO monitor (
  monitor_id, title, monitor_prompt, watch_urls_json, schedule_json, enabled,
  created_at, updated_at, last_run_time, next_run_time
) VALUES (
  :monitor_id, :title, :monitor_prompt, :watch_urls_json, :schedule_json,
  :enabled, :created_at, :updated_at, :last_run_time, :next_run_time
)
ON CONFLICT(monitor_id) DO UPDATE SET
  title = :title,
  monitor_prompt = :monitor_prompt,
  watch_urls_json = :watch_urls_json,
  schedule_json = :schedule_json,
  enabled = :enabled,
  updated_at = :updated_at,
  last_run_time = :last_run_time,
  next_run_time = :next_run_time;
`;

const MONITOR_HISTORY_INSERT = `
INSERT INTO monitor_history (
  history_id, monitor_id, ordinal, checked_at, status, result_json, condition_met
) VALUES (
  :history_id, :monitor_id, :ordinal, :checked_at, :status, :result_json,
  :condition_met
);
`;

const MONITORS_ALL = `
SELECT monitor_id, title, monitor_prompt, watch_urls_json, schedule_json,
  enabled, created_at, updated_at, last_run_time, next_run_time
FROM monitor
ORDER BY created_at ASC;
`;

const MONITOR_HISTORY_ALL = `
SELECT history_id, monitor_id, ordinal, checked_at, status, result_json,
  condition_met
FROM monitor_history
ORDER BY monitor_id ASC, ordinal ASC;
`;

const DELETE_MONITOR_BY_ID = `
DELETE FROM monitor
WHERE monitor_id = :monitor_id;
`;

const DELETE_ALL_MONITORS = `
DELETE FROM monitor;
`;

const DELETE_HISTORY_BY_MONITOR = `
DELETE FROM monitor_history
WHERE monitor_id = :monitor_id;
`;

function parseJSON(value, fallback) {
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

function toJSON(value) {
  return JSON.stringify(value ?? null);
}

function monitorParams(monitor) {
  return {
    monitor_id: monitor.id,
    title: monitor.title,
    monitor_prompt: monitor.monitorPrompt,
    watch_urls_json: toJSON(monitor.watchUrls),
    schedule_json: toJSON(monitor.schedule),
    enabled: monitor.enabled ? 1 : 0,
    created_at: monitor.createdAt,
    updated_at: monitor.updatedAt,
    last_run_time: monitor.lastRunTime,
    next_run_time: monitor.nextRunTime ?? null,
  };
}

function historyParams(monitor) {
  return (monitor.history ?? []).map((entry, ordinal) => ({
    history_id: entry.id || crypto.randomUUID(),
    monitor_id: monitor.id,
    ordinal,
    checked_at: entry.checkedAt ?? monitor.updatedAt,
    status: entry.status ?? "success",
    result_json: toJSON(entry.resultExplanation ?? ""),
    condition_met: entry.conditionMet ? 1 : 0,
  }));
}

function monitorFromRow(row) {
  return {
    id: row.getResultByName("monitor_id"),
    title: row.getResultByName("title"),
    monitorPrompt: row.getResultByName("monitor_prompt"),
    watchUrls: parseJSON(row.getResultByName("watch_urls_json"), []),
    schedule: parseJSON(row.getResultByName("schedule_json"), null),
    enabled: !!row.getResultByName("enabled"),
    createdAt: row.getResultByName("created_at"),
    updatedAt: row.getResultByName("updated_at"),
    lastRunTime: row.getResultByName("last_run_time"),
    nextRunTime: row.getResultByName("next_run_time"),
    history: [],
  };
}

function historyFromRow(row) {
  return {
    id: row.getResultByName("history_id"),
    checkedAt: row.getResultByName("checked_at"),
    status: row.getResultByName("status"),
    resultExplanation: parseJSON(row.getResultByName("result_json"), ""),
    conditionMet: !!row.getResultByName("condition_met"),
  };
}

function isDatabaseCorruptionError(error) {
  return (
    error?.result == Cr.NS_ERROR_FILE_CORRUPTED ||
    error?.errors?.some(
      storageError => storageError.result == Ci.mozIStorageError.NOTADB
    )
  );
}

/**
 * Persists monitor definitions and run history.
 */
class MonitorStoreImpl {
  #asyncShutdownBlocker;
  #conn = null;
  #promiseConn = null;
  #promiseWrite = Promise.resolve();

  constructor() {
    this.#asyncShutdownBlocker = async () => {
      await this.#promiseWrite;
      await this.close();
    };
  }

  async listMonitors() {
    await this.#ensureDatabase();

    const monitorRows = await this.#conn.executeCached(MONITORS_ALL);
    const monitors = monitorRows.map(monitorFromRow);
    const monitorsById = new Map(
      monitors.map(monitor => [monitor.id, monitor])
    );

    const historyRows = await this.#conn.executeCached(MONITOR_HISTORY_ALL);
    for (const row of historyRows) {
      const monitor = monitorsById.get(row.getResultByName("monitor_id"));
      if (monitor) {
        monitor.history.push(historyFromRow(row));
      }
    }

    return monitors;
  }

  async saveMonitor(monitor) {
    return this.#queueWrite(async () => {
      await this.#ensureDatabase();
      await this.#conn.executeTransaction(async () => {
        await this.#saveMonitorRows(monitor);
      });
    });
  }

  async saveMonitors(monitors) {
    return this.#queueWrite(async () => {
      await this.#ensureDatabase();
      await this.#conn.executeTransaction(async () => {
        await this.#deleteMonitorsNotIn(monitors.map(monitor => monitor.id));
        for (const monitor of monitors) {
          await this.#saveMonitorRows(monitor);
        }
      });
    });
  }

  async deleteMonitor(id) {
    return this.#queueWrite(async () => {
      await this.#ensureDatabase();
      await this.#conn.executeCached(DELETE_MONITOR_BY_ID, {
        monitor_id: id,
      });
    });
  }

  async destroyDatabase() {
    return this.#queueWrite(async () => {
      await this.#removeDatabaseFiles();
      this.#promiseConn = null;
    });
  }

  async close() {
    if (!this.#conn) {
      return;
    }

    lazy.Sqlite.shutdown.removeBlocker(this.#asyncShutdownBlocker);
    try {
      await this.#conn.close();
    } catch (error) {
      lazy.log.warn(`Error closing connection: ${error.message}`);
    }
    this.#conn = null;
    this.#promiseConn = null;
  }

  async #deleteMonitorsNotIn(ids) {
    if (!ids.length) {
      await this.#conn.execute(DELETE_ALL_MONITORS);
      return;
    }

    const params = {};
    const placeholders = ids.map((id, index) => {
      params[`id${index}`] = id;
      return `:id${index}`;
    });
    await this.#conn.execute(
      `DELETE FROM monitor WHERE monitor_id NOT IN (${placeholders.join(", ")});`,
      params
    );
  }

  #queueWrite(task) {
    const promise = this.#promiseWrite.then(task, task);
    this.#promiseWrite = promise.catch(() => {});
    return promise;
  }

  async #saveMonitorRows(monitor) {
    await this.#conn.executeCached(MONITOR_UPSERT, monitorParams(monitor));
    await this.#conn.executeCached(DELETE_HISTORY_BY_MONITOR, {
      monitor_id: monitor.id,
    });

    const history = historyParams(monitor);
    if (history.length) {
      await this.#conn.executeCached(MONITOR_HISTORY_INSERT, history);
    }
  }

  async #openConnection() {
    this.#conn = await lazy.Sqlite.openConnection({
      path: this.databaseFilePath,
    });
    lazy.Sqlite.shutdown.addBlocker(
      "MonitorStore: Shutdown",
      this.#asyncShutdownBlocker
    );

    await this.#conn.execute("PRAGMA journal_mode = WAL;");
    await this.#conn.execute("PRAGMA wal_autocheckpoint = 16;");
    await this.#conn.execute("PRAGMA foreign_keys = ON;");
  }

  async #ensureDatabase() {
    if (this.#promiseConn) {
      return this.#promiseConn;
    }

    this.#promiseConn = (async () => {
      if (this.#removeDatabaseOnStartup) {
        await this.#removeDatabaseFiles();
      }

      try {
        await this.#openConnection();
        await this.#initializeSchema();
      } catch (error) {
        if (!isDatabaseCorruptionError(error)) {
          throw error;
        }
        await this.#removeDatabaseFiles();
        await this.#openConnection();
        await this.#initializeSchema();
      }

      return this.#conn;
    })().catch(async error => {
      await this.close();
      this.#promiseConn = null;
      throw error;
    });

    return this.#promiseConn;
  }

  async #initializeSchema() {
    const version = await this.#conn.getSchemaVersion();
    if (version == CURRENT_SCHEMA_VERSION) {
      return;
    }
    if (version > CURRENT_SCHEMA_VERSION) {
      throw new Error(
        `Monitor store schema version ${version} is newer than supported version ${CURRENT_SCHEMA_VERSION}.`
      );
    }

    await this.#conn.executeTransaction(async () => {
      if (version == 0) {
        await this.#createDatabaseEntities();
        await this.#conn.setSchemaVersion(CURRENT_SCHEMA_VERSION);
        return;
      }

      await this.#conn.setSchemaVersion(CURRENT_SCHEMA_VERSION);
    });
  }

  async #createDatabaseEntities() {
    await this.#conn.execute(MONITOR_TABLE);
    await this.#conn.execute(MONITOR_HISTORY_TABLE);
    await this.#conn.execute(MONITOR_HISTORY_MONITOR_INDEX);
  }

  async #removeDatabaseFiles() {
    await this.close();
    try {
      for (const file of [
        this.databaseFilePath,
        PathUtils.join(PathUtils.profileDir, `${this.databaseFileName}-wal`),
        PathUtils.join(PathUtils.profileDir, `${this.databaseFileName}-shm`),
      ]) {
        await IOUtils.remove(file, {
          ignoreAbsent: true,
          recursive: true,
          retryReadonly: true,
        });
      }
      this.#removeDatabaseOnStartup = false;
    } catch (error) {
      this.#removeDatabaseOnStartup = true;
      throw error;
    }
  }

  get #removeDatabaseOnStartup() {
    return Services.prefs.getBoolPref(
      `${PREF_BRANCH}.removeDatabaseOnStartup`,
      false
    );
  }

  set #removeDatabaseOnStartup(value) {
    Services.prefs.setBoolPref(`${PREF_BRANCH}.removeDatabaseOnStartup`, value);
  }

  get databaseFileName() {
    return DB_FILE_NAME;
  }

  get databaseFilePath() {
    return PathUtils.join(PathUtils.profileDir, this.databaseFileName);
  }
}

const MonitorStore = new MonitorStoreImpl();
export { MonitorStore };
