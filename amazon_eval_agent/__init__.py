"""Amazon instrument review evaluation and diagnostics agent (stage 1).

This project evaluates and diagnoses simulation results produced by a separate
user-simulation project. Stage 1 delivers a strict, versioned result-artifact
contract (``SimulationRunV1``) and a standalone validation CLI:

    py -m amazon_eval_agent validate --input <artifact.json>
    py -m amazon_eval_agent validate --check
    py -m amazon_eval_agent validate --dry-run

Scope honesty: stage 1 performs artifact shape/safety validation only. It
computes no accuracy metric, calls no model, and never turns a declared source
metadata field into evidence that a real model ran. Full evaluation
orchestration, metrics, LLM explanation and human-confirmed export are later
stages.
"""

__version__ = "0.1.0"
