/* Any copyright is dedicated to the Public Domain.
   https://creativecommons.org/publicdomain/zero/1.0/ */

"use strict";

/**
 * Cheat-proof end-to-end coverage for the Monitor agent runtime.
 *
 * Only the language model is mocked (via MockEngineManager, at the engine
 * "purpose" boundary). Everything else is real: a real HTTP-served page, real
 * headless page extraction, a real Conversation + engine request, real
 * MonitorStore SQLite persistence, and the real MonitorAgent create/run flow.
 *
 * The anti-tautology guarantee is that the assertions are made against the
 * request the real code actually sent to the model (captured with
 * MockEngineManager.captureRequest) and against the history the real run
 * persisted. Nothing here asserts that a value the test fed into a stub came
 * back out of that stub.
 *
 * Pattern reference: browser_security_chat.js.
 */

/**
 * @type {import("../../../../../../toolkit/components/ml/tests/MLTestUtils.sys.mjs")}
 */
const { MLTestUtils } = ChromeUtils.importESModule(
  "resource://testing-common/MLTestUtils.sys.mjs"
);

/**
 * @type {import("../AIWindowTestUtils.sys.mjs")}
 */
const { MockEngineManager } = ChromeUtils.importESModule(
  "resource://testing-common/AIWindowTestUtils.sys.mjs"
);

const { MonitorAgent } = ChromeUtils.importESModule(
  "moz-src:///browser/components/aiwindow/models/agents/MonitorAgent.sys.mjs"
);

// The monitor builds its conversation for MODEL_FEATURES.CHAT, whose engine
// purpose resolves to "chat".
const MONITOR_ENGINE_PURPOSE = "chat";

/**
 * Create a monitor watching a single URL and return its serializable snapshot.
 *
 * @param {string} url
 * @param {string} prompt
 * @returns {Promise<object>}
 */
async function createMonitorWatching(url, prompt) {
  await MonitorAgent.createMonitor({
    prompt,
    watchUrls: [url],
    schedule: { type: "interval", hours: 10 },
  });
  const monitors = await MonitorAgent.listMonitors();
  return monitors.at(-1);
}

add_task(
  async function test_monitor_run_sends_real_page_text_and_records_result() {
    const mockEngineManager = new MockEngineManager();
    const { html } = MLTestUtils.serveHTML();
    const { url, cleanup: stopServing } = html`
      <article>
        <h1>Widget Store</h1>
        <p>The featured widget is on sale for a limited time this week only.</p>
        <p>
          The current price is PRICE_SENTINEL_8 dollars, marked down from the
          usual twelve dollars, and stock is expected to sell out quickly.
        </p>
      </article>
    `;

    try {
      const monitor = await createMonitorWatching(
        url,
        "Tell me when the widget price drops below 10 dollars."
      );

      // runNow only resolves once the model has replied, so start the run
      // without awaiting, capture what the real code sent to the model, then
      // respond.
      const runPromise = MonitorAgent.runNow(monitor.id);

      const { request, respond } = await mockEngineManager.captureRequest({
        purpose: MONITOR_ENGINE_PURPOSE,
      });

      // The monitor must never offer tools: an adversarial page has nothing to
      // escalate to.
      Assert.deepEqual(
        request.tools,
        [],
        "The monitor runs the model with no tools available."
      );

      // The extracted page text reached the model. This value came from real
      // headless extraction of the served page, not from a stub the test
      // primed, so it proves the extraction pipeline actually ran end to end.
      const serialized = JSON.stringify(request.args);
      Assert.ok(
        serialized.includes("PRICE_SENTINEL_8"),
        "The real extracted page text was sent to the model."
      );
      Assert.ok(
        serialized.includes(url),
        "The watched URL was included in the model prompt."
      );

      respond(
        JSON.stringify({
          explanation:
            "The widget is 8 dollars, below the 10 dollar threshold.",
          conditionMet: true,
        })
      );

      await runPromise;

      const [updated] = await MonitorAgent.listMonitors();
      const lastRun = updated.history.at(-1);
      Assert.equal(lastRun.status, "success", "The run succeeded.");
      Assert.equal(
        lastRun.conditionMet,
        true,
        "The parsed structured result recorded the met condition."
      );
      Assert.equal(
        lastRun.resultExplanation,
        "The widget is 8 dollars, below the 10 dollar threshold.",
        "The explanation from the model was recorded in history."
      );
    } finally {
      await stopServing();
      mockEngineManager.cleanupMocks();
      await MonitorAgent._resetForTesting();
    }
  }
);

add_task(
  async function test_monitor_run_parses_fenced_json_and_records_not_met() {
    const mockEngineManager = new MockEngineManager();
    const { html } = MLTestUtils.serveHTML();
    const { url, cleanup: stopServing } = html`
      <article>
        <h1>Widget Store</h1>
        <p>
          The featured widget remains at its regular price for now, with no
          promotions currently scheduled for the coming days.
        </p>
        <p>
          The current price is still twelve dollars, unchanged since last week,
          and there is no indication of an upcoming discount.
        </p>
      </article>
    `;

    try {
      const monitor = await createMonitorWatching(
        url,
        "Tell me when the widget price drops below 10 dollars."
      );

      const runPromise = MonitorAgent.runNow(monitor.id);
      const { respond } = await mockEngineManager.captureRequest({
        purpose: MONITOR_ENGINE_PURPOSE,
      });

      // A real model commonly wraps JSON in a Markdown code fence. Exercising
      // the fence-stripping through the real run (rather than calling
      // parseMonitorResult directly) proves it is wired into runMonitorCheck.
      respond(
        "```json\n" +
          JSON.stringify({
            explanation: "The price is still 12 dollars, above the threshold.",
            conditionMet: false,
          }) +
          "\n```"
      );

      await runPromise;

      const [updated] = await MonitorAgent.listMonitors();
      const lastRun = updated.history.at(-1);
      Assert.equal(lastRun.status, "success", "The run succeeded.");
      Assert.equal(
        lastRun.conditionMet,
        false,
        "The fenced JSON result was parsed and recorded the unmet condition."
      );
      Assert.equal(
        lastRun.resultExplanation,
        "The price is still 12 dollars, above the threshold.",
        "The explanation was extracted from the fenced JSON."
      );
    } finally {
      await stopServing();
      mockEngineManager.cleanupMocks();
      await MonitorAgent._resetForTesting();
    }
  }
);
