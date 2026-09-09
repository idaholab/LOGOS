# PRISM GUI — Priority-Rule Reference (the 22 rules)

Plain-language descriptions of the 22 priority rules the RCPSP scheduler exposes, for the RunConfig
rule picker (tooltip / help text) **and** as an implementer reference. Every description is grounded
in the actual engine code — `src/CPM/pert.py` and `src/CPM/cpm_utils.py` — not invented. Companion to
the model spec (§4 RunConfig) and the adapter spec (§4, the 22 keys passed to the run method).

The 22 keys (verbatim from `_list_priority_names`, [pert.py:70](../../CPM/pert.py#L70)):
`lf, ls, ef, es, duration, random, mts, mtp, grpw, grd, rr, avgrr, maxrr, minrr, mehh_8000_b,
mehh_3375_b, mehh_1000_b, mehh_125_b, gphh_b, wcs, acs, irsm`.

---

## How a rule becomes a schedule

A priority rule does **not** place activities directly. At each scheduling decision the serial/parallel
SGS has a set of **eligible** activities (predecessors done, resources checkable) and must pick an order.
The rule assigns each eligible activity a value; the activities are then sorted, and
[pert.py:6422](../../CPM/pert.py#L6422) turns rank into the final priority `1/(1+i)` — so **whatever
sorts first gets scheduled first.** All 22 keys are dispatched from one place,
`priority_calculation(eligible, priority_rule, current_time)`
([pert.py:6304](../../CPM/pert.py#L6304)); the name is lower-cased
([pert.py:6343](../../CPM/pert.py#L6343)) and routed through an if/elif chain. An unrecognized rule
raises `IOError("Invalid priority rule")` ([pert.py:6415](../../CPM/pert.py#L6415)) — there is **no
silent default**, so the GUI must only ever send one of the 22 keys (it drives them from this list).

**Sort direction is not uniform — read it per rule.** There are two conventions plus the inline group:

- **Lower value first (ascending).** `lf, ls, ef, es, duration`, the five evolved rules
  (`mehh_*`, `gphh_b`), and `wcs, acs, irsm`. For the timing rules this is the classic "most urgent
  first"; for the evolved rules it is simply how they were fit (see the caveat below).
- **Higher value first (descending).** `mts, mtp, grpw, grd, rr, avgrr, maxrr, minrr` — "more of this
  quantity ⇒ schedule sooner."
- **No value.** `random` shuffles.

Ties are broken by the `mehh_8000_b` score for the timing/structure/resource rules, and by `lf` for the
slack rules. This matters for reproducibility: a given seed + rule is deterministic, but two rules that
agree on the primary key can still order differently.

> **These are heuristics, not optimizers.** Each rule is a fast, greedy ordering policy; none guarantees
> the shortest makespan. Comparing several rules over the same plan (the priority-rule *sweep*) is the
> intended way to find a good schedule — which is exactly why the fresh-runtime invariant (architecture)
> matters: each rule must run against an uncontaminated runtime.

---

## Timing rules (CPM-based) — *lower value scheduled first*

These read the activity's CPM forward/backward-pass times ([pert.py:6344-6347](../../CPM/pert.py#L6344)).

| Key | Name | Plain description | Reads |
|---|---|---|---|
| `lf` | Latest Finish | Schedule the activity whose **latest allowable finish** is soonest — the one with the least room before it starts delaying the project. The engine's default rule. | `infoDict[a]['lf']` (backward pass) |
| `ls` | Latest Start | Schedule the activity whose **latest allowable start** is soonest. Close kin of `lf`. | `infoDict[a]['ls']` |
| `ef` | Earliest Finish | Schedule the activity that *could* finish earliest — favors short/early work, clearing the board. | `infoDict[a]['ef']` (forward pass) |
| `es` | Earliest Start | Schedule the activity that could start earliest. | `infoDict[a]['es']` |
| `duration` | Shortest Duration | Shortest activity first (classic shortest-processing-time). Tends to maximize the number of activities completed early; can starve long critical work. | `infoDict[a]['duration']` |

## Structure rules (network shape) — *higher value scheduled first*

| Key | Name | Plain description | Reads / caveat |
|---|---|---|---|
| `mts` | Most Total Successors | Prefer the activity that unblocks the most downstream work. **Approximate:** a path-count that over-counts shared descendants ([pert.py:812](../../CPM/pert.py#L812), comment [829-831](../../CPM/pert.py#L829)) — a ranking signal, not an exact unique-successor count. |
| `mtp` | Most Total Predecessors | Prefer the activity with the most upstream work behind it (deep in the network). Same path-count approximation ([pert.py:845](../../CPM/pert.py#L845)). |
| `grpw` | Greatest Rank Positional Weight | Prefer high **positional weight** = the activity's own duration + the total duration of everything upstream of it ([pert.py:873](../../CPM/pert.py#L873), [897](../../CPM/pert.py#L897)). Biases toward long, deeply-preceded chains. Same over-count caveat. |

## Resource rules — *higher value scheduled first*

| Key | Name | Plain description | Reads / caveat |
|---|---|---|---|
| `grd` | Greatest Resource Demand | Prefer the most resource-hungry activity: `(Σ crew_count + Σ equipment quantity + #zones) × duration` ([pert.py:903](../../CPM/pert.py#L903), [920-924](../../CPM/pert.py#L920)). Places big consumers while capacity is free. |
| `rr` | Resource Requirement (breadth) | Prefer the activity that touches the **largest fraction of resource types** — `count of resource types used ÷ total resource types` ([pert.py:927](../../CPM/pert.py#L927), [975](../../CPM/pert.py#L975)). Note: breadth (how many kinds), *not* raw quantity. |
| `avgrr` | Average Resource Requirement | Prefer the highest **mean** normalized demand across resource types (each = demand ÷ that resource's max availability) ([pert.py:976](../../CPM/pert.py#L976)). |
| `maxrr` | Max Resource Requirement | Prefer the highest **peak** normalized demand on any single resource type — targets the activity most likely to be the bottleneck ([pert.py:977](../../CPM/pert.py#L977)). |
| `minrr` | Min Resource Requirement | Ranks by the **minimum** normalized demand across resource types. **Near-degenerate in practice:** the minimum is taken over *all* resource types including the many the activity doesn't use (seeded to 0.0, [pert.py:951](../../CPM/pert.py#L951)), so it is almost always 0.0 and rarely discriminates ([pert.py:978](../../CPM/pert.py#L978)). Offered for completeness; not a recommended default. |

## Stochastic

| Key | Name | Plain description |
|---|---|---|
| `random` | Random Order | Shuffles the eligible set ([pert.py:6348](../../CPM/pert.py#L6348)). Reproducible only with a fixed **seed** (RunConfig). Useful as a baseline and for multi-start sampling; never for a single "the answer" run. |

## Slack-based rules (Kolisch 1996) — *lower value scheduled first*

Computed inline, not cached; all use `_compute_pairwise_E(i, j, t_n)` — the earliest feasible start of
`i` if `j` is scheduled at the current time `t_n` ([pert.py:6253](../../CPM/pert.py#L6253)). Ties break on
`lf`.

| Key | Name | Plain description |
|---|---|---|
| `wcs` | Worst-Case Slack | Rank by the *worst* slack an activity would have against the single competitor that hurts it most: `LS_j − max_i E(j,i)` ([pert.py:6355](../../CPM/pert.py#L6355)). Lowest (most urgent under the worst case) first. |
| `acs` | Average-Case Slack | Like `wcs` but against the **average** competitor: `LS_j − mean_i E(j,i)` ([pert.py:6375](../../CPM/pert.py#L6375)). Lowest first. |
| `irsm` | Improved Resource Scheduling Method | Rank by the largest delay this activity would *inflict* on others: `max{0, max_i (E(i,j) − LS_i)}` ([pert.py:6394](../../CPM/pert.py#L6394)). Schedules the least-disruptive activity first (lowest value). "Improved," not "integrated." |

## Evolved / hyper-heuristic rules — *lower value scheduled first*

`mehh_8000_b, mehh_3375_b, mehh_1000_b, mehh_125_b, gphh_b` are **genetic-programming-evolved**
priority functions — fixed closed-form arithmetic expressions over 10 normalized activity features
(`ES, EF, LS, LF, total-predecessor-count, total-successor-count, RR, AvgRReq, MaxRReq, MinRReq`),
defined in [cpm_utils.py:71-284](../../CPM/cpm_utils.py#L71), registered in `CUSTOM_PRIORITY_FUNCS`
([cpm_utils.py:287](../../CPM/cpm_utils.py#L287)), and evaluated in `calculate_gp_rules`
([pert.py:981](../../CPM/pert.py#L981)). They are **not stubs** — each is a distinct evolved expression.

- **`mehh_*` = "multi-expression hyper-heuristic"; `gphh_b` = "genetic-programming hyper-heuristic."**
  The suffix numbers (`8000, 3375, 1000, 125`) are **opaque variant tags** (most likely a
  training-budget / generation marker); the code does not document their meaning, so the GUI must not
  claim a specific semantic for the number — describe them as "evolved rule, variant N."
- **They sort ascending** (lower evolved-score = higher scheduling priority) — counterintuitive but it
  is what the code does ([pert.py:6344](../../CPM/pert.py#L6344), the same branch as the timing rules).
  Do not present them as "higher score = better."
- `mehh_8000_b` doubles as the **universal tie-breaker** for the timing/structure/resource rules, so it
  is always computed even when it is not the selected rule.

**Suggested UI treatment:** group these under an "Advanced / evolved" heading with a short "learned
heuristic — try it in a rule sweep and compare the makespan" note, rather than a per-rule mechanistic
explanation the code cannot substantiate. Their value is empirical (often competitive in benchmarks),
which the sweep-and-compare workflow surfaces directly.

---

## Caveats an implementer must not paper over

These come straight from the code and its own comments; the GUI's help text must not overstate:

1. **`minrr` is near-degenerate** (≈ 0.0 for almost every activity) — do not describe it as a meaningful
   discriminator. ([pert.py:951](../../CPM/pert.py#L951), [978](../../CPM/pert.py#L978))
2. **`mts` / `mtp` / `grpw` are path-count approximations** that over-count shared ancestors/descendants
   (explicit code comments). Describe as approximate "how much downstream/upstream work," not exact counts.
3. **The five evolved rules sort ascending** — lower score first. Easy to get backwards.
4. **`rr` is breadth, not quantity** — the *fraction of resource types* used, not the amount. `grd` is the
   quantity-weighted one.
5. **Normalization is cosmetic** — `normalize_tuples` ([pert.py:6417](../../CPM/pert.py#L6417)) rescales
   returned values (and is skipped for `rr/avgrr/maxrr/minrr`), but because the final priority is
   rank-based (`1/(1+i)`), it does not change the *ordering* or the schedule. Don't attribute behavior to it.
6. **Unknown rule = hard error**, not a fallback — the GUI must send only the 22 enumerated keys.

## Which rule to default to

`lf` (Latest Finish) is the engine's own default and a sound general choice (classic minimum-slack
heuristic). The design's intended workflow is the **priority-rule sweep** — run several rules on the same
effective plan, compare makespan/disposition, and keep the best — so the picker should make "compare a
set" as easy as "pick one," and should not imply any single rule is universally optimal.
