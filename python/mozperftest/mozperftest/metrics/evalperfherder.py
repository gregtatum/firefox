# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
from mozperftest.metrics.perfherder import Perfherder


class EvalPerfherder(Perfherder):
    """Perfherder output for evalmetrics results."""

    name = "evalperfherder"
    activated = True

    def run(self, metadata):
        # Force a predictable suite name and disable alerts.
        self.set_arg("prefix", self.get_arg("prefix") or "eval")
        self.set_arg("metrics", [])
        self.set_arg("stats", False)
        # Run the base perfherder generation.
        return super().run(metadata)
