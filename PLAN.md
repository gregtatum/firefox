LLM instructions: Update this plan as tasks progress, replacing `[ ]` with `[x]` when finished. Add/adjust steps if scope changes. Keep comments minimal per repo guidance.

# Context
- Goal: build an end-to-end evaluation pipeline for ML features in Firefox. Traditional tests are deterministic; LLM/ML outputs are statistical, so we need eval runs that drive Firefox and analyze outputs.
- Existing fit: perf testing (mozperftest) already gathers statistical data. We can run Firefox, collect eval artifacts, and analyze with Python (BLEU/other scores). LLM-as-judge may require network (endpoint or VM).
- Harness choice: mochitest offers deep chrome access; Selenium/WebDriver is limited. mozperftest (python/mozperftest) supports layered harnesses; we can add a custom eval layer. WebDriver extensibility exists but full chrome access is a maintenance burden.
- mozperftest status (from sparky): evolving toward a harness-of-harnesses; development is incremental via requests. Unit tests are required; layer design keeps setup/metrics/test separated through metadata. Custom layers can define CLI options; multiple metric layers can run together.
- mozproxy recording steps: add site to testing/performance/pageload_sites.json; run `./mach perftest --flavor desktop-browser --verbose --proxy --hooks testing/performance/hooks_recording.py --proxy-perftest-page NAME testing/performance/perftest_record.js`; upload artifact from mozilla-central/artifacts to tooltool; add to manifest. Perf team can assist.

# Plan
- [x] Define evaluation requirements: start with EN→ES BLEU sample mochitest that emits JSON payload; defer privacy/artifact work for now.
- [x] Specify eval payload contract: mochitest logs `EVAL_RESULT` JSON results. Evaluations follow.
- [x] Prototype mozperftest eval layer to run minimal mochitest(s) and forward payload via metadata.
- [x] Add custom metrics layer to compute scores (BLEU/others) and optionally call LLM-as-judge behind a flag.
- [ ] Wire CLI ergonomics: flags for prompt file, endpoint, run-id, offline/no-network, artifact path, seed; document a sample `./mach perftest ...` command with layers enabled.
- [ ] Ensure deterministic inputs/artifacts; store JSON summaries under artifacts/.
- [ ] Add tests for layers (mock network/LLM); keep mochitest minimal (single happy path).
- [ ] Integrate recording/tooltool needs for any pageload dependencies.
- [ ] Socialize prototype with perf team for hooks/priority and iterate.
- [ ] Finalize flow, mark completed tasks with `[x]`, adjust scope as needed.

# Notes:

Patch to get conditioned profile artifacts automatically.
https://phabricator.services.mozilla.com/D257197
