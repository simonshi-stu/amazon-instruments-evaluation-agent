"""Amazon instrument review evaluation and diagnostics agent.

This project evaluates simulation artifacts produced by a separate
user-simulation project. It currently provides:

- stage-1 strict validation of ``simulation_run.v1`` artifacts;
- batch-1 typed evaluation config, B-owned task manifest, independent local
  source hashing, strict A prepared identity/context verification,
  cohort/split/time checks, explicit A schema-2/prepared adapters and
  fingerprint-bound manual confirmation;
- batch-2 point scoring with explicit success-only/system/degraded/missing
  denominators and no interval/significance/explanation/export.

Scope honesty: no real model is called by the package, no data is downloaded,
and a declared ``real_model_declared`` source remains unverified. The recovered
A work snapshot is read-only reference; this repository does not import A or
fabricate its missing mirror.
"""

__version__ = "0.2.0"
