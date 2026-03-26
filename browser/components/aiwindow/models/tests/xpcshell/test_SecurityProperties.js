/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

const { SecurityProperties } = ChromeUtils.importESModule(
  "moz-src:///browser/components/aiwindow/models/SecurityProperties.sys.mjs"
);

add_task(function test_securityProperties_flags_not_visible_before_commit() {
  const sp = new SecurityProperties();
  sp.setPrivateData();
  sp.setUntrustedInput();
  Assert.strictEqual(sp.privateData, false, "not visible before commit");
  Assert.strictEqual(sp.untrustedInput, false, "not visible before commit");
  sp.commit();
  Assert.strictEqual(sp.privateData, true, "private_data now set");
  Assert.strictEqual(sp.untrustedInput, true, "untrusted_input now set");
});

add_task(function test_securityProperties_sticky() {
  const sp = new SecurityProperties();
  sp.setUntrustedInput();
  sp.commit();
  sp.commit();
  Assert.strictEqual(sp.untrustedInput, true, "flag persists across commits");
});

add_task(function test_seenUrls_basic() {
  const sp = new SecurityProperties();
  Assert.equal(sp.seenUrls.size, 0, "starts empty");

  sp.addSeenUrls(["https://a.com", "https://b.com"]);
  Assert.equal(sp.seenUrls.size, 2, "two URLs added");
  Assert.ok(sp.seenUrls.has("https://a.com"), "contains first URL");
  Assert.ok(sp.seenUrls.has("https://b.com"), "contains second URL");
});

add_task(function test_seenUrls_deduplicates() {
  const sp = new SecurityProperties();
  sp.addSeenUrls(["https://a.com", "https://a.com", "https://b.com"]);
  Assert.equal(sp.seenUrls.size, 2, "duplicate URLs are not counted twice");
});

add_task(function test_seenUrls_caps_at_max() {
  const sp = new SecurityProperties();
  const urls = Array.from(
    { length: 600 },
    (_, i) => `https://example.com/${i}`
  );
  sp.addSeenUrls(urls);
  Assert.equal(
    sp.seenUrls.size,
    500,
    "seen URLs capped at MAX_SEEN_URLS (500)"
  );

  sp.addSeenUrls(["https://example.com/overflow"]);
  Assert.equal(
    sp.seenUrls.size,
    500,
    "adding more URLs past the cap is a no-op"
  );
  Assert.ok(
    !sp.seenUrls.has("https://example.com/overflow"),
    "overflow URL was not added"
  );
});
