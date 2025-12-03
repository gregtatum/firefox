# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import json
import os
from pathlib import Path

import requests

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

            llm_judge = None
            token = os.environ.get("MOZ_FXA_BEARER_TOKEN")
            if token:
                llm_judge = self._judge_with_llm(src, trg, ref, token)

            scored = {
                "type": "translation",
                "bleu": bleu,
                "chrf": chrf,
                "src": src,
                "trg": trg,
                "ref": ref,
                "llm": llm_judge,
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
            if res.get("llm") and isinstance(res["llm"], dict):
                llm_score = res["llm"].get("score")
                if llm_score is not None:
                    suite_results.append(
                        {
                            "name": res.get("type", "eval"),
                            "subtest": f"entry-{idx}-llm",
                            "data": [{"file": "eval", "value": llm_score, "xaxis": 2}],
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

        token = os.environ.get("MOZ_FXA_BEARER_TOKEN")
        endpoint = "https://mlpa-nonprod-stage-mozilla.global.ssl.fastly.net/v1/chat/completions"
        if token:
            try:
                resp = requests.post(
                    endpoint,
                    headers={
                        "authorization": f"Bearer {token}",
                        "content-type": "application/json",
                        "service-type": "ai",
                    },
                    json={
                        "model": "vertex_ai/mistral-small-2503",
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a helpful assistant.",
                            },
                            {"role": "user", "content": "Say hello world."},
                        ],
                        "stream": False,
                    },
                    timeout=15,
                )
                snippet = resp.text[:200]
                # Escape braces because mozlog formatting uses str.format().
                safe_snippet = snippet.replace("{", "{{").replace("}", "}}")
                self.info(f"LLM call status={resp.status_code} body={safe_snippet}")
            except Exception as exc:
                self.info(f"LLM call failed: {exc}")

        return metadata

    def _judge_with_llm(self, src, hypothesis, reference, token):
        endpoint = "https://mlpa-nonprod-stage-mozilla.global.ssl.fastly.net/v1/chat/completions"
        prompt_src = f"Source: {src}\n" if src else ""
        user_prompt = (
            f"{prompt_src}Reference: {reference}\nHypothesis: {hypothesis}\n"
            'Return JSON with fields: score (0-100), verdict ("good"|"ok"|"bad"), explanation (short).'
        )
        try:
            resp = requests.post(
                endpoint,
                headers={
                    "authorization": f"Bearer {token}",
                    "content-type": "application/json",
                    "service-type": "ai",
                },
                json={
                    "model": "vertex_ai/mistral-small-2503",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a translation quality judge. Rate adequacy/fluency.",
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                },
                timeout=30,
            )
        except Exception as exc:
            self.info(f"LLM judge request failed: {exc}")
            return None

        if not resp.ok:
            body = resp.text[:200]
            safe_body = body.replace("{", "{{").replace("}", "}}")
            self.info(
                f"LLM judge bad status={resp.status_code} ct={resp.headers.get('content-type')} body={safe_body}"
            )
            return None

        try:
            payload = resp.json()
            message = payload.get("choices", [{}])[0].get("message", {})
            content = message.get("content", "")
            cleaned = content.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                cleaned = "\n".join(
                    line for line in lines if not line.strip().startswith("```")
                )
            parsed = json.loads(cleaned)
            return {
                "score": parsed.get("score"),
                "verdict": parsed.get("verdict"),
                "explanation": parsed.get("explanation"),
                "model": payload.get("model"),
            }
        except Exception as exc:
            safe_body = content[:200].replace("{", "{{").replace("}", "}}")
            self.info(f"LLM judge parse failed: {exc} body={safe_body}")
            return None
