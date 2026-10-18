# Amazon Musical Instruments Review Evaluation & Diagnostics Agent

[English](README.md) | [简体中文](README.zh-CN.md)

This is the **evaluation side (Project B)** of a two-part user-simulation project. Project A generates predicted ratings and reviews for Amazon Musical Instruments users. This project checks whether those predictions are actually good, where they fail, and what the evidence can honestly support.

It is not a review generator, and it is not a second Amazon shopping agent.

## 0. Credits and acknowledgements

This project is built on the shoulders of several separate efforts:

- **Project A — [LLM User Simulation Agent](https://github.com/simonshi-stu/llm-user-simulation-agent).** The generation side of the same research project. A produces simulated ratings and reviews; B evaluates them through a versioned result contract.
- **[AgentSocietyChallenge](https://github.com/tsinghua-fib-lab/AgentSocietyChallenge)** (FIB Lab, Tsinghua University, MIT License). Project A uses its PyPI package as the simulation framework. This evaluation repository does **not** vendor or import that framework. The upstream authors have not reviewed or endorsed this project.
- **Amazon Product Data (2014), Musical Instruments 5-core reviews**, maintained by Julian McAuley's group at UC San Diego. The dataset page is [here](https://cseweb.ucsd.edu/~jmcauley/datasets/amazon/links.html). We do not redistribute the raw reviews, targets, or private run files. Public availability does not by itself settle redistribution or commercial-use rights; please check the source and any applicable terms before using the data.
- **GitHub Pages** hosts the static synthetic demo. No dataset or model service is exposed there.

The people and organisations above provided data, framework code, or hosting. They did not write this evaluation design and do not endorse its conclusions.

## 1. What this project is for, and why it is needed

A simulated user can produce a review that sounds completely convincing and still be wrong. It may predict 5 stars when the real user gave 2, copy text from the reference review, fail halfway through, or claim that "memory helped" when the memory never entered the prompt.

Those are different failures, and a single accuracy number hides them.

This project exists to make evaluation independent from generation:

- **Measure what can be measured.** Rating error, ranking of misses, coverage, format failures, retries, fallbacks, prompt inclusion, and runtime cost.
- **Compare fairly.** Two methods are compared on the same tasks and the same users, not on whichever subset each method happened to finish.
- **Show the denominator.** A model that succeeds on 3 easy tasks out of 12 should not look better than one that succeeds on 9 harder tasks.
- **Keep explanations tied to evidence.** The long-term workflow lets a model explain tool results, but not invent numbers, citations, or causal claims.
- **Say "we cannot tell" when the evidence is thin.** Missing clusters, missing common tasks, or absent real runs should produce an honest limitation, not a confident chart.

The project is deliberately not built as a multi-agent spectacle. The first version is a **tool-augmented, fixed workflow**: Python calculates, an LLM may later explain, and a human approves. LangChain, LangGraph, and a vector database are not required for this stage. RAG and persistent memory matter more on the generation side, where they still need controlled comparison before anyone claims a benefit.

## 2. How it is used

A typical evaluation story looks like this:

```text
A produces a run artifact
        ↓
B validates the artifact and its declared source
        ↓
B scores predictions and separates success / degraded / failed / refused
        ↓
B compares methods only on common valid tasks
        ↓
B packages numbers, denominators, coverage, and limitations as evidence
        ↓
An explanation layer may describe the evidence, but cannot recompute or invent it
        ↓
A human reviews and approves the report
```

Concrete examples:

- **"Did the new prompt help?"** Run the old and new prompt on the same evaluation tasks. Compare MAE/RMSE, ±1-star accuracy, bias, failure rate, and coverage. A lower error on a smaller successful subset is not automatically an improvement.
- **"Did memory help?"** Check whether memory was actually included in the prompt, then compare paired runs with and without it. A mode label is not evidence of a memory effect.
- **"Is the agent ready for new users?"** Keep a user-level holdout that was never used for prompt tuning. Report old-user future behaviour and new-user generalisation separately.
- **"Why is the report saying this?"** Every claim should point to a specific metric, sample size, denominator, and limitation.

The online demo tells this story with three small synthetic scenarios:

- a fair comparison on common tasks;
- a "low error" method that looks good only because most failures disappeared from its denominator;
- a case where there are no common successful tasks, so the honest answer is "cannot compare".

## 3. Current progress

**Working today**

- A strict `simulation_run.v1` artifact validator with a standalone CLI. It checks shape, resource limits, status consistency, diagnostic invariants, and privacy-sensitive fields without echoing review text or paths.
- A public synthetic demo on GitHub Pages: [open the demo](https://simonshi-stu.github.io/amazon-instruments-evaluation-agent/).
- Deterministic Python demo scoring for MAE, RMSE, ±1-star accuracy, bias, coverage, and common-task comparison. The demo also refuses to compare when there are no common tasks.
- A concise Chinese interview/decision log covering method choices, prompt changes, hallucination checks, evaluation design, memory, and generalisation: [docs/INTERVIEW_NOTES.zh-CN.md](docs/INTERVIEW_NOTES.zh-CN.md).
- Verified in this round: **99 tests passed, 3 skipped**. The skipped cross-repository checks require the original newer Project A contract mirror, which is not present. The public Project A `main` source has been restored, but it is not a substitute for that missing mirror.

**Not finished yet**

- The production metric pipeline has not been wired to real run artifacts. The current scoring and comparison live in the synthetic demo.
- The explanation layer, claim-to-evidence validator, uncertainty intervals, and human-approved export are still later stages.
- No real LLM experiment, human style review, memory effect measurement, or new-user holdout result has been produced in this round.
- The demo's charts are evidence of the evaluation logic, not evidence of model quality. The page says this explicitly and makes no real-accuracy claim.

The short version: **the contract and a transparent synthetic demo are working; the full evaluation-and-diagnosis agent is not finished.**

## 4. How to use it

There is **no plugin and no backend service**. The public demo is a static site; the local tools are a Python CLI and a standard-library Python package.

### Use the online demo

Open: [https://simonshi-stu.github.io/amazon-instruments-evaluation-agent/](https://simonshi-stu.github.io/amazon-instruments-evaluation-agent/)

Choose a scenario, inspect the metrics, follow each claim to its evidence, and download the synthetic report if useful. The page is in Chinese. It never uploads files and never calls a model.

### Run the validator locally

Requires Python 3.10 or newer. The validator itself uses only the standard library.

```powershell
python -m amazon_eval_agent validate --check
python -m amazon_eval_agent validate --input "<path-to-run-artifact.json>" --json
```

Exit code `0` means the artifact is contract-valid. It does **not** mean the predictions are accurate. A valid artifact may still contain failed, refused, or degraded records, and the report shows those separately.

### Run the tests and the demo locally

```powershell
python -m pip install pytest==8.3.5
python -m pytest -q -rs

python -m amazon_eval_agent.demo --output _site
python -m http.server 8193 --bind 127.0.0.1 --directory _site
```

Then open `http://127.0.0.1:8193/` and stop the server with `Ctrl+C`. The demo builder copies only three whitelisted files plus the generated synthetic reports; it does not read the repository's private data directories.

### Deploy your own copy

The included workflow `.github/workflows/pages.yml` runs the tests, builds `_site`, and deploys it to GitHub Pages when `main` is updated. To use it in a fork:

1. Push the repository to GitHub.
2. Open **Settings → Pages** and choose **GitHub Actions** as the source.
3. Push to `main`, or run the **Synthetic evaluation demo** workflow manually.

No API key, database, or LLM endpoint is needed. That is intentional: the public demo proves the evaluation chain, not a paid model's performance.

Real reviews, target labels, private run artifacts, prompts, and experiment results stay outside the repository. Any real model call or data transfer needs separate approval and a defined budget.

## 5. Where to read more

- [Development plan and evaluation boundaries](docs/DEVELOPMENT_PLAN.md)
- [Dataset and licensing notes](docs/DATASETS.md)
- [Chinese interview and decision notes](docs/INTERVIEW_NOTES.zh-CN.md)
- [Package-level contract details](amazon_eval_agent/README.md)
- [Project A: the generation side](https://github.com/simonshi-stu/llm-user-simulation-agent)

If you only remember one thing: **the value of this project is not a prettier leaderboard; it is making a simulated-user agent's claims checkable, comparable, and honest about what it cannot prove.**
