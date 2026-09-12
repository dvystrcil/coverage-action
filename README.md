# coverage-action

Composite action: run a repo's test suite under coverage and gate the PR on
three things a single percentage cannot separate.

```yaml
- uses: dvystrcil/coverage-action@v1
  with:
    runtime: node          # or: python
```

## Setup note: this repo is private

Every other `*-action` repo in this account is public, so nothing needed
saying. This one is private, and a private action is **not resolvable from
another repo** by default — the consumer's run fails with:

```
Unable to resolve action `dvystrcil/coverage-action`, repository not found
```

which reads like a typo and is not one. Grant access once, on THIS repo:

```bash
gh api -X PUT repos/dvystrcil/coverage-action/actions/permissions/access \
  -f access_level=user
```

(Settings → Actions → General → Access, "Accessible from repositories owned
by dvystrcil".) Verified against dvystrcil/n8n-workflow#220: the job failed
with the message above, and passed unchanged once the setting was applied.

## What it checks, and why it is three things

**1. That the tests actually executed.**

A pytest-style module executed as `python3 file.py` imports cleanly, defines
its test functions, runs none of them, and exits 0. The check goes green
having asserted nothing. Coverage of such a run is not "low" in a way anyone
notices — it is a number nobody reads.

So `tests=0` fails with exit **2** and a message that does not mention
coverage, because "the harness ran nothing" and "the code is poorly covered"
are different findings with different fixes
([homelab#1184](https://github.com/dvystrcil/homelab/issues/1184) AC1: a
check that can be a no-op must report which it was).

**2. That coverage has not dropped below a committed floor.**

Floors live in `.coverage-baseline.json`, not in workflow YAML, so raising one
is a reviewable diff and lowering one cannot happen by accident:

```json
{ "floors": { "lines": 99.0, "branches": 85.0, "functions": 95.0 } }
```

The gate fails below the floor (exit **1**) and *reports without failing* when
a metric has risen a full point above it, so the baseline gets raised and the
gain cannot be lost silently. Same shape as homelab's `policy/*.yaml`
exemption lists: it may shrink, it may not grow by accident.

**3. Which metric moved.**

Lines, branches and functions are reported and gated separately. On
`dvystrcil/n8n-workflow` line coverage read **99.90%** while branch coverage
read **86.81%** — and every bug worth finding lived in that gap.

## What it deliberately does NOT do

**Mutation testing.** This matters enough to state plainly, because a green
coverage gate invites exactly the wrong conclusion.

Measured on `dvystrcil/n8n-workflow`, 2026-09-12, at 99.90% line coverage:

| | |
|---|---|
| mutants generated | 70 |
| killed | 65 |
| **survived** | **5** |

Every one of those five lines was covered. The survivors:

- a null-guard whose `&&` could become `||`, so a provenance label stopped
  gating anything and an alert would comment on an unrelated issue
- two staleness comparisons whose `>` and `<` could shift to `>=` and `<=`
  with nothing sitting on either boundary
- a malformed-input guard nothing exercised, sitting under a comment
  explaining why it must not be removed
- a `return false` that could become `return true` and would have fired an
  alert rule on **every paused workflow in the fleet**

Coverage cannot see any of that. It answers "did this line run", not "would
anything have noticed if it were wrong".

Mutation testing is the tool that answers the second question, and it is not
in this action because its cost is repo-shaped: 70 mutants against a 0.4s
suite took ~40 seconds, which is cheap; the same sweep against a repo with a
minutes-long suite is not. A mutation gate wants a designated file list rather
than a whole tree, which is a different input and a different action.

**Treat a passing coverage gate as evidence the harness ran, not as evidence
the code is correct.**

## Measure the baseline ON CI, not locally

The floors describe what CI will see, so CI has to be what measures them.

A locally-measured floor is a guess about another machine. On
`dvystrcil/helm-update-ai` the two disagreed by **1.5 points** — a local run
of the identical tree and the same 105 tests reported 55.11% / 43.25% where
CI reported 53.56% / 40.80%, so floors set from the local figure failed the
gate the moment it landed.

Ruled out there: environment variables (re-run locally with `GITHUB_ACTIONS`,
`CI` and `GITHUB_REPOSITORY` set — unchanged) and tree differences (16 files,
1162 statements, nothing untracked). The likely cause is **unpinned
dependencies**: that repo's `requirements.txt` specifies only minimums, so a
fresh CI resolve takes different fallback paths than a long-lived local venv.
`dvystrcil/llm-wiki`, measured the same way in the same session, matched CI
exactly — and has no `requirements.txt` at all.

So: land the workflow with provisional floors, read the real numbers off the
first run's `COVERAGE-GATE-COMPLETE` line, and commit those.

```
measured: --tests-run 105 --lines 53.56 --branches 40.80
```

Set each floor **~0.85 below** the CI figure. That is enough headroom for
ordinary churn, and close enough to stay under the 1-point ratchet slack so
the gate does not suggest raising the floor on every single PR — which is how
a notice gets ignored.

## Exit codes

| code | meaning |
|---|---|
| 0 | at or above every floor |
| 1 | below a floor — coverage regressed |
| 2 | could not measure — no tests ran, or no/invalid baseline |

2 is not a coverage verdict. It means the question was never asked. A missing
baseline lands here too: absent floors would otherwise default to zero and the
gate would pass on any coverage at all.

## Inputs

| input | default | notes |
|---|---|---|
| `runtime` | *required* | `node` or `python` |
| `test_command` | per-runtime default | must emit native coverage output |
| `baseline` | `.coverage-baseline.json` | committed floors |
| `working_directory` | `.` | |

Defaults are the dependency-free invocations:

- **node** — `node --experimental-test-coverage --test tests/*.js`. Native
  since Node 22; no package needed.
- **python** — `python3 -m coverage run -m unittest discover -s tests -p "test*.py"`.
  Python ships `trace` and `sys.monitoring` but no coverage report with
  thresholds, so `coverage.py` is a real dependency here.

## A parsing note

A metric the parser cannot find is reported **absent**, never `0`. Reporting
zero would turn "I could not read the report" into "your coverage is 0%",
which reads as a coverage failure and sends someone to write tests for a
problem that does not exist. `functions=-` on Python runs is this: coverage.py
has no function-level percentage.
