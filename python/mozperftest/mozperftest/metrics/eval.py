# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import json
import math
from collections import Counter
from pathlib import Path

from mozperftest.layers import Layer


def _bleu_score(candidate, references, max_order=4, smooth=False):
    cand_tokens = candidate.split()
    ref_tokens = [r.split() for r in references]

    def _ngram_counts(tokens, order):
        return Counter(
            [" ".join(tokens[i : i + order]) for i in range(len(tokens) - order + 1)]
        )

    precisions = []
    for order in range(1, max_order + 1):
        cand_counts = _ngram_counts(cand_tokens, order)
        max_ref_counts = Counter()
        for ref in ref_tokens:
            ref_counts = _ngram_counts(ref, order)
            for ngram, count in ref_counts.items():
                max_ref_counts[ngram] = max(max_ref_counts[ngram], count)

        overlap = {ng: min(count, max_ref_counts[ng]) for ng, count in cand_counts.items()}
        overlap_count = sum(overlap.values())
        cand_count = max(sum(cand_counts.values()), 1)
        if overlap_count == 0 and smooth:
            precisions.append(1.0 / cand_count)
        else:
            precisions.append(overlap_count / cand_count)

    # Brevity penalty
    ref_lengths = [len(r) for r in ref_tokens]
    cand_len = len(cand_tokens)
    ref_len = min(ref_lengths, key=lambda rl: (abs(rl - cand_len), rl))
    if cand_len == 0:
        bp = 0.0
    elif cand_len > ref_len:
        bp = 1.0
    else:
        bp = math.exp(1 - (ref_len / cand_len))

    if any(p == 0 for p in precisions):
        geo_mean = 0.0
    else:
        geo_mean = math.exp(sum(math.log(p) for p in precisions) / max_order)

    return bp * geo_mean


def _chrf_score(candidate, references, n=6, beta=2.0):
    def _char_ngrams(text, order):
        return Counter(
            [text[i : i + order] for i in range(len(text) - order + 1)]
        )

    cand = candidate
    ref_list = references
    total_prec, total_rec = 0.0, 0.0
    for order in range(1, n + 1):
        cand_counts = _char_ngrams(cand, order)
        cand_total = sum(cand_counts.values()) or 1

        # Use the best matching reference for this order
        best_prec, best_rec = 0.0, 0.0
        for ref in ref_list:
            ref_counts = _char_ngrams(ref, order)
            ref_total = sum(ref_counts.values()) or 1
            overlap = sum((cand_counts & ref_counts).values())
            prec = overlap / cand_total
            rec = overlap / ref_total
            if (prec + rec) > (best_prec + best_rec):
                best_prec, best_rec = prec, rec
        total_prec += best_prec
        total_rec += best_rec

    total_prec /= n
    total_rec /= n
    if total_prec == 0 and total_rec == 0:
        return 0.0
    return ((1 + beta * beta) * total_prec * total_rec) / (
        (beta * beta * total_prec) + total_rec
    )


class EvalMetrics(Layer):
    name = "evalmetrics"
    activated = True
    arguments = {}

    def run(self, metadata):
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

            bleu = _bleu_score(trg, [ref], smooth=True)
            chrf = _chrf_score(trg, [ref])
            scored = {
                "type": "translation",
                "bleu": bleu,
                "chrf": chrf,
                "src": src,
                "trg": trg,
                "ref": ref,
            }
            results.append(scored)

        output_dir = Path(self.get_arg("output")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / "eval-results-scored.json"
        out_file.write_text(json.dumps(results, indent=2))
        self.info(f"Wrote eval scores to {out_file}")

        # Store scored results in metadata for any downstream consumers.
        metadata.add_result(
            {
                "name": "evalmetrics",
                "framework": {"name": "mozperftest"},
                "transformer": "mozperftest.metrics.eval:EvalMetrics",
                "results": results,
            }
        )
        if metadata.get_output() is None:
            metadata.set_output(str(out_file))
        return metadata
