# Independent additions to the original three speed patches

This standalone Linux/Python 3.9+ runner tests each of 13 batch-04 source patches independently
on top of commit `5b89b777c6c8cb7db04e9e8ea3bfe7e757f97e5d`. The benchmark BASE is the user's
saved original three-patch executable, `~/stockfish-old-latest`, never master. Each TEST contains
exactly the original three plus one addition. No additions accumulate across tests.

Candidates: 02, 03, 04, 05, 06, 07, 08, 11, 12, 13, 16, 21, 22. Commits are pinned in the script.
Rank PEXT retains its original shift/mask fallback on targets without `USE_PEXT`.

Requirements: Linux CPU affinity, Python 3.9+, git, GNU make, g++, and wget/curl for network files.
Default compilation is GCC PGO with `ARCH=x86-64-avx512icl`, at most four build jobs.
The saved baseline must be the original three built for the same VM/compiler/target settings.
A matching bench signature is a guard, not proof that an arbitrary supplied binary has the right source.

```bash
wget -O ~/bench_additions.py https://raw.githubusercontent.com/rubin-vera/Stockfish/tools/addition-speedtest/tools/bench_additions.py
nohup python3 -u ~/bench_additions.py --runs 200 > ~/stockfish-additions.log 2>&1 &
tail -f ~/stockfish-additions.log
```

Stop other benchmarks before starting. Ctrl+C exits tail; the background runner continues.
The default is 200 pairs per candidate. Each pair runs the saved original three and the current
combination once. Four warmup pairs are excluded. Both signatures must be 1559611; single-thread
node totals must agree for every measured pair.

With two known physical cores, the engines run concurrently with affinity set before exec and
CPU assignments swapped each pair. Otherwise, or with `--sequential`, both run alternately on one
fixed logical CPU. Bench depth 13, hash 16 MiB, one thread. Ratios are incremental: +0.2% means
faster than the original three, not faster than master. Prior gains are not assumed additive.

Outputs:

- `~/stockfish-additions/summary.csv`: current successful summaries.
- `~/stockfish-additions/runs/<config-id>/`: frozen settings, source hashes, binary hashes, raw paired
  JSONL measurements, per-candidate build logs and failures. Summary confidence intervals are approximate
  within-run intervals; they do not account for build/runtime bias or selection over candidates.

Re-run the same command to resume. Changing the requested pair count extends or summarizes saved
pairs for the same configuration. Completed candidates are skipped. An interrupted candidate's binary
is reused when its hash matches. If rebuilding produces a different executable, prior pairs for that
candidate are archived and its measurements restart, so different binaries are not silently pooled.
Changing the script, baseline hash, source selection, compiler/flags, architecture or CPU settings
creates a separate result directory. Other benchmark programs are not covered by this runner's lock.

One managed checkout and NNUE cache are shared across all builds. Only the current TEST executable
is retained. The supplied BASE is read, never replaced. Available nets are copied from the previous
batch-04 checkout; otherwise Stockfish downloads them. At least 1 GiB free is required before a build.
It does not delete old test folders or backups.

Options:

```bash
# Skip the four additions already tried (TT key, rank PEXT, Finny layout, list counters):
python3 -u ~/bench_additions.py --runs 200 --only 2 4 5 8 11 12 13 16 22

# Smaller screen, different baseline location, or sequential comparisons:
python3 -u ~/bench_additions.py --runs 25
python3 -u ~/bench_additions.py --base ~/stockfish-three-rebuilt --runs 200
python3 -u ~/bench_additions.py --sequential --runs 200
```

Other options: `--workdir`, `--jobs`, `--arch`. Changing --only changes configuration identity, so
use the same selection when extending a run.

Validation of the runner includes unit/integration tests for candidate selection, independent source
composition, miniature Git clone/PGO-target build, baseline retention, resume, extension, and archiving
pairs when executable identity changes. All 13 real patches were checked to apply independently to
the exact three-patch baseline with only the expected source files changed. A real-engine end-to-end
smoke test checks source composition, PGO build, signature validation and paired measurements.
