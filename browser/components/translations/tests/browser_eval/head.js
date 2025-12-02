/* import-globals-from ../browser/head.js */

Services.scriptloader.loadSubScript(
  "chrome://mochitests/content/browser/browser/components/translations/tests/browser/head.js",
  this
);

/**
 * Use a tagged template literal to create a page extraction actor test. This spins
 * up an http server that serves the markup in a new tab. The page extractor can then
 * be used on the page.
 *
 * @param {TemplateStringsArray} strings - The literal string parts.
 * @param {...any} values - The interpolated expressions.
 */
function html(strings, ...values) {
  // Convert the arguments into markup.
  let markup = "";
  for (let i = 0; i < strings.length; i++) {
    markup += strings[i];
    if (i < values.length) {
      markup += values[i];
    }
  }

  return `<!DOCTYPE html><head><body>${markup}</body>`;
}

async function setupEvaluation({
  markup,
  endToEndTest = false,
  architecture, // unused now
  languagePairs, // unused now
  appLocales,
  systemLocales = ["en"],
  webLanguages,
}) {
  const proxyURL = Services.env.get("ML_SERVICES_PROXY_URL");
  if (!proxyURL) {
    throw new Error(
      "The ML_SERVICES_PROXY_URL was not set for the test. This is set when run " +
        "via the mozpertest harness (./mach perftest path/to/test)."
    );
  }

  info("Setting the proxy URL for remote settings: " + proxyURL);
  Services.prefs.setStringPref("services.settings.server", proxyURL);

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
  console.log(`!!! tab`, tab);

  const { availableLocales, requestedLocales } = Services.locale;

  if (webLanguages) {
    await SpecialPowers.pushPrefEnv({
      set: [["intl.accept_languages", webLanguages.join(",")]],
    });
  }

  return {
    tab,

    async cleanup() {
      info("Cleaning up");
      await serverClosed;
      BrowserTestUtils.removeTab(tab);

      if (appLocales) {
        const appLocaleChanged = waitForAppLocaleChanged();

        Services.locale.availableLocales = availableLocales;
        Services.locale.requestedLocales = requestedLocales;

        await appLocaleChanged;

        await SpecialPowers.popPrefEnv();
      }

      if (systemLocales) {
        TranslationsParent.mockedSystemLocales = null;
      }

      if (webLanguages) {
        await SpecialPowers.popPrefEnv();
      }

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
  info("EVAL_RESULT " + JSON.stringify(data));

  dump("-------------------------------------\n");
  dump("Eval result:\n");
  dump(JSON.stringify(data, null, 2));
  dump("\n");
}
