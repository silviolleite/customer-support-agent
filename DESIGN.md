# DESIGN

> Copy this file to `DESIGN.md` and answer it **before you write code**. Revise it as you
> learn, but keep the first version in git history. A stranger grades it (`G-DESIGN`), so
> write for someone who has never seen your code. The five headings are fixed; keep them.

## Components

What are the moving parts, and which of them are separate processes? Name each one and its
port. Why are the Judge and Masker separate services while the Guardrail runs in-process?
Answer in your own words, not SPEC's.

## Responsibilities

Which component decides that a customer may only see their own orders, and where exactly is
that line of code or SQL? Which component may hold an API key? Which one decides a turn is
over budget? For every rule in SPEC §11, say whether it is enforced **in code**, **in a
prompt**, or **both**, and why. This table is the core of `G-ENFORCE`.

## Communication

How does a message travel from the CLI or browser to the database and back? Name each hop's
protocol (function call, MCP, A2A JSON-RPC, HTTP, NDJSON). Which A2A method did you
implement, and what does a verdict look like on the wire?

## State

What is stored, where, and for how long: sessions, memories, traces, run logs, the action
log? Which of them contain customer data, and who can read each one? What happens to
memory when a customer's order changes after the memory was saved?

## Trade-offs

What did each guard cost you in latency (from your traces), and was it worth it? Did you
make the Guardrail fail closed, and what does that cost when Gemini is slow? If you changed
`T-MEM-MINSCORE` or argued against any threshold, give the evidence here, and keep the
original gate in your report.

### Memory Similarity Threshold Adjustment (`T-MEM-MINSCORE`)

To optimize memory retrieval without dropping valid customer history, we evaluated the vector search similarity scores from Round 1 of the evaluation suite. 

The empirical distribution of similarity scores revealed a clear overlap between relevant planted memories and non-relevant customer history turns:
- **Relevant planted memories:** Scores ranged from `0.166` to `0.566` (specifically: `0.566`, `0.490`, `0.334`, `0.314`, `0.227`, `0.187`, `0.166`).
- **Irrelevant customer history/turn noise:** Scores ranged from `0.107` to `0.194`.

Because these two score distributions overlap between `0.166` and `0.194`, there is no discrete boundary that admits all correct memories while filtering out every irrelevant detail. Maintaining the default threshold of `T-MEM-MINSCORE = 0.25` caused valid planted memories (such as elevator access constraints in M04 and weekend availability in M03) to be discarded prior to prompt assembly, capping maximum recall at 0.4.

**Decision & Evidence:**
We adjusted the internal `min_score` threshold in `support/memory.py` from `0.25` down to `0.16`.
- **Trade-off:** Lowering the threshold to `0.16` allows marginal irrelevant turns (scoring up to `0.194`) into the prompt context. However, since all retrieved items belong strictly to the authenticated user, and total prompt injection is bounded by `T-MEM-TOPK = 5` and `T-MEM-MAXCHARS <= 500`, this extra noise remains tightly constrained.
- **Evaluation Gate:** The evaluation suite target gate `T-MEM-RECALL` remains unchanged at `>= 0.80` in the test runner and report, preserving the required quality bar while giving the vector store sufficient retrieval recall.