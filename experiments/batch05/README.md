# Batch 05: 24 independent speed experiments

Repository: [rubin-vera/Stockfish](https://github.com/rubin-vera/Stockfish)

All 24 commits have **exactly one parent**, official master **`dbe600b7438662e9ade60b31fa0625259dcd0077`**
([Unroll NNUE feature updates](https://github.com/official-stockfish/Stockfish/commit/dbe600b7438662e9ade60b31fa0625259dcd0077)).
Official master was fetched before development and checked again after uploading all 24; it still pointed to this commit.
The `bench/b05-base` branch pins that baseline. None of the older experiment branches, original three-patch combination, or mega combinations is included.

These are intended non-functional changes. They are **unbuilt and untested**, as requested: no compile checks, bench signatures, correctness checks or timing measurements have been run for this batch. A source review is not a correctness guarantee. The master signature observed previously is 1559611; each candidate still needs its signature checked. No candidate has an established speed benefit.

## Candidate index

| # | Branch | Idea | Code area |
|---|---|---|---|
| 01 | [bench/b05-01-piece-count-bytes](https://github.com/rubin-vera/Stockfish/tree/bench/b05-01-piece-count-bytes) | Store board piece counts in bytes | board-storage |
| 02 | [bench/b05-02-castle-mask-bytes](https://github.com/rubin-vera/Stockfish/tree/bench/b05-02-castle-mask-bytes) | Store per-square castling masks in bytes | board-storage |
| 03 | [bench/b05-03-no-enemy-slider-blockers](https://github.com/rubin-vera/Stockfish/tree/bench/b05-03-no-enemy-slider-blockers) | Skip king blocker rays without enemy sliders | king-blockers |
| 04 | [bench/b05-04-inline-check-front](https://github.com/rubin-vera/Stockfish/tree/bench/b05-04-inline-check-front) | Inline the direct and discovered check front end | gives-check |
| 05 | [bench/b05-05-undo-normal-front](https://github.com/rubin-vera/Stockfish/tree/bench/b05-05-undo-normal-front) | Give ordinary move undo a direct path | undo |
| 06 | [bench/b05-06-empty-piece-splat](https://github.com/rubin-vera/Stockfish/tree/bench/b05-06-empty-piece-splat) | Skip empty non-pawn SIMD move emission | move-emission |
| 07 | [bench/b05-07-promotion-packed-write](https://github.com/rubin-vera/Stockfish/tree/bench/b05-07-promotion-packed-write) | Emit full promotion sets as one small copy | promotions |
| 08 | [bench/b05-08-move-membership-avx2](https://github.com/rubin-vera/Stockfish/tree/bench/b05-08-move-membership-avx2) | Search generated move membership sixteen moves at a time | move-membership |
| 09 | [bench/b05-09-lmr-divisor-lut](https://github.com/rubin-vera/Stockfish/tree/bench/b05-09-lmr-divisor-lut) | Tabulate the exact LMR divisor | search-lmr |
| 10 | [bench/b05-10-correction-local-indices](https://github.com/rubin-vera/Stockfish/tree/bench/b05-10-correction-local-indices) | Reuse continuation-correction piece and square indices | search-correction |
| 11 | [bench/b05-11-quiet-update-operands](https://github.com/rubin-vera/Stockfish/tree/bench/b05-11-quiet-update-operands) | Keep quiet-history operands across the continuation call | search-quiet-update |
| 12 | [bench/b05-12-shuffling-cheap-gates](https://github.com/rubin-vera/Stockfish/tree/bench/b05-12-shuffling-cheap-gates) | Check cheap shuffling gates before board capture state | search-shuffling |
| 13 | [bench/b05-13-ft-unsigned-low-product](https://github.com/rubin-vera/Stockfish/tree/bench/b05-13-ft-unsigned-low-product) | Use unsigned low products for AVX512 feature activation | nnue-transform |
| 14 | [bench/b05-14-nnz-empty-block](https://github.com/rubin-vera/Stockfish/tree/bench/b05-14-nnz-empty-block) | Skip NNZ compaction and stores for empty blocks | nnue-nnz |
| 15 | [bench/b05-15-fc2-even-odd-chains](https://github.com/rubin-vera/Stockfish/tree/bench/b05-15-fc2-even-odd-chains) | Split the final dot product into even and odd chains | nnue-fc2 |
| 16 | [bench/b05-16-fc1-zero-eight](https://github.com/rubin-vera/Stockfish/tree/bench/b05-16-fc1-zero-eight) | Skip empty eight-byte groups in the second layer | nnue-fc1 |
| 17 | [bench/b05-17-fc0-lookahead-prefetch](https://github.com/rubin-vera/Stockfish/tree/bench/b05-17-fc0-lookahead-prefetch) | Prefetch a future sparse first-layer weight column | nnue-fc0 |
| 18 | [bench/b05-18-network-bucket-prefetch](https://github.com/rubin-vera/Stockfish/tree/bench/b05-18-network-bucket-prefetch) | Prefetch the selected network before feature transformation | nnue-network-dispatch |
| 19 | [bench/b05-19-accumulator-fused-scan](https://github.com/rubin-vera/Stockfish/tree/bench/b05-19-accumulator-fused-scan) | Find both usable accumulator ancestors in one traversal | nnue-ancestor-scan |
| 20 | [bench/b05-20-next-dirties-prefetch](https://github.com/rubin-vera/Stockfish/tree/bench/b05-20-next-dirties-prefetch) | Prefetch the next accumulator dirty record for writing | nnue-stack-push |
| 21 | [bench/b05-21-threat-output-select](https://github.com/rubin-vera/Stockfish/tree/bench/b05-21-threat-output-select) | Select dirty-threat output lists before appending | nnue-threat-indices |
| 22 | [bench/b05-22-pp-single-partner](https://github.com/rubin-vera/Stockfish/tree/bench/b05-22-pp-single-partner) | Use scalar indexing for single-partner AVX512 pawn changes | nnue-pawn-pairs |
| 23 | [bench/b05-23-psq-empty-half](https://github.com/rubin-vera/Stockfish/tree/bench/b05-23-psq-empty-half) | Skip empty sides of cached PSQ index generation | nnue-psq-indices |
| 24 | [bench/b05-24-dirty-piece-compress](https://github.com/rubin-vera/Stockfish/tree/bench/b05-24-dirty-piece-compress) | Compact dirty-threat piece bytes directly | board-threat-emission |

The [machine-readable manifest](manifest.json) supplies immutable commit IDs, source paths, hypotheses, baseline ID, and individual patch URLs. Candidate notes live under `experiments/batch05/NN-candidate.json` on each candidate branch. Each note has a unique path so combining candidates does not overwrite another candidate's metadata.

## How this batch was planned for combinations

The edits target separate functions and pipeline stages rather than offering 24 competing implementations of one routine. All 24 start independently from the same master; no patch requires another patch. Independence of source edits does not imply independence of speed effects.

| Family | Candidates | Combination considerations |
|---|---|---|
| Position storage | 01, 02 | Both shrink the same object. Treat as interacting layout experiments; try separately before combining. Their adjacent declarations may need a trivial manual merge. |
| Board control paths | 03, 04, 05 | Different routines: blocker discovery, check detection, and undo. Can be layered without replacing the same algorithm. |
| Move generation and membership | 06, 07, 08 | Empty non-pawn emission, promotion emission, and list lookup are separate operations. |
| Search bookkeeping | 09, 10, 11, 12 | Separate helpers: LMR divisor, correction reads, quiet updates, and shuffling gates. No search constants, scores or move-ordering policies are intentionally changed. |
| NNUE arithmetic and traversal | 13, 14, 15, 16, 19 | Feature multiplication, NNZ recording, final dot product, second layer, and ancestor scan. Different stages, but instruction scheduling and inlining still couple them. |
| Feature-index production | 21, 22, 23, 24 | Threat lists, pawn pairs, PSQ lists, and board dirty emission. Different producers/consumers; preserve feature and move ordering. |
| Memory hints | 17, 18, 20 | Sparse weight lookahead, network dispatch, and next stack dirty record. Add these last, one at a time: their shared cache and bandwidth costs make them especially likely to interact. |

A reasonable composition sequence is: a confirmed board-control winner, then a confirmed search-bookkeeping winner, then a feature-index winner, then a neural arithmetic winner, then the storage and prefetch experiments. These are proposed comparison stages, not preselected winning patches. Test all individual candidates first; only use those that actually help on the target VM.

Compare every added patch directly against the current best combination, using the same pinned source baseline, compiler, architecture and PGO settings. Keep the addition only if it improves the combination. A final comparison against the pinned master measures the total gain. More speed-test pairs on one pair of binaries do not measure variation between fresh PGO builds.

## Scope and ISA notes

- 06, 14, 22, 23 and 24 change ICL-specific paths.
- 08 and 15 use AVX2 paths, which are also active in the AVX512ICL build.
- 13 changes AVX512 feature activation.
- 16 changes the x86 VNNI second-layer loop; 17 changes the AVX512 VNNI sparse loop.
- Other changes are architecture-neutral source transformations or prefetch hints with platform-dependent effects.
- ISA-specific alternatives retain existing fallback code for other targets. Compilation on those targets has not been checked.

Use this batch's master SHA, not `effdc3fd` or an old saved master executable, as the baseline. Previous benchmark launchers may default to the old fork, older master or older candidate selection; point any runner explicitly to this repository, this manifest and this baseline.
