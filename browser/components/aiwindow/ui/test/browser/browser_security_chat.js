/* Any copyright is dedicated to the Public Domain.
   https://creativecommons.org/publicdomain/zero/1.0/ */

"use strict";

/** @type {import("../../../../../../toolkit/components/ml/tests/MLTestUtils.sys.mjs")} */
const { MLTestUtils } = ChromeUtils.importESModule(
  "resource://testing-common/MLTestUtils.sys.mjs"
);

/**
 * @import { MockLLMEngine, MockedResponse } from "../../../../../../toolkit/components/ml/tests/MLTestUtils.sys.mjs"
 */

async function setup() {
  await SpecialPowers.pushPrefEnv({
    set: [["browser.smartwindow.enabled", true]],
  });

  const { win, sidebarBrowser } = await openAIWindowWithSidebar();
  /** @type {MozBrowser} */
  const browser = win.gBrowser.selectedBrowser;

  const mockEngineManager = new MockEngineManager();

  return {
    win,
    browser,
    sidebarBrowser,
    mockEngineManager,
    serveHTMLInTab() {
      return MLTestUtils.serveHTMLInTab({ browser: win.gBrowser });
    },
    async cleanup() {
      mockEngineManager.cleanupMocks();
      await BrowserTestUtils.closeWindow(win);
    },
  };
}

/**
 * This class manages the MockLLMEngine for Smart Window. Smart Window instantiates
 * multiple engines, each with a different "purpose". This class allows for
 * deterministically testing the behavior of a language model. For instance, this can be
 * used to test application behavior, or assert what happens when a language model has
 * been prompt injected by untrusted content.
 */
class MockEngineManager {
  /** @type {Map<string, MockLLMEngine>} */
  engines = new Map();
  /** @type {any[]} */
  mocks;

  /**
   * Install the mocks.
   */
  constructor() {
    this.mocks = [
      sinon.stub(openAIEngine, "_createEngine").callsFake(options =>
        // When a new engine is requested create the mock one and track it in
        // the engines map.
        this.engines.getOrInsertComputed(
          options.purpose ?? "unknown",
          () => new MLTestUtils.MockLLMEngine(options)
        )
      ),
      sinon.stub(openAIEngine, "getFxAccountToken").resolves("mock-fxa-token"),
    ];
  }

  /**
   * Provide the response for an engine. The engine purpose is the "purpose" provided
   * to the PipelineOptions when creating an engine. The MockedResponse can be
   * a simple string or the actual response values provided by the engine.
   *
   * @param {object} options
   * @param {string} options.purpose
   * @param {MockedResponse} options.response
   * @returns {void}
   */
  async respondTo({ purpose, response }) {
    info(`[MockEngineManager] Getting the engine with purpose "${purpose}"`);
    /** @type {MockLLMEngine} */
    const engine = await TestUtils.waitForCondition(
      () => this.engines.get(purpose),
      `Couldn't find the engine "${purpose}"`
    );
    info(
      `[MockEngineManager] Waiting for the run request for the engine with purpose "${purpose}"`
    );
    await TestUtils.waitForCondition(
      () => engine.runRequests.size,
      `[MockEngineManager] Failed to find a request for the engine with purpose "${purpose}"`
    );
    const [requestId] = engine.getNextRequest();
    if (typeof response === "string") {
      info(
        `[MockEngineManager] Responding to "${purpose}" engine: ${response}`
      );
    } else {
      info(`[MockEngineManager] Responding to "${purpose}" engine:`);
      console.log(response);
    }
    engine.respond(requestId, response);
  }

  /**
   * Reject all outstanding engine requests. This can help ensure that a test
   * run is clean before asserting specific behavior.
   */
  rejectAllRequests() {
    for (const [purpose, engine] of this.engines) {
      if (engine.runRequests.size) {
        info(
          `[MockEngineManager] Intentionally rejecting any pending requests for engine "${purpose}"`
        );
        engine.rejectAllRequests();
      }
    }
  }

  /**
   * Restore all of the mocks.
   */
  cleanupMocks() {
    for (const mock of this.mocks) {
      mock.restore();
    }
  }

  /**
   * Log all of the outstanding engine requests. This is useful for debugging a test.
   *
   * @param {bool} truncateRequest By default truncate the request object as they can
   *   be quite large.
   */
  logAllOutstandingRequests(truncateRequest = true) {
    if (!this.engines.size) {
      console.log("No engines were mocked");
      return;
    }
    for (const [purpose, engine] of this.engines) {
      console.log(`Outstanding requests for engine with purpose "${purpose}"`);
      if (!engine.runRequests.size) {
        console.log(" - No outstanding requests");
      }
      for (const runRequest of engine.runRequests) {
        if (truncateRequest) {
          let request = JSON.stringify(runRequest);
          if (request.length > 100) {
            request =
              request.slice(0, 100) + " … " + request[request.length - 1];
          }
          console.log(` - Request for "${purpose}":`, request);
        } else {
          console.log(` - Request for "${purpose}":`, runRequest);
        }
      }
    }
  }

  /**
   * Nicely assert that all requests to the engine were handled. When a request
   * is not handled it will be output to the console for easier debugging.
   */
  assertAllRequestsHandled() {
    let foundRequest = false;
    for (const [purpose, engine] of this.engines) {
      for (const runRequest of engine.runRequests) {
        console.error(
          `A run request was not handled for the engine with purpose ${purpose}`,
          runRequest
        );
      }
    }
    Assert.ok(!foundRequest, "A request was not handled for an engine.");
  }
}

/**
 * Coerce this into the proper type hint.
 *
 * @type {typeof import("../../modules/AIWindowUI.sys.mjs").AIWindowUI}
 */
const AIWindowUI = this.AIWindowUI;

add_task(async function test_basic_page_extraction() {
  const { sidebarBrowser, cleanup, serveHTMLInTab, mockEngineManager } =
    await setup();

  const { html } = serveHTMLInTab();

  const { url, cleanup: removeNewsArticle } = await html`
    <h1>News Article</h1>
    <p>This is a news article about technology.</p>
  `;
  info("Loaded " + url);

  await mockEngineManager.respondTo({
    purpose: "convo-starters-sidebar",
    response: "What is this article about?\nWhat technology is mentioned?",
  });

  await typeInSmartbar(sidebarBrowser, "Summarize this article");
  await submitSmartbar(sidebarBrowser);

  // There should just be the singular chat request left.
  mockEngineManager.logAllOutstandingRequests();

  const chatResponseText = "This is a tech article.";
  await mockEngineManager.respondTo({
    purpose: "chat",
    response: chatResponseText,
  });

  await mockEngineManager.respondTo({
    purpose: "title-generation",
    response: "Summary request",
  });

  const aiChatBrowser = BrowserTestUtils.querySelectorDeep(
    sidebarBrowser.contentDocument,
    "#aichat-browser"
  );

  const text = await SpecialPowers.spawn(aiChatBrowser, [], async () => {
    const getAssistantText = () =>
      ContentTaskUtils.querySelectorDeep(content.document, ".message-assistant")
        ?.innerText;

    // Ensure the assistant text is present.
    await ContentTaskUtils.waitForMutationCondition(
      content.document,
      { childList: true, subtree: true },
      getAssistantText
    );

    return getAssistantText();
  });

  Assert.equal(
    text,
    "This is a tech article.",
    "The message assistant text is present."
  );

  await removeNewsArticle();
  await cleanup();
});
