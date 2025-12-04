/* global ChromeUtils, arguments */

// Return the moz-extension base URL for the installed extension id passed in.
const { ExtensionParent } = ChromeUtils.importESModule(
  "resource://gre/modules/ExtensionParent.sys.mjs"
);
const ext = ExtensionParent.GlobalManager.getExtension(arguments[0]);
return ext ? ext.baseURL : null;
