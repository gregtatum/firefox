# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import json
from pathlib import Path

from mozperftest.layers import Layer
from mozperftest.utils import install_package


class EvalMetrics(Layer):
    name = "evalmetrics"
    activated = True
    arguments = {}

    def run(self, metadata):
        if not metadata.get_eval_results():
            return metadata

        # Ensure sacrebleu is available
        self.mach_cmd.activate_virtualenv()
        install_package(
            self.mach_cmd.virtualenv_manager,
            "sacrebleu==2.4.2",
        )

        try:
            import sacrebleu  # noqa
        except Exception as e:
            raise RuntimeError(f"Failed to import sacrebleu: {e}")

        results = []
        for entry in metadata.get_eval_results():
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "translation":
                continue

            src = entry.get("src", "")
            trg = entry.get("trg", "")
            ref = entry.get("ref", "")
            if not trg or not ref:
                continue

            bleu = sacrebleu.corpus_bleu([trg], [[ref]]).score
            chrf = sacrebleu.corpus_chrf([trg], [[ref]]).score
            scored = {
                "type": "translation",
                "bleu": bleu,
                "chrf": chrf,
                "src": src,
                "trg": trg,
                "ref": ref,
            }
            results.append(scored)

        if not results:
            return metadata

        output_dir = Path(self.get_arg("output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Raw scored results for debugging/consumption.
        out_file = output_dir / "eval-results-scored.json"
        out_file.write_text(json.dumps(results, indent=2))
        self.info(f"Wrote eval scores to {out_file}")
        if metadata.get_output() is None:
            metadata.set_output(str(out_file))

        # Store intermediate results for Perfherder compatibility.
        suite_results = []
        for idx, res in enumerate(results):
            suite_results.append(
                {
                    "name": res.get("type", "eval"),
                    "subtest": f"entry-{idx}",
                    "data": [
                        {"file": "eval", "value": res.get("bleu"), "xaxis": 0},
                        {"file": "eval", "value": res.get("chrf"), "xaxis": 1},
                    ],
                    "value": None,
                    "unit": None,
                    "shouldAlert": False,
                    "lowerIsBetter": None,
                }
            )

        metadata.add_result(
            {
                "name": "evalmetrics",
                "framework": {"name": "mozperftest"},
                "transformer": "mozperftest.metrics.eval:EvalMetrics",
                "results": suite_results,
            }
        )
        return metadata
