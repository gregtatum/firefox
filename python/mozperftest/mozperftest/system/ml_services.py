# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import os

from mozperftest.layers import Layer
from mozperftest.metadata import Metadata


class MLServices(Layer):
    """
    Define environment variable values to allow ML Services to be accessed by tests.
    """

    name = "ml-services"
    activated = True

    allowed_hosts = [
        # Remote Rettings contains the machine learning model descriptions, translations
        # models, and various prompts.
        "firefox.settings.services.mozilla.com",
        "firefox-settings-attachments.cdn.mozilla.net",
        "content-signature-2.cdn.mozilla.net",
        # The models hub is a HuggingFace compatible model hub for downloading models.
        "model-hub.mozilla.org",
        # The MLPA server is the front for the mozilla-managed LLM service.
        "mlpa-nonprod-stage-mozilla.global.ssl.fastly.net",
    ]

    prefs = {
        "services.settings.server": "https://firefox.settings.services.mozilla.com/v1"
    }

    def setup(self):
        # Allow for using staging servers in remote settings.
        os.environ["MOZ_REMOTE_SETTINGS_DEVTOOLS"] = "1"

    def run(self, metadata: Metadata):
        metadata.get_options("browser_prefs").update(self.prefs)
        os.environ["MOZ_NONLOCAL_ALLOWLIST"] = ",".join(MLServices.allowed_hosts)
        for host in MLServices.allowed_hosts:
            self.info(f"[ml-services] allowed host: {host}")

        return metadata
