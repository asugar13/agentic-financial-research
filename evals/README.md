# Report quality baseline

This evaluates the current **report writer**, using seven fixed evidence cases.
It calls local Qwen directly through `OllamaModel.write_report()`. Planning, tool
selection, the research graph, Yahoo, and FRED are outside this evaluation.

Start with [`report_cases.json`](report_cases.json): questions and grading criteria
are under `cases`. The shared evidence contains summary snapshots from the live
Yahoo/FRED run retrieved on 2026-10-22, retaining its dates, units, and provenance.
Daily arrays are omitted, just as they are from normal model context. Failed-tool
scenarios are constructed test inputs. No API keys are included or required.

| Case | What a good report does |
| --- | --- |
| `price-facts` | Preserves AAPL endpoints and price change; distinguishes total return |
| `yield-units` | Uses percentage points or basis points and acknowledges revisions |
| `causation` | Describes both changes without claiming causation or correlation |
| `false-premise` | Corrects the claim that observed yields fell |
| `missing-macro` | Reports available prices and acknowledges the failed comparison |
| `no-evidence` | Makes no findings and explains retrieval failure |
| `date-coverage` | Uses actual observation dates and distinguishes dividend-adjusted returns |

## Generate a baseline

Run from the repository root with Ollama running and the model installed:

```sh
.venv/bin/python -m evals.report_baseline run
```

`--model` overrides `OLLAMA_MODEL`, whose fallback is `qwen2.5:7b`. Use
`--case price-facts` for one case, or `--repeats 2` to observe variation across
repeated requests. Default generation makes seven local report requests. It
uses the adapter's existing temperature, context limits, and timeout.

Each invocation creates `runs/evals/<run-id>/` containing:

- `results.json`: model ID, Git revision, case-file hash, exact input states,
  reports, elapsed seconds, automatic checks, errors, and manual checklists.
- `<case-id>-<repeat>.md`: readable reports whose citation IDs are valid.
- `summary.md`: an initial table with counts of passes, failures, pending reviews,
  and errors.

Completed results are saved after each request. A failed request is recorded and
the remaining cases continue. Generation exits nonzero on model errors or failed
automatic checks. A zero exit code does **not** mean semantic quality passed.

## Grade and compare

Automatic checks cover the report schema, citation IDs, and the absence of claims
when no evidence exists. They do not establish claim support or numerical accuracy.

For each report, read its saved evidence and set each `manual_checks[].passed` in
`results.json` to JSON `true` or `false`, with a short explanation in `notes`.
Grade the title, claims, and limitations. Judge the meaning rather than exact
wording; the case criteria specify acceptable rounding and equivalent units.
Leave an uncertain or unreviewed criterion as `null`.

```sh
.venv/bin/python -m evals.report_baseline summarize runs/evals/<run-id>/results.json
```

The summarizer prints updated counts without model calls. It does not modify the
original `summary.md`; redirect its output if you want to save a graded table.
A case passes only when every automatic and manual check passes. An ungraded
criterion keeps the case pending; any failed criterion makes it fail.

Keep these cases and the model fixed when comparing the baseline with a report
change. Repeat runs to expose variation. Record quality changes alongside runtime,
including good claims damaged by a change. These seven cases are a small development
set; add held-out cases before making broader quality claims.
