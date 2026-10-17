# Pass 1: follow one research question

The learning goal is to explain how a model's decision changes application state
and determines what executes next.

## 1. Start with the state

Read `ResearchState` in `src/financial_research/state.py`.

At the beginning, only `question` and `max_tool_calls` have meaningful values.
The plan and report are absent; evidence, tool calls, and decisions are empty.
Each graph node receives the current snapshot and returns a dictionary containing
only the fields it changes. Because the graph is sequential, nodes explicitly
replace list fields with their previous values plus new entries. There are no
parallel list reducers to understand in this pass.

Pydantic validates external decisions and tool results. The action union uses the
`tool` field to distinguish a price request from a macro request, a news request,
or a finish action. An invented tool name or missing required input is rejected.

## 2. Run the demo and locate each node

```sh
.venv/bin/financial-research
```

Read the four nested node functions in `src/financial_research/workflow.py`:

| Node | Responsibility | State change |
| --- | --- | --- |
| `plan` | Ask the model for a research plan | `plan`, `events` |
| `decide` | Enforce budget, then ask for one action | `action`, `decisions`, `stop_reason`, `events` |
| `execute` | Dispatch a typed tool and normalize its result | `tool_calls`, `evidence`, `events` |
| `report` | Ask for a report and check citation IDs | `report`, `events` |

The conditional edge after `decide` routes to `execute` or `report`. That edge
does not decide which financial explanation is persuasive. It executes the next
step implied by the validated action and application limits.

`execute` appends a tool-call record even when the result contains an error.
The next model decision can see that failure. Successful observations receive
application-assigned IDs such as `E001`; repeated observations from the same source
with identical content do not create more evidence.

## 3. Inspect the artifacts

Open the `state.json` path printed by the CLI. Find:

1. The assumption defining “recently.”
2. The first action and its requested symbols.
3. The tool output containing the calculated silver return.
4. Evidence `E001`, with its fixture URI, observation period, and retrieval time.
5. The next decision made after that evidence was added.
6. The final `stop_reason` and report citations.

The prices and news are invented. The simple-return calculation is real Python:
`(end_price / start_price - 1) * 100`. This distinction lets us test calculations
and control flow without depending on an external data service.

The report labels observations separately from inferences. Its last inference
acknowledges that candidate explanations do not establish causation. Citation
validation checks references, not the truth or entailment of those statements.

## 4. Your first experiment

Before changing code, predict what this command will do:

```sh
.venv/bin/financial-research --max-tool-calls 1
```

Expected behavior: one price-tool call collects two evidence items, macro and news
tools never execute, the graph stops with `tool_budget_exhausted`, and the report
includes only collected observations plus an incomplete-research limitation.

Then try `--max-tool-calls 0`. The plan still runs, but no data tool does. The report
contains no factual claims. This checks that the writer does not fill an empty
evidence collection with the normal demo narrative.

For a small code change, swap the macro and news actions in
`DemoModel.choose_action`, run with `--max-tool-calls 2`, and predict which evidence
will be missing. Restore the original order afterward; a workflow test deliberately
documents that default order.

## 5. Understand the model boundary

`ResearchModel` has three methods: make a plan, choose the next action, and write
a report. The graph does not need to know which provider implements them.

`DemoModel` replays predetermined choices. `OpenAIModel` sends the updated research
state to an LLM. That model chooses a native function call from the typed catalog;
Python validates and executes it. The next request gets the normalized result in
state. An API call requesting a tool does not itself run Python.

The tests include a decision sequence that starts with an unavailable macro
series, observes the failure, and switches to news. That demonstrates the graph
does not encode a fixed financial-tool order, even though the demo replay does.

## 6. What to explain before pass 2

- Why can the LLM select a tool but cannot execute an arbitrary function?
- Why does a failed call still consume budget?
- Why must evidence IDs be assigned by application code?
- How does the next model decision learn what the previous tool returned?
- Why is a valid citation insufficient to prove a claim is supported?
- Which parts change when a real market-data API replaces a fixture?

Once those answers are clear, the next step is one real data adapter and its error
paths. The full quantitative experiment loop comes in its own later stage.
