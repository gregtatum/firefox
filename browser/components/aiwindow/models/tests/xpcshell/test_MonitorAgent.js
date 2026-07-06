/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

do_get_profile();

const {
  MONITOR_AGENTS_CHANGED_TOPIC,
  Monitor,
  MonitorAgent,
  TOTAL_NUM_MONITORS,
  TOTAL_NUM_URLS_IN_MONITOR,
} = ChromeUtils.importESModule(
  "moz-src:///browser/components/aiwindow/models/agents/MonitorAgent.sys.mjs"
);
const { DailySchedule, IntervalSchedule, Schedule, WeeklySchedule } =
  ChromeUtils.importESModule(
    "moz-src:///browser/components/aiwindow/models/agents/Schedule.sys.mjs"
  );
const { MonitorStore } = ChromeUtils.importESModule(
  "moz-src:///browser/components/aiwindow/models/agents/MonitorStore.sys.mjs"
);
const { Sqlite } = ChromeUtils.importESModule(
  "resource://gre/modules/Sqlite.sys.mjs"
);
const { TestUtils } = ChromeUtils.importESModule(
  "resource://testing-common/TestUtils.sys.mjs"
);
const { _clearRemoteClientForTesting } = ChromeUtils.importESModule(
  "moz-src:///browser/components/aiwindow/models/Utils.sys.mjs"
);
const { sinon } = ChromeUtils.importESModule(
  "resource://testing-common/Sinon.sys.mjs"
);

const PREF_MODEL = "browser.smartwindow.model";
const PREF_MODEL_CHOICE = "browser.smartwindow.firstrun.modelChoice";
const PREF_MONITOR_AGENTS = "browser.smartwindow.monitorAgents";

registerCleanupFunction(async () => {
  await MonitorAgent._resetForTesting();
  for (const pref of [PREF_MODEL, PREF_MODEL_CHOICE, PREF_MONITOR_AGENTS]) {
    if (Services.prefs.prefHasUserValue(pref)) {
      Services.prefs.clearUserPref(pref);
    }
  }
  _clearRemoteClientForTesting();
});

function intervalSchedule(hours = 10) {
  return { type: "interval", hours };
}

function makeMonitor(options = {}) {
  return new Monitor({
    id: "monitor-1",
    title: "Price check",
    monitorPrompt: "Tell me when the price is below $10",
    watchUrls: ["https://example.com/product"],
    schedule: new IntervalSchedule(15),
    createdAt: "2026-06-23T12:00:00.000Z",
    updatedAt: "2026-06-23T12:00:00.000Z",
    lastRunTime: "2026-06-23T12:00:00.000Z",
    nextRunTime: "2026-06-24T03:00:00.000Z",
    ...options,
  });
}

async function clearStoredMonitors() {
  await MonitorAgent._resetForTesting();
  if (Services.prefs.prefHasUserValue(PREF_MONITOR_AGENTS)) {
    Services.prefs.clearUserPref(PREF_MONITOR_AGENTS);
  }
}

function stockMonitorOptions(options = {}) {
  return {
    prompt: "Watch stock",
    watchUrls: ["https://example.com/stock"],
    schedule: intervalSchedule(),
    ...options,
  };
}

function makeDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function isoFromNow(offsetMs) {
  return new Date(Date.now() + offsetMs).toISOString();
}

function observeTopic(topic) {
  let count = 0;
  const observer = () => count++;
  Services.obs.addObserver(observer, topic);
  return {
    get count() {
      return count;
    },
    cleanup() {
      Services.obs.removeObserver(observer, topic);
    },
  };
}

async function createStoredMonitor(options) {
  await MonitorAgent.createMonitor(options);
  return (await MonitorAgent.listMonitors()).at(-1);
}

add_task(function test_Schedule_fromJSON_normalizes_supported_shapes() {
  Assert.equal(new IntervalSchedule(1).hours, 1);

  const interval = Schedule.fromJSON({ type: "interval", hours: "30" });
  Assert.ok(interval instanceof IntervalSchedule);
  Assert.equal(interval.hours, 30);

  const daily = Schedule.fromJSON({ type: "daily", hour: "9", minute: "5" });
  Assert.ok(daily instanceof DailySchedule);
  Assert.equal(daily.hour, 9);
  Assert.equal(daily.minute, 5);

  const weekly = Schedule.fromJSON({
    type: "weekly",
    weekday: "2",
    hour: "14",
    minute: "45",
  });
  Assert.ok(weekly instanceof WeeklySchedule);
  Assert.equal(weekly.weekday, 2);
  Assert.equal(weekly.hour, 14);
  Assert.equal(weekly.minute, 45);

  Assert.throws(
    () => Schedule.fromJSON({ type: "yearly" }),
    /Monitor schedule type is invalid/
  );
});

add_task(function test_Monitor_constructor_normalizes_and_validates_input() {
  const monitor = new Monitor({
    id: "monitor-id",
    title: "  Sale watcher  ",
    monitorPrompt: "  tell me when it ships  ",
    watchUrls: [
      " https://example.com/a ",
      "about:config",
      "",
      null,
      "not a url",
      "http://example.com/insecure",
      "https://example.com/b",
    ],
    schedule: new IntervalSchedule(10),
    enabled: false,
    createdAt: "2026-06-23T12:00:00.000Z",
  });

  Assert.equal(monitor.id, "monitor-id");
  Assert.equal(monitor.title, "Sale watcher");
  Assert.equal(monitor.monitorPrompt, "tell me when it ships");
  Assert.deepEqual(monitor.watchUrls, [
    "https://example.com/a",
    "http://example.com/insecure",
    "https://example.com/b",
  ]);
  Assert.equal(monitor.enabled, false);
  Assert.equal(monitor.updatedAt, monitor.createdAt);
  Assert.equal(monitor.lastRunTime, monitor.createdAt);
  Assert.equal(typeof monitor.nextRunTime, "string");

  Assert.throws(
    () =>
      new Monitor({
        monitorPrompt: "prompt",
        watchUrls: ["https://example.com"],
        schedule: {},
      }),
    /Monitor schedule is invalid/
  );
  Assert.throws(
    () =>
      new Monitor({
        monitorPrompt: "prompt",
        watchUrls: [],
        schedule: new IntervalSchedule(10),
      }),
    /Monitor is invalid/
  );
  Assert.throws(
    () =>
      new Monitor({
        monitorPrompt: "prompt",
        watchUrls: ["about:config"],
        schedule: new IntervalSchedule(10),
      }),
    /Monitor is invalid/
  );
});

add_task(function test_Monitor_limits_watch_urls() {
  const urls = Array.from(
    { length: TOTAL_NUM_URLS_IN_MONITOR + 2 },
    (_, index) => `https://example.com/${index}`
  );
  const monitor = makeMonitor({
    watchUrls: urls,
  });

  Assert.equal(monitor.watchUrls.length, TOTAL_NUM_URLS_IN_MONITOR);
  Assert.deepEqual(monitor.watchUrls, urls.slice(0, TOTAL_NUM_URLS_IN_MONITOR));
});

add_task(function test_Monitor_fromJSON_supports_current_fields() {
  const monitor = Monitor.fromJSON({
    id: "stored-id",
    title: "Stored title",
    monitorPrompt: "Stored prompt",
    watchUrls: ["https://example.com/stored"],
    schedule: intervalSchedule(20),
    lastRunTime: "2026-06-23T10:00:00.000Z",
  });

  Assert.equal(monitor.id, "stored-id");
  Assert.equal(monitor.title, "Stored title");
  Assert.equal(monitor.monitorPrompt, "Stored prompt");
  Assert.deepEqual(monitor.watchUrls, ["https://example.com/stored"]);
  Assert.equal(monitor.schedule.hours, 20);
  Assert.equal(monitor.lastRunTime, "2026-06-23T10:00:00.000Z");

  Assert.throws(() => Monitor.fromJSON({}), /Monitor is invalid/);
});

add_task(function test_parseMonitorResult_accepts_json_and_fenced_json() {
  const monitor = makeMonitor();

  Assert.deepEqual(
    monitor.parseMonitorResult({
      finalOutput:
        ' { "explanation": "  Price dropped to $9.99  ", "conditionMet": true } ',
    }),
    {
      explanation: "Price dropped to $9.99",
      conditionMet: true,
    }
  );

  Assert.deepEqual(
    monitor.parseMonitorResult({
      finalOutput:
        '```json\n{ "explanation": "Still $12", "conditionMet": false }\n```',
    }),
    {
      explanation: "Still $12",
      conditionMet: false,
    }
  );
});

add_task(function test_parseMonitorResult_falls_back_for_invalid_response() {
  const monitor = makeMonitor();

  Assert.deepEqual(monitor.parseMonitorResult({ finalOutput: "not json" }), {
    explanation: "not json",
    conditionMet: false,
  });
  Assert.deepEqual(monitor.parseMonitorResult({ finalOutput: "   " }), {
    explanation:
      "The monitor check completed, but no model response was returned.",
    conditionMet: false,
  });
  Assert.deepEqual(
    monitor.parseMonitorResult({
      finalOutput:
        '{ "explanation": "String boolean is not enough", "conditionMet": "true" }',
    }),
    {
      explanation:
        '{ "explanation": "String boolean is not enough", "conditionMet": "true" }',
      conditionMet: false,
    }
  );
});

add_task(async function test_run_skips_scheduled_check_until_due_or_enabled() {
  const sb = sinon.createSandbox();
  try {
    const monitor = makeMonitor({
      nextRunTime: isoFromNow(60 * 60 * 1000),
    });
    const runMonitorCheck = sb.stub(monitor, "runMonitorCheck").resolves({
      explanation: "Should not run",
      conditionMet: true,
    });
    const scheduleNextRun = sb.stub(monitor, "scheduleNextRun");

    Assert.equal(await monitor.run(), undefined);
    sinon.assert.notCalled(runMonitorCheck);
    sinon.assert.calledOnce(scheduleNextRun);
    Assert.equal(monitor.history.length, 0);

    monitor.enabled = false;
    monitor.nextRunTime = isoFromNow(-60 * 1000);
    scheduleNextRun.resetHistory();
    Assert.equal(await monitor.run(), undefined);
    sinon.assert.notCalled(runMonitorCheck);
    sinon.assert.calledOnce(scheduleNextRun);
    Assert.equal(monitor.history.length, 0);
  } finally {
    sb.restore();
  }
});

add_task(
  async function test_run_manual_success_records_history_and_resets_next_run_time() {
    const sb = sinon.createSandbox();
    try {
      const monitor = makeMonitor({
        enabled: false,
        nextRunTime: isoFromNow(60 * 60 * 1000),
      });
      const originalLastRunTime = monitor.lastRunTime;
      const originalNextRunTime = monitor.nextRunTime;
      const runMonitorCheck = sb.stub(monitor, "runMonitorCheck").resolves({
        explanation: "Price is $8.99",
        conditionMet: true,
      });
      const saveAndNotify = sb.stub(MonitorAgent, "_saveAndNotify").resolves();
      const scheduleNextRun = sb.stub(monitor, "scheduleNextRun");

      const beforeRun = Date.now();
      await monitor.run({ manual: true });
      const lastRunTimeMs = Date.parse(monitor.lastRunTime);

      sinon.assert.calledOnce(runMonitorCheck);
      Assert.equal(runMonitorCheck.firstCall.args[0].flowId, "monitor-1");
      sinon.assert.calledTwice(saveAndNotify);
      sinon.assert.notCalled(scheduleNextRun);
      Assert.notEqual(monitor.lastRunTime, originalLastRunTime);
      Assert.notEqual(monitor.nextRunTime, originalNextRunTime);
      Assert.greaterOrEqual(lastRunTimeMs, beforeRun);
      Assert.lessOrEqual(lastRunTimeMs, Date.now());
      Assert.equal(
        monitor.nextRunTime,
        monitor.schedule.getNextRunTime(monitor.lastRunTime).toISOString()
      );
      Assert.equal(monitor.history.length, 1);
      Assert.equal(monitor.history[0].status, "success");
      Assert.equal(monitor.history[0].resultExplanation, "Price is $8.99");
      Assert.equal(monitor.history[0].conditionMet, true);
    } finally {
      sb.restore();
    }
  }
);

add_task(async function test_run_scheduled_success_updates_next_run_time() {
  const sb = sinon.createSandbox();
  try {
    const monitor = makeMonitor({
      nextRunTime: isoFromNow(-60 * 1000),
    });
    sb.stub(monitor, "runMonitorCheck").resolves({
      explanation: "Still too expensive",
      conditionMet: false,
    });
    sb.stub(MonitorAgent, "_saveAndNotify").resolves();
    sb.stub(monitor, "scheduleNextRun");

    const beforeRun = Date.now();
    await monitor.run();
    const lastRunTimeMs = Date.parse(monitor.lastRunTime);

    Assert.greaterOrEqual(lastRunTimeMs, beforeRun);
    Assert.lessOrEqual(lastRunTimeMs, Date.now());
    Assert.equal(
      monitor.nextRunTime,
      monitor.schedule.getNextRunTime(monitor.lastRunTime).toISOString()
    );
    Assert.equal(monitor.history.length, 1);
    Assert.equal(monitor.history[0].status, "success");
    Assert.equal(monitor.history[0].conditionMet, false);
  } finally {
    sb.restore();
  }
});

add_task(async function test_run_records_error_and_allows_later_runs() {
  const sb = sinon.createSandbox();
  try {
    const monitor = makeMonitor({
      nextRunTime: isoFromNow(-60 * 1000),
    });
    const runMonitorCheck = sb
      .stub(monitor, "runMonitorCheck")
      .rejects(new Error("network unavailable"));
    sb.stub(MonitorAgent, "_saveAndNotify").resolves();
    sb.stub(monitor, "scheduleNextRun");

    await monitor.run();

    Assert.equal(monitor.history.length, 1);
    Assert.equal(monitor.history[0].status, "error");
    Assert.equal(monitor.history[0].resultExplanation, "network unavailable");

    runMonitorCheck.resolves({
      explanation: "Recovered",
      conditionMet: false,
    });
    await monitor.run({ manual: true });
    Assert.equal(monitor.history.length, 2);
    Assert.equal(monitor.history[1].status, "success");
  } finally {
    sb.restore();
  }
});

add_task(async function test_run_save_failure_does_not_start_model_check() {
  const sb = sinon.createSandbox();
  try {
    const monitor = makeMonitor({
      nextRunTime: isoFromNow(-60 * 1000),
    });
    const originalLastRunTime = monitor.lastRunTime;
    const originalNextRunTime = monitor.nextRunTime;
    const runMonitorCheck = sb.stub(monitor, "runMonitorCheck").resolves({
      explanation: "Should not run",
      conditionMet: true,
    });
    const saveAndNotify = sb.stub(MonitorAgent, "_saveAndNotify");
    saveAndNotify.onFirstCall().rejects(new Error("database unavailable"));
    saveAndNotify.onSecondCall().resolves();
    sb.stub(monitor, "scheduleNextRun");

    const beforeRun = Date.now();
    await monitor.run({ manual: true });
    const lastRunTimeMs = Date.parse(monitor.lastRunTime);

    sinon.assert.notCalled(runMonitorCheck);
    sinon.assert.calledTwice(saveAndNotify);
    Assert.equal(monitor.history.length, 1);
    Assert.equal(monitor.history[0].status, "error");
    Assert.equal(monitor.history[0].resultExplanation, "database unavailable");
    Assert.notEqual(monitor.lastRunTime, originalLastRunTime);
    Assert.notEqual(monitor.nextRunTime, originalNextRunTime);
    Assert.greaterOrEqual(lastRunTimeMs, beforeRun);
    Assert.lessOrEqual(lastRunTimeMs, Date.now());
    Assert.equal(
      monitor.nextRunTime,
      monitor.schedule.getNextRunTime(monitor.lastRunTime).toISOString()
    );
  } finally {
    sb.restore();
  }
});

add_task(
  async function test_run_timeout_releases_state_and_ignores_late_result() {
    const sb = sinon.createSandbox();
    try {
      const monitor = makeMonitor({
        nextRunTime: isoFromNow(-60 * 1000),
      });
      const deferred = makeDeferred();
      const runMonitorCheck = sb.stub(monitor, "runMonitorCheck");
      runMonitorCheck.onFirstCall().returns(deferred.promise);
      runMonitorCheck.onSecondCall().resolves({
        explanation: "Recovered",
        conditionMet: false,
      });
      sb.stub(MonitorAgent, "_saveAndNotify").resolves();
      const scheduleNextRun = sb.stub(monitor, "scheduleNextRun");

      await monitor.run({ timeoutMs: 1 });

      Assert.equal(monitor.history.length, 1);
      Assert.equal(monitor.history[0].status, "error");
      Assert.equal(
        monitor.history[0].resultExplanation,
        "Monitor check timed out."
      );
      sinon.assert.calledOnce(runMonitorCheck);
      sinon.assert.calledOnce(scheduleNextRun);

      await monitor.run({ manual: true });
      Assert.equal(monitor.history.length, 2);
      Assert.equal(monitor.history[1].status, "success");
      Assert.equal(monitor.history[1].resultExplanation, "Recovered");
      sinon.assert.calledTwice(runMonitorCheck);
      sinon.assert.calledTwice(scheduleNextRun);

      deferred.resolve({
        explanation: "Late result",
        conditionMet: true,
      });
      await Promise.resolve();
      Assert.equal(monitor.history.length, 2);
      Assert.equal(monitor.history[0].status, "error");
      Assert.equal(
        monitor.history[0].resultExplanation,
        "Monitor check timed out."
      );
      Assert.equal(monitor.history[1].conditionMet, false);
      sinon.assert.calledTwice(scheduleNextRun);
    } finally {
      sb.restore();
    }
  }
);

add_task(async function test_run_deduplicates_concurrent_checks() {
  const sb = sinon.createSandbox();
  try {
    const monitor = makeMonitor();
    const deferred = makeDeferred();
    const runMonitorCheck = sb
      .stub(monitor, "runMonitorCheck")
      .returns(deferred.promise);
    sb.stub(MonitorAgent, "_saveAndNotify").resolves();
    sb.stub(monitor, "scheduleNextRun");

    const firstRun = monitor.run({ manual: true });
    Assert.equal(await monitor.run({ manual: true }), undefined);
    sinon.assert.calledOnce(runMonitorCheck);
    Assert.equal(monitor.history.length, 1);
    Assert.equal(monitor.history[0].status, "running");

    deferred.resolve({
      explanation: "Completed",
      conditionMet: false,
    });
    await firstRun;

    Assert.equal(monitor.history[0].status, "success");
    await monitor.run({ manual: true });
    sinon.assert.calledTwice(runMonitorCheck);
  } finally {
    sb.restore();
  }
});

add_task(
  async function test_MonitorAgent_delete_during_run_does_not_reschedule() {
    await clearStoredMonitors();
    const sb = sinon.createSandbox();
    try {
      const deferred = makeDeferred();
      let signal;
      const runMonitorCheck = sb
        .stub(Monitor.prototype, "runMonitorCheck")
        .callsFake(({ signal: runSignal }) => {
          signal = runSignal;
          return deferred.promise;
        });
      sb.stub(MonitorAgent, "_saveAndNotify").resolves();
      const scheduleNextRun = sb.spy(Monitor.prototype, "scheduleNextRun");

      const monitor = await createStoredMonitor(stockMonitorOptions());
      scheduleNextRun.resetHistory();

      const runPromise = MonitorAgent.runNow(monitor.id);
      await TestUtils.waitForCondition(
        () => runMonitorCheck.calledOnce,
        "Monitor check should start"
      );

      Assert.ok(signal, "Run should pass an abort signal to the monitor check");
      Assert.equal(await MonitorAgent.deleteMonitor(monitor.id), true);
      Assert.equal(signal.aborted, true);
      deferred.resolve({
        explanation: "Completed",
        conditionMet: false,
      });
      await runPromise;

      sinon.assert.notCalled(scheduleNextRun);
      Assert.deepEqual(await MonitorAgent.listMonitors(), []);
    } finally {
      sb.restore();
      await clearStoredMonitors();
    }
  }
);

add_task(
  async function test_MonitorAgent_uninit_during_run_does_not_reschedule() {
    await clearStoredMonitors();
    const sb = sinon.createSandbox();
    try {
      const deferred = makeDeferred();
      let signal;
      const runMonitorCheck = sb
        .stub(Monitor.prototype, "runMonitorCheck")
        .callsFake(({ signal: runSignal }) => {
          signal = runSignal;
          return deferred.promise;
        });
      sb.stub(MonitorAgent, "_saveAndNotify").resolves();
      const scheduleNextRun = sb.spy(Monitor.prototype, "scheduleNextRun");

      const monitor = await createStoredMonitor(stockMonitorOptions());
      scheduleNextRun.resetHistory();

      const runPromise = MonitorAgent.runNow(monitor.id);
      await TestUtils.waitForCondition(
        () => runMonitorCheck.calledOnce,
        "Monitor check should start"
      );

      MonitorAgent.uninit();
      Assert.ok(signal.aborted, "Run should be aborted during uninit");
      deferred.resolve({
        explanation: "Completed",
        conditionMet: false,
      });
      await runPromise;

      sinon.assert.notCalled(scheduleNextRun);
    } finally {
      sb.restore();
      await clearStoredMonitors();
    }
  }
);

add_task(function test_addHistoryEntry_caps_history() {
  const monitor = makeMonitor();
  for (let i = 0; i < 35; i++) {
    monitor.addHistoryEntry({ id: `history-${i}` });
  }

  Assert.equal(monitor.history.length, 30);
  Assert.equal(monitor.history[0].id, "history-5");
  Assert.equal(monitor.history.at(-1).id, "history-34");
});

add_task(async function test_MonitorAgent_ignores_legacy_pref_storage() {
  await clearStoredMonitors();
  Services.prefs.setStringPref(
    PREF_MONITOR_AGENTS,
    JSON.stringify([
      {
        id: "valid-monitor",
        title: "Saved title",
        monitorPrompt: "Saved prompt",
        watchUrls: ["https://example.com/saved"],
        schedule: intervalSchedule(5),
      },
      {
        id: "bad-monitor",
        monitorPrompt: "",
        watchUrls: [],
        schedule: intervalSchedule(5),
      },
      {
        id: "bad-schedule",
        monitorPrompt: "Saved prompt",
        watchUrls: ["https://example.com/saved"],
        schedule: { type: "yearly" },
      },
    ])
  );

  const monitors = await MonitorAgent.listMonitors();

  Assert.deepEqual(monitors, []);
  Assert.ok(
    Services.prefs.prefHasUserValue(PREF_MONITOR_AGENTS),
    "Legacy monitor pref should not be treated as monitor storage"
  );

  await clearStoredMonitors();
});

add_task(
  async function test_MonitorAgent_create_update_delete_persists_and_notifies() {
    await clearStoredMonitors();
    const sb = sinon.createSandbox();
    const observer = observeTopic(MONITOR_AGENTS_CHANGED_TOPIC);
    try {
      const scheduleNextRun = sb.stub(Monitor.prototype, "scheduleNextRun");
      const clearTimer = sb.stub(Monitor.prototype, "clearTimer");

      const monitor = await createStoredMonitor(
        stockMonitorOptions({
          prompt: "  Watch stock  ",
          watchUrls: [" https://example.com/stock "],
          pageTitle: " Stock page ",
        })
      );

      Assert.equal(monitor.monitorPrompt, "Watch stock");
      Assert.deepEqual(monitor.watchUrls, ["https://example.com/stock"]);
      Assert.equal(monitor.title, "Stock page");
      sinon.assert.calledOnce(scheduleNextRun);
      Assert.equal(observer.count, 1);

      const savedAfterCreate = await MonitorAgent.listMonitors();
      Assert.equal(savedAfterCreate.length, 1);
      Assert.equal(savedAfterCreate[0].id, monitor.id);
      Assert.ok(
        !Services.prefs.prefHasUserValue(PREF_MONITOR_AGENTS),
        "Monitors should no longer be saved to prefs"
      );

      monitor.title = "client mutation";
      Assert.notEqual(
        (await MonitorAgent.listMonitors())[0].title,
        "client mutation"
      );

      await MonitorAgent.updateMonitor(monitor.id, {
        monitorPrompt: "Check both pages",
        watchUrls: [
          " https://example.com/one ",
          "",
          null,
          "https://example.com/two",
        ],
        title: "Updated title",
        enabled: false,
        schedule: { type: "daily", hour: 8, minute: 30 },
      });

      const updated = (await MonitorAgent.listMonitors())[0];
      Assert.equal(updated.monitorPrompt, "Check both pages");
      Assert.deepEqual(updated.watchUrls, [
        "https://example.com/one",
        "https://example.com/two",
      ]);
      Assert.equal(updated.title, "Updated title");
      Assert.equal(updated.enabled, false);
      Assert.equal(updated.schedule.type, "daily");
      sinon.assert.calledTwice(scheduleNextRun);
      Assert.equal(observer.count, 2);

      Assert.equal(await MonitorAgent.updateMonitor("missing", {}), null);
      Assert.equal(await MonitorAgent.deleteMonitor(monitor.id), true);
      sinon.assert.calledOnce(clearTimer);
      Assert.equal(observer.count, 3);
      Assert.equal(await MonitorAgent.deleteMonitor(monitor.id), false);
      Assert.equal(observer.count, 3);
      Assert.deepEqual(await MonitorAgent.listMonitors(), []);
    } finally {
      observer.cleanup();
      sb.restore();
      await clearStoredMonitors();
    }
  }
);

add_task(async function test_MonitorAgent_persists_monitors_in_database() {
  await clearStoredMonitors();
  const sb = sinon.createSandbox();
  try {
    sb.stub(Monitor.prototype, "scheduleNextRun");

    await MonitorAgent.createMonitor(
      stockMonitorOptions({ pageTitle: "Stock page" })
    );

    MonitorAgent._unloadForTesting();

    const saved = await MonitorAgent.listMonitors();
    Assert.equal(saved.length, 1);
    Assert.equal(saved[0].monitorPrompt, "Watch stock");
    Assert.deepEqual(saved[0].watchUrls, ["https://example.com/stock"]);
    Assert.equal(saved[0].schedule.type, "interval");
  } finally {
    sb.restore();
    await clearStoredMonitors();
  }
});

add_task(async function test_MonitorStore_serializes_writes_in_call_order() {
  await clearStoredMonitors();
  try {
    const monitor = makeMonitor({
      id: "queued-monitor",
    });

    await Promise.all([
      MonitorStore.saveMonitors([monitor]),
      MonitorStore.deleteMonitor(monitor.id),
    ]);
    Assert.deepEqual(await MonitorStore.listMonitors(), []);

    await Promise.all([
      MonitorStore.deleteMonitor(monitor.id),
      MonitorStore.saveMonitors([monitor]),
    ]);
    const saved = await MonitorStore.listMonitors();
    Assert.equal(saved.length, 1);
    Assert.equal(saved[0].id, monitor.id);
  } finally {
    await clearStoredMonitors();
  }
});

add_task(
  async function test_MonitorStore_rejects_newer_schema_without_deleting_database() {
    await clearStoredMonitors();
    try {
      const conn = await Sqlite.openConnection({
        path: MonitorStore.databaseFilePath,
      });
      try {
        await conn.setSchemaVersion(2);
      } finally {
        await conn.close();
      }

      await Assert.rejects(
        MonitorAgent.listMonitors(),
        /schema version 2 is newer than supported version 1/
      );

      const verifyConn = await Sqlite.openConnection({
        path: MonitorStore.databaseFilePath,
      });
      try {
        Assert.equal(await verifyConn.getSchemaVersion(), 2);
      } finally {
        await verifyConn.close();
      }
    } finally {
      await clearStoredMonitors();
    }
  }
);

add_task(
  async function test_MonitorAgent_update_rejects_invalid_required_fields() {
    await clearStoredMonitors();
    const sb = sinon.createSandbox();
    const observer = observeTopic(MONITOR_AGENTS_CHANGED_TOPIC);
    try {
      const scheduleNextRun = sb.stub(Monitor.prototype, "scheduleNextRun");
      const monitor = await createStoredMonitor(
        stockMonitorOptions({ pageTitle: "Stock page" })
      );
      const original = (await MonitorAgent.listMonitors())[0];
      scheduleNextRun.resetHistory();

      await Assert.rejects(
        MonitorAgent.updateMonitor(monitor.id, { monitorPrompt: " " }),
        /Monitor is invalid/
      );
      await Assert.rejects(
        MonitorAgent.updateMonitor(monitor.id, { watchUrls: [" ", null] }),
        /Monitor is invalid/
      );
      await Assert.rejects(
        MonitorAgent.updateMonitor(monitor.id, { watchUrls: ["about:config"] }),
        /Monitor is invalid/
      );
      await Assert.rejects(
        MonitorAgent.updateMonitor(monitor.id, {
          watchUrls: ["ftp://example.com/file"],
        }),
        /Monitor is invalid/
      );

      const saved = (await MonitorAgent.listMonitors())[0];
      Assert.equal(saved.monitorPrompt, original.monitorPrompt);
      Assert.deepEqual(saved.watchUrls, original.watchUrls);
      Assert.equal(observer.count, 1);
      sinon.assert.notCalled(scheduleNextRun);
    } finally {
      observer.cleanup();
      sb.restore();
      await clearStoredMonitors();
    }
  }
);

add_task(async function test_MonitorAgent_create_caps_monitor_count() {
  await clearStoredMonitors();
  const sb = sinon.createSandbox();
  try {
    sb.stub(Monitor.prototype, "scheduleNextRun");

    for (let i = 0; i < TOTAL_NUM_MONITORS; i++) {
      await MonitorAgent.createMonitor({
        prompt: `Watch ${i}`,
        watchUrls: [`https://example.com/${i}`],
        schedule: intervalSchedule(),
      });
      Assert.equal(
        (await MonitorAgent.listMonitors()).length,
        i + 1,
        `Should create monitor ${i}`
      );
    }

    Assert.equal(
      await MonitorAgent.createMonitor({
        prompt: "Too many",
        watchUrls: ["https://example.com/too-many"],
        schedule: intervalSchedule(),
      }),
      null
    );
    Assert.equal(
      (await MonitorAgent.listMonitors()).length,
      TOTAL_NUM_MONITORS
    );
  } finally {
    sb.restore();
    await clearStoredMonitors();
  }
});

add_task(async function test_MonitorAgent_create_accepts_watchUrls() {
  await clearStoredMonitors();
  const sb = sinon.createSandbox();
  try {
    sb.stub(Monitor.prototype, "scheduleNextRun");
    const monitor = await createStoredMonitor({
      prompt: "Watch multiple pages",
      watchUrls: [" https://example.com/one ", "", "https://example.com/two"],
      schedule: intervalSchedule(),
    });

    Assert.deepEqual(monitor.watchUrls, [
      "https://example.com/one",
      "https://example.com/two",
    ]);
  } finally {
    sb.restore();
    await clearStoredMonitors();
  }
});

add_task(
  async function test_MonitorAgent_init_and_uninit_manage_loaded_monitors() {
    await clearStoredMonitors();
    const sb = sinon.createSandbox();
    try {
      const scheduleNextRun = sb.stub(Monitor.prototype, "scheduleNextRun");
      const clearTimer = sb.stub(Monitor.prototype, "clearTimer");

      await MonitorAgent.createMonitor({
        prompt: "One",
        watchUrls: ["https://example.com/one"],
        schedule: intervalSchedule(),
      });
      await MonitorAgent.createMonitor({
        prompt: "Two",
        watchUrls: ["https://example.com/two"],
        schedule: intervalSchedule(20),
      });
      scheduleNextRun.resetHistory();

      await MonitorAgent.init();
      sinon.assert.calledTwice(scheduleNextRun);

      MonitorAgent.uninit();
      sinon.assert.calledTwice(clearTimer);
    } finally {
      sb.restore();
      await clearStoredMonitors();
    }
  }
);
