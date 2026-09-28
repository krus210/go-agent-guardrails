# Go guardrails for AI coding agents

Runnable examples of automated checks that turn review comments into commands an AI coding agent can run on its own. The agent reads a diagnostic, fixes the code and reruns the same command.

The module models a small reporting service. Each area has a passing example, a deliberate violation, and a control that proves a broken tool run is not counted as a detected violation.

| Area | Check | Violation it catches |
|---|---|---|
| Naming | `revive` (`var-naming`) | `UserId` instead of `UserID` |
| Complexity | `gocognit` | Nested conditions above the threshold |
| Resources | `bodyclose`, `go vet` (`lostcancel`) | Unclosed HTTP response body, discarded `cancel` |
| Concurrency | `testing/synctest`, `goleak`, `-race` | Sender that ignores cancellation, leaked goroutine, channel deadlock, data race |
| Architecture | `depguard`, `go/packages` test | Calculation code that reaches storage directly or through a helper package |
| Test quality | whole-value assertions, `exhaustruct_v5`, mutation testing | Tests that pass although a field or a boundary is wrong |

## Requirements

The examples were verified with:

- Go 1.27.1 on macOS arm64;
- golangci-lint 2.13.2 built with Go 1.27.1 (it bundles exhaustruct v5.0.3 as `exhaustruct_v5`);
- the `avito-tech/go-mutesting` fork at `v0.0.0-20251226130216-48d0401f00fb`;
- Python 3.10 or newer for the verification scripts.

The pinned versions are listed in `tool-versions.json`. They are pinned for reproducibility, not as a recommendation. `-race` needs a supported platform and cgo; outside Darwin it also needs a C compiler. See the [race detector requirements](https://go.dev/doc/articles/race_detector).

## Setup

Run all commands below from the repository root in the same fish session. Set the toolchain for the whole session before installing tools or running checks:

```fish
set -gx GOTOOLCHAIN go1.27.1
go version
go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.13.2
go install github.com/avito-tech/go-mutesting/cmd/go-mutesting@v0.0.0-20251226130216-48d0401f00fb
go mod download
```

`GOTOOLCHAIN` now applies to subsequent commands in this session, even if another `go` comes first in `PATH`; Go may download the toolchain on first use. The `go 1.27.0` line in `go.mod` sets a minimum version, not the exact 1.27.1 used here. See [Go toolchains](https://go.dev/doc/toolchain). Make sure the Go binary directory is in `PATH` and `golangci-lint version` reports 2.13.2 built with Go 1.27.1.

## Quick run

```sh
go test -race -count=1 ./...
golangci-lint run ./...
python3 -m unittest discover -p 'test_checks.py'
python3 verify.py
python3 verify_mutation.py
```

The first two commands must pass cleanly. The scripts set `GOTOOLCHAIN=go1.27.1` when it is unset, stop on an incompatible value, and check the installed tool versions.

Negative examples live under `testdata`, which `./...` skips, so they are run explicitly.

## Examples

### Naming

```sh
golangci-lint run --config naming.yml ./testdata/naming
```

`naming.yml` enables only `revive` with the `var-naming` rule, so no other findings are mixed in. The command exits with code 1:

```text
testdata/naming/naming.go:5:2: var-naming: struct field UserId should be UserID (revive)
```

### Complexity

```sh
golangci-lint run --config complexity.yml ./testdata/complexity
```

```go
func CanExport(active, allowed, ready bool) bool {
	if active {
		if allowed {
			if ready {
				return true
			}
		}
	}
	return false
}
```

Each nesting level adds more to the cognitive complexity: 1 + 2 + 3 = 6. The threshold in `complexity.yml` is 3 only to keep the example short; pick a project threshold after looking at existing code.

```text
testdata/complexity/complexity.go:3:1: cognitive complexity 6 of func `CanExport` is high (> 3) (gocognit)
```

Configuration files can be validated offline: since golangci-lint 2.12 the JSON schema is embedded in the binary and the `--schema` flag is gone. A typo such as `min-complexty` fails this step even though a plain `run` may ignore it:

```sh
golangci-lint config verify --config complexity.yml
```

### Errors and resources

`testdata/bodyclose` reads a response body without closing it; `bodyclose` reports `response body must be closed`. The fixed version in `internal/download` closes the body and returns both the read error and the close error:

```go
func Download(url string) (data []byte, err error) {
	response, err := http.Get(url)
	if err != nil {
		return nil, err
	}
	defer func() {
		err = errors.Join(err, response.Body.Close())
	}()
	return io.ReadAll(response.Body)
}
```

[`TestDownloadReadAndClose`](internal/download/download_test.go) covers successful reading and closing, a read error, a close error, and both errors together. It checks that `Close` is called exactly once, returned data is preserved (including partial data on a read error), and each expected error can be found with `errors.Is`.

```fish
go test -race -count=1 -run '^TestDownloadReadAndClose$' ./internal/download
```

The test uses a fake `http.RoundTripper`, so it performs no network requests. Subtests run sequentially because they replace `http.DefaultClient`; cleanup restores the original client after each case. This test is included in `go test ./...` and the normal test step of `verify.py`.

The example covers ownership of the response body only. Timeouts, context propagation and status handling are out of scope.

`testdata/lostcancel` discards the `cancel` function returned by `context.WithTimeout`:

```sh
go vet ./testdata/lostcancel
```

### Concurrency

`Forward` in `internal/report` sends one value or stops when the context is cancelled. It closes the returned `done` channel when it finishes; the caller owns `out`.

```go
func Forward(ctx context.Context, out chan<- int, value int) <-chan struct{} {
	done := make(chan struct{})
	go func() {
		defer close(done)
		select {
		case out <- value:
		case <-ctx.Done():
		}
	}()
	return done
}
```

`TestForwardCancellation` uses [`testing/synctest`](https://pkg.go.dev/testing/synctest). It starts the sender without a receiver, waits until the sender is durably blocked, checks that `done` is still open, then cancels and waits for `done`. If the `ctx.Done()` case is removed, the test fails with a deadlock report:

```text
--- FAIL: TestForwardCancellation (0.00s)
panic: deadlock: all goroutines in bubble are blocked [recovered, repanicked]
```

The stack shows the test waiting for `done` and the sender blocked on `chan send`. The test checks the sender's reaction to an explicit cancel. It does not check that a real caller cancels when its receiver exits early.

`TestForwardDelivery` checks that 42 is delivered and that the sender completes; it uses [goleak](https://github.com/uber-go/goleak). Inside a synctest bubble, synctest itself reports goroutines that never finish.

Standalone negative examples:

```sh
go test -count=1 ./testdata/leak       # goleak: unexpected goroutines
go test -count=1 -run '^TestDeadlock$' ./testdata/deadlock # synctest: channel deadlock
go test -race -count=1 ./testdata/race # WARNING: DATA RACE
```

synctest detects only durable blocking on bubble-owned channels and similar primitives. Waiting on `sync.Mutex`, I/O or system calls is not durable, so such hangs end in a test timeout, and a timeout alone does not prove a deadlock.

[`TestMutexDeadlock`](testdata/deadlock/mutex_deadlock_test.go) deliberately calls `Lock` twice on the same mutex. The second call blocks, so the deferred `Unlock` cannot run. Run this example separately with a short timeout:

```fish
go test -count=1 -timeout=2s -run '^TestMutexDeadlock$' ./testdata/deadlock
```

The command is expected to fail with `panic: test timed out after 2s`. Inspect the goroutine stack to locate the blocked second `Lock`; the timeout itself only establishes that the test did not finish. `verify.py` uses this fixture to check that a mutex timeout is rejected as evidence of a synctest deadlock.

### Architecture

The rule: packages under `internal/reportcalc` must not depend on `internal/storage` or its subpackages, directly or transitively. Test files are excluded.

`depguard.yml` covers direct imports. Two deny entries separate the cases:

| Import from `internal/reportcalc` | Result |
|---|---|
| `internal/storage` | Denied: the entry ending in `$` is an exact match |
| `internal/storage/reader` | Denied: the entry ending in `/` covers subpackages |
| `internal/storagecache` | Allowed: a different package with the same prefix |

`depguard` does not follow imports, so it misses a path through a helper such as `internal/shared`, which imports storage. `TestArchitecture` in `architecture_test.go` loads the production package graph with [`golang.org/x/tools/go/packages`](https://pkg.go.dev/golang.org/x/tools/go/packages) and reports the whole chain:

```text
ARCH001: calculation depends on storage: example.com/guardrails/internal/reportcalc -> example.com/guardrails/internal/shared -> example.com/guardrails/internal/storage
```

The test also fails when the graph cannot be loaded, when the target package is missing, or when no calculation package matches the rule. This keeps a renamed package from turning the check into a silent pass.

`go test -tags` is not passed to `packages.Load` automatically. Use `GOFLAGS` for build tags. The file `internal/reportcalc/tagged.go` adds a direct violation under a tag:

```sh
GOFLAGS='-tags=guardrails_violation' go test -count=1 -run '^TestArchitecture$' .
```

The test sees only Go imports. It does not detect runtime coupling, for example a network call to a storage service.

### Test quality

`ToView` must copy `ID` and `Region`. An assertion on `got.ID` alone passes when `Region` is lost; `TestToView` compares the whole value instead. When a new field such as `Currency` is added and neither the implementation nor the expected value is updated, the whole-value comparison still passes because both sides hold the zero value. `exhaustruct.yml` then reports every `View{...}` literal that omits the field, in the implementation and in the test:

```text
report.View is missing field Currency
```

`exhaustruct.yml` enables `exhaustruct_v5` with `explicit-mode: true` and an `enforce-patterns` entry for `View`; without explicit mode v5 checks every composite literal. The old `exhaustruct` (v4) linter is deprecated since golangci-lint 2.13, and its `include` setting has no direct v5 equivalent. The check covers only composite literals of the selected type. A wrong type pattern is still valid configuration, so the rule is verified against a known violation.

## Mutation testing

`Allowed` in `internal/policy` permits sizes up to 10 inclusive. `verify_mutation.py` builds a separate module, runs go-mutesting with the custom executor in `mutation_executor.py`, and compares every mutant with the expected set:

| Mutant | Case that detects it |
|---|---|
| `size < 10` | `Allowed(10)` expects `true` |
| `size <= 9` | `Allowed(10)` expects `true` |
| `size <= 11` | `Allowed(11)` expects `false` |

A test that checks only `Allowed(9)` lets all three survive. The cases 9, 10 and 11 kill all three.

The executor classifies each mutant as:

- `killed`: the target test ran and failed on the expected assertion about `Allowed`;
- `survived`: the target test and package passed;
- `invalid`: build failure, timeout, unrelated failure, missing target test, or unreadable output.

The built-in executor of this go-mutesting revision can count a build failure or a timeout as a detected mutant, and go-mutesting can exit with code 0 while mutants survive. The score alone is therefore not trusted. The executor runs only the package of the mutated file; tests in consumer packages need a separate run.

A killed mutant shows that the test is sensitive to a change. It does not prove that the expected value itself matches the requirement.

## How verification works

`verify.py` runs 50 steps in temporary copies of the module; the working tree is never modified. Positive examples must pass or produce no linter findings. Negative examples must exit with code 1 and produce structured evidence: linter name, position and text in golangci-lint JSON output, or the expected test and diagnostic in `go test -json` output.

Build errors, `typecheck` errors, configuration errors, missing Go files and timeouts never count as a detected violation. The script creates each of these outcomes on purpose and checks that the validator rejects it. For example, `PASS reject mutex timeout as bubble deadlock` means that a timeout was correctly not counted as a deadlock. Another 14 Python tests cover the validators, report saving and output normalization.

| Area | Cases |
|---|---|
| Baseline | Tests with the race detector, base linters, schemas of all five configurations |
| Naming and complexity | `UserId`; complexity 6 above threshold 3; the `min-complexty` typo |
| Errors and resources | Unclosed response body; lost cancel; a type error is not counted as a finding |
| Concurrency | goleak, synctest and race examples; build failure and timeout are not counted |
| `Forward` | Five broken cancellation variants; wrong delivered value; correct delivery |
| Results and tests | Weak and boundary expectations; lost `Region`; zero-valued new field; incomplete literal; wrong exhaustruct scope |
| Architecture | `storage`, `storage/reader`, the allowed `storagecache`, a transitive import, a nested calculation package, an excluded test file |
| Graph scope | Tag via `GOFLAGS`; missing target; empty calculation scope; type loading error |

## Reports

Every run writes a timestamped JSON report to `reports/`, including failed runs. Only a complete successful run atomically replaces `verification.json` or `mutation-verification.json`, so a failed run never erases the last good report. Reports use `schema_version: 2`: environment metadata, step results and, for mutations, the source, status and test output of each mutant. Machine paths and memory addresses are replaced with markers; timings depend on the machine.
