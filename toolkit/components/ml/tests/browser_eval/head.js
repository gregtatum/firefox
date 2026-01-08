/* import-globals-from ../browser/head.js */

Services.scriptloader.loadSubScript(
  "chrome://mochitests/content/browser/browser/components/translations/tests/browser/head.js",
  this
);

/**
 *
 * @param {string} lang - The HTML language tag.
 */
function getHtmlTagFunction(lang = "en") {
  /**
   * Use a tagged template literal to create a page extraction actor test. This spins
   * up an http server that serves the markup in a new tab. The page extractor can then
   * be used on the page.
   *
   * @param {TemplateStringsArray} strings - The literal string parts.
   * @param {...any} values - The interpolated expressions.
   */
  return function html(strings, ...values) {
    // Convert the arguments into markup.
    let markup = "";
    for (let i = 0; i < strings.length; i++) {
      markup += strings[i];
      if (i < values.length) {
        markup += values[i];
      }
    }

    return `<!DOCTYPE html lang="${lang}">
<html>
<head>
  <meta charset="utf-16" />
</head>
<body>
${markup}
</body>
</html>
`;
  };
}

async function setupEvaluation({ markup }) {
  {
    const { RemoteSettingsClient } = ChromeUtils.importESModule(
      "resource://services-settings/RemoteSettingsClient.sys.mjs"
    );
    RemoteSettingsClient.prototype.validateCollectionSignature = async () => {};
  }

  const { url, serverClosed } = serveOnce(markup);

  const tab = await BrowserTestUtils.openNewForegroundTab(
    gBrowser,
    url,
    true // waitForLoad
  );

  return {
    tab,

    async cleanup() {
      info("Cleaning up");
      await serverClosed;
      BrowserTestUtils.removeTab(tab);

      clearDirtyPrefs();
    },
  };
}

function clearDirtyPrefs() {
  Services.prefs.clearUserPref(
    "browser.translations.mostRecentTargetLanguages"
  );
  Services.prefs.clearUserPref("intl.locale.requested");
}

/**
 * Start an HTTP server that serves page.html with the provided HTML.
 *
 * @param {string} html
 */
function serveOnce(html) {
  /** @type {import("../../../../../netwerk/test/httpserver/httpd.sys.mjs")} */
  const { HttpServer } = ChromeUtils.importESModule(
    "resource://testing-common/httpd.sys.mjs"
  );
  info("Create server");
  const server = new HttpServer();

  const { promise, resolve } = Promise.withResolvers();

  server.registerPathHandler("/page.html", (_request, response) => {
    info("Request received for: " + url);
    response.setHeader("Content-Type", "text/html");
    response.write(html);
    resolve(server.stop());
  });

  server.start(-1);

  let { primaryHost, primaryPort } = server.identity;
  // eslint-disable-next-line @microsoft/sdl/no-insecure-url
  const url = `http://${primaryHost}:${primaryPort}/page.html`;
  info("Server listening for: " + url);

  return { url, serverClosed: promise };
}

/**
 * Report eval data out to stdout, which will be picked up by the test harness for
 * analysis.
 *
 * @param {any} data - JSON serializable data.
 */
function reportEvalResult(data) {
  info("evalDataPayload " + JSON.stringify(data));

  dump("-------------------------------------\n");
  dump("Eval result:\n");
  dump(JSON.stringify(data, null, 2));
  dump("\n");
}

/**
 * Wait for every element in the selector to have been mutated once.
 *
 * @param {MozBrowser} browser
 * @param {string} selector
 * @returns {Promise<void>}
 */
function waitForMutations(browser, selector) {
  return SpecialPowers.spawn(browser, [selector], async selector => {
    const elements = new Set(content.document.querySelectorAll(selector));

    await new Promise(resolve => {
      for (const element of elements) {
        const observer = new content.MutationObserver(() => {
          elements.delete(element);
          observer.disconnect();
          if (elements.size === 0) {
            resolve();
          }
        });
        observer.observe(content.document.querySelector("article"), {
          subtree: true,
          characterData: true,
          childList: true,
        });
      }
    });
  });
}
