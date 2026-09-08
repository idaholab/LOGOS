# RCPSP Benchmark Protocol

## 1. Benchmark Objective

-   Verify correctness (feasible schedules)
-   Validate objective value (makespan vs reference)
-   Assess robustness across instances
-   Evaluate scalability and competitiveness

## 2. Benchmark Scope

### Layer A --- Correctness

-   PSPLIB: j30, j60

### Layer B --- Scaling

-   PSPLIB: j90, j120

### Layer C --- Generalization

-   UGent: RG30, RG300

------------------------------------------------------------------------

## 3. Phases

### Phase 1 --- Feasibility

-   Small subset of j30
-   Check precedence and resource constraints
-   Acceptance: 100% feasible

### Phase 2 --- Exactness

-   All j30 and j60
-   Compare with reference solutions

### Phase 3 --- Scaling

-   j60, j90, j120
-   Measure runtime and gaps

### Phase 4 --- Generalization

-   UGent datasets
-   Evaluate robustness

------------------------------------------------------------------------

## 4. Metrics

-   instance_id
-   dataset_family
-   n_activities
-   n_resources
-   solver_version
-   random_seed
-   time_limit_sec
-   feasible
-   makespan
-   reference_value
-   absolute_gap
-   relative_gap_percent
-   runtime_sec
-   termination_status

------------------------------------------------------------------------

## 5. Formulas

absolute_gap = makespan - reference_value

relative_gap_percent = 100 \* (makespan - reference_value) /
reference_value

------------------------------------------------------------------------

## 6. Deterministic vs Stochastic

Deterministic: - Single run per instance

Stochastic: - Multiple seeds - Report mean, best, std

------------------------------------------------------------------------

## 7. Time Limits (example)

-   j30: 10s
-   j60: 30s
-   j90: 60s
-   j120: 180s
-   RG300: 300s

------------------------------------------------------------------------

## 8. Feasibility Checks

-   Precedence constraints
-   Resource capacity constraints
-   All tasks scheduled exactly once

------------------------------------------------------------------------

## 9. Dataset Ladder

-   Tier 0: Smoke tests
-   Tier 1: j30 subset
-   Tier 2: j30 + j60 subset
-   Tier 3: full PSPLIB
-   Tier 4: UGent

------------------------------------------------------------------------

## 10. Directory Structure

    benchmark/
      datasets/
      references/
      configs/
      scripts/
      results/
      logs/

------------------------------------------------------------------------

## 11. Reproducibility

Record: - git commit - config - seeds - machine specs - dataset version

------------------------------------------------------------------------

## 12. Failure Types

-   PARSE_ERROR
-   CRASH
-   TIME_LIMIT
-   INFEASIBLE_SCHEDULE
-   RESOURCE_VIOLATION
-   PRECEDENCE_VIOLATION

------------------------------------------------------------------------

## 13. Acceptance Criteria

-   100% feasibility on j30
-   Stable performance on j60+
-   Graceful degradation on large instances

------------------------------------------------------------------------

## 14. First Benchmark Campaign

1.  j30 full set
2.  j60 full set
3.  j90/j120
4.  UGent datasets

------------------------------------------------------------------------

## 15. Outputs

-   Summary tables
-   Runtime plots
-   Gap distributions
-   Failure statistics

------------------------------------------------------------------------

## Generated on 2026-04-18T17:25:14.220032
