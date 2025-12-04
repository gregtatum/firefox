/* global arguments */

// Inject the SingleFile library into the content page if needed and return page data.
// Expects:
//  - arguments[0]: string source of the SingleFile core library
//  - callback: last argument, Marionette async callback
const libSource =
  arguments[0] +
  "\n; if (typeof singlefile !== 'undefined') { window.singlefile = singlefile; }";
const callback = arguments[arguments.length - 1];

(async () => {
  try {
    const win = window.wrappedJSObject || window;

    if (!win.singlefile || !win.singlefile.getPageData) {
      const script = win.document.createElement("script");
      script.type = "text/javascript";
      script.textContent = libSource;
      win.document.documentElement.appendChild(script);
      script.remove();
    }

    if (!win.singlefile || !win.singlefile.getPageData) {
      throw new Error("SingleFile library not available on window");
    }

    const data = await win.singlefile.getPageData({});
    const keys = data && typeof data === "object" ? Object.keys(data) : [];
    callback({ ok: true, data, keys });
  } catch (error) {
    const globals = Object.keys(window).filter(key =>
      key.toLowerCase().includes("single")
    );
    callback({
      ok: false,
      error: error && error.stack ? error.stack : String(error),
      globals,
      name: error && error.name ? error.name : null,
      message: error && error.message ? error.message : null,
    });
  }
})();
