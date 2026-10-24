# Stepwise batch05 composition

The 21-patch combination measured approximately neutral on the user's VM. This runner searches incrementally instead of treating isolated gains as additive.

- Baseline is pinned to `dbe600b7438662e9ade60b31fa0625259dcd0077`.
- First build two fresh PGO masters, compare for 50 pairs, and stop if the difference exceeds 0.2% in magnitude. Do not subtract a control difference from candidate gains.
- Start with candidate 23, the strongest 25-pair individual result. If it fails confirmation, retain master and try the next candidate.
- Propose one addition at a time, comparing directly against the current accepted combination.
- Screen for 25 pairs. For a positive screen, measure 100 new pairs, without pooling the screen into confirmation.
- Keep an addition only when its confirmation mean is at least +0.05% and its approximate geometric 95% interval is entirely positive.
- Delay the two Position layout changes and three prefetch experiments until the end because they may affect shared cache resources.
- Finally rebuild BOTH the selected combination and master, and compare them for 100 new pairs. Report that final result separately from selection results.

Default order: 23, 4, 9, 24, 8, 19, 21, 14, 11, 15, 12, 3, 7, 5, 13, 10, 1, 2, 20, 18, 17. This interleaves code paths initially; it is a heuristic, not an exhaustive search. Omitted candidates can become useful in another context, and a selected improvement is not guaranteed to reproduce across fresh builds or machines.

The script uses one managed clone, one current trial executable, one best executable and a master executable. Raw pairs, compiler configuration, binary hashes, decisions, logs and source patches are saved in a new `~/stockfish-b05-greedy-*` directory. It does not alter existing checkouts, binaries or GitHub branches. It stops on a build or signature failure rather than selecting a broken combination. It does not resume after interruption; a second launch starts a fresh experiment.

Run with no other benchmarks active:

```bash
python3 -u ~/bench_b05_greedy.py --runs 25 --confirm 100
```

A successful run writes `best.json`, `best.patch`, `decisions.json`, `measurements.json` and `final.json`. Send `best.json` and `final.json` back to prepare a branch from the chosen source edits. The final fresh-build check can fail even after incremental acceptances; such a result is not a confirmed gain. The control checks are useful diagnostics, not a complete estimate of compiler, PGO or VM uncertainty.

The runner has been syntax-checked, and its source composer reproduces the previously published 21-patch mega source exactly. No engine build or benchmark was run locally for this runner.
