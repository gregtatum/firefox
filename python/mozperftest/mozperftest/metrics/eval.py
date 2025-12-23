# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import importlib.util
import json
from pathlib import Path

from mozperftest.layers import Layer
from mozperftest.metadata import Metadata
from mozperftest.utils import install_package


def _load_evals_module(topsrcdir: str):
    spec = importlib.util.spec_from_file_location(
        "ml_eval_evals", Path(topsrcdir) / "toolkit/components/ml/eval/evals.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvalMetrics(Layer):
    name = "evalmetrics"
    activated = True
    arguments = {}

    def run(self, metadata: Metadata):
        evaluations: dict[str, dict] = (
            metadata.script.get("options", {}).get("default", {}).get("evaluations", {})
        )

        if not evaluations:
            raise RuntimeError("No evaluations were configured for this run.")

        self.mach_cmd.activate_virtualenv()

        evals_module = _load_evals_module(self.mach_cmd.topsrcdir)
        per_test_results: dict[str, list[dict]] = {}
        for eval_name, eval_args in evaluations.items():
            eval_cls = getattr(evals_module, eval_name, None)
            if eval_cls is None:
                raise RuntimeError(f"Missing eval class {eval_name} in evals.py")

            def log(message):
                self.info("[eval] {message}", message=message)

            eval_instance = eval_cls(log, **eval_args)

            for requirement in eval_instance.requirements:
                install_package(
                    self.mach_cmd.virtualenv_manager,
                    requirement,
                )

            eval_payloads = metadata.get_eval_payloads()

            if not eval_payloads:
                raise ValueError("No eval payloads were found")

            # Run the evals from toolkit/components/ml/eval.
            for test_name, payloads in metadata.get_eval_payloads():
                self.info(f"[eval] Running {eval_name} on {test_name}")
                result = eval_instance.run(test_name, payloads)
                if test_name not in per_test_results:
                    per_test_results[test_name] = []
                per_test_results[test_name].append(result)

        for test_name, results in per_test_results.items():
            metadata.add_result(
                {
                    "name": test_name,
                    "framework": {"name": "mozperftest"},
                    "results": results,
                }
            )

        return metadata
