# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import importlib.util
import json
import os
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


def _normalize_eval_result(result: dict) -> dict:
    values = [
        item["value"] for item in result.get("data", []) if "value" in item
    ]
    if not values and "value" in result and result["value"] is not None:
        values = [result["value"]]

    metric = {"name": result.get("name"), "subtest": result.get("subtest")}
    metric["values"] = values
    for key in ("unit", "lowerIsBetter", "shouldAlert", "alertThreshold", "value"):
        if key in result and result[key] is not None:
            metric[key] = result[key]
    return metric


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
        per_metric_results: dict[str, list[dict]] = {}
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
                result = _normalize_eval_result(
                    eval_instance.run(test_name, payloads)
                )
                metric_name = result.get("name")
                if not metric_name:
                    raise RuntimeError("Eval metric result is missing a name")
                per_metric_results.setdefault(metric_name, []).append(result)

        output_dir = Path(self.get_arg("output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        for metric_name, results in per_metric_results.items():
            rows = []
            combined_values = []
            for result in results:
                test_name = result.get("subtest")
                values = result.get("values", [])
                if not test_name:
                    raise RuntimeError(
                        f"Eval metric result for {metric_name} missing subtest name"
                    )
                combined_values.extend(values)
                for value in values:
                    rows.append({test_name: value})

            file_name = f"eval-{metric_name.replace(os.sep, '_')}.json"
            eval_path = output_dir / file_name
            eval_path.write_text(json.dumps(rows, indent=2))

            suite_settings = results[0]
            summary_value = (
                sum(combined_values) / len(combined_values)
                if combined_values
                else None
            )
            suite_result = {
                "name": metric_name,
                "framework": {"name": "mozperftest"},
                "results": str(eval_path),
                "unit": suite_settings.get("unit"),
                "lowerIsBetter": suite_settings.get("lowerIsBetter"),
                "shouldAlert": suite_settings.get("shouldAlert"),
                "value": summary_value,
            }
            if suite_settings.get("alertThreshold") is not None:
                suite_result["alertThreshold"] = suite_settings.get("alertThreshold")
            metadata.add_result(suite_result)

        return metadata
