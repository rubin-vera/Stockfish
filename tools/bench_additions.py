#!/usr/bin/env python3
"""Test independent additions to the original three-patch Stockfish baseline."""
import argparse
import csv
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import statistics as st
import subprocess as sp
import tempfile

VERSION = 1


def command(args, cwd=None):
    return sp.check_output(args, cwd=cwd, text=True, stderr=sp.STDOUT).strip()


def atomic(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(value)
    temp.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def cpu_sets():
    allowed = sorted(os.sched_getaffinity(0))
    cores = {}
    for cpu in allowed:
        root = Path('/sys/devices/system/cpu') / ('cpu%d' % cpu) / 'topology'
        try:
            key = (root.joinpath('physical_package_id').read_text().strip(),
                   root.joinpath('core_id').read_text().strip())
        except OSError:
            # Unknown topology: sequential tests avoid accidentally pairing siblings.
            return [allowed]
        cores.setdefault(key, []).append(cpu)
    return [[v[0]] for v in cores.values()]


def parse_bench(text):
    nodes = re.search(r'Nodes searched\s*:\s*(\d+)', text)
    nps = re.search(r'Nodes/second\s*:\s*(\d+)', text)
    if not nodes or not nps or int(nps[1]) <= 0:
        raise RuntimeError('Missing or invalid benchmark output: ' + text[-1200:])
    return {'nodes': int(nodes[1]), 'nps': int(nps[1])}


def run_engines(jobs, threads, depth, concurrent):
    """Affinity is installed in each child before exec; no timing of startup."""
    children = []
    outputs = []
    def launch(binary, cpus):
        output = tempfile.TemporaryFile(mode='w+t')
        outputs.append(output)
        proc = sp.Popen([str(binary), 'bench', '16', str(threads), str(depth)],
                        stdout=sp.DEVNULL, stderr=output, start_new_session=True,
                        preexec_fn=lambda: os.sched_setaffinity(0, cpus))
        children.append(proc)
        return proc, output
    def finish(proc, output):
        proc.wait(timeout=600)
        output.seek(0)
        text = output.read()
        if proc.returncode:
            raise RuntimeError('Engine exited %d: %s' % (proc.returncode, text[-1200:]))
        return parse_bench(text)
    try:
        if concurrent:
            running = [launch(*job) for job in jobs]
            return [finish(*item) for item in running]
        return [finish(*launch(*job)) for job in jobs]
    finally:
        for proc in children:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        for output in outputs:
            output.close()


def pair(base, test, sets, index, threads, depth, concurrent):
    if concurrent:
        cpus = sets if index % 2 == 0 else sets[::-1]
        values = run_engines([(base, cpus[0]), (test, cpus[1])], threads, depth, True)
    else:
        jobs = [(base, sets[0]), (test, sets[0])]
        if index % 2:
            jobs.reverse()
        values = run_engines(jobs, threads, depth, False)
        if index % 2:
            values.reverse()
    if threads == 1 and values[0]['nodes'] != values[1]['nodes']:
        raise RuntimeError('Node mismatch in timed pair: %s' % values)
    return {'pair': index, 'base': values[0], 'test': values[1]}


def summarize(pairs):
    base = [p['base']['nps'] for p in pairs]
    test = [p['test']['nps'] for p in pairs]
    delta = [t - b for b, t in zip(base, test)]
    logs = [math.log(t / b) for b, t in zip(base, test)]
    se = st.stdev(delta) / math.sqrt(len(delta)) if len(delta) > 1 else 0
    log_se = st.stdev(logs) / math.sqrt(len(logs)) if len(logs) > 1 else 0
    mean = st.mean(delta)
    probability = st.NormalDist().cdf(mean / se) if se else (1 if mean > 0 else 0 if mean < 0 else .5)
    return dict(runs=len(pairs), base_nps=st.mean(base), test_nps=st.mean(test),
                speedup_pct=100 * mean / st.mean(base), diff_nps=mean,
                diff_95=1.96 * se, p_positive_normal=probability,
                geometric_pct=100 * math.expm1(st.mean(logs)),
                geometric_low_95=100 * math.expm1(st.mean(logs) - 1.96 * log_se),
                geometric_high_95=100 * math.expm1(st.mean(logs) + 1.96 * log_se))


def load_pairs(path):
    if not path.exists():
        return []
    data = path.read_bytes()
    # A power loss may leave the last append incomplete. Keep complete lines only.
    complete = data[:data.rfind(b'\n') + 1]
    if complete != data:
        path.write_bytes(complete)
    pairs = [json.loads(line) for line in complete.splitlines()]
    for i, item in enumerate(pairs):
        if item['pair'] != i:
            raise RuntimeError('Invalid pair sequence in ' + str(path))
    return pairs


def build(repo, sha, args, log):
    if shutil.disk_usage(args.workdir).free < 1024 ** 3:
        raise RuntimeError('Less than 1 GiB free; free disk space before building.')
    command(['git', 'checkout', '--detach', '--force', sha], repo)
    with open(log, 'w') as f:
        proc = sp.Popen(['make', '-j%d' % args.jobs, 'profile-build',
                         'ARCH=' + args.arch, 'COMP=gcc', 'CXX=g++'],
                        cwd=repo / 'src', stdout=f, stderr=sp.STDOUT,
                        start_new_session=True)
        try:
            if proc.wait(timeout=3600):
                raise RuntimeError('Build failed; see ' + str(log))
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    binary = repo / 'src/stockfish'
    if not binary.is_file():
        raise RuntimeError('Build produced no src/stockfish')
    return binary


def write_summary(work, run, rows):
    if not rows:
        return
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    atomic(run / 'summary.csv', out.getvalue())
    atomic(work / 'summary.csv', out.getvalue())



MASTER = "effdc3fd3f6de6e75fe1374a51a7a7eed66f880b"
THREE = "5b89b777c6c8cb7db04e9e8ea3bfe7e757f97e5d"
SOURCE = "https://github.com/nathan-vaughan/Stockfish.git"
CANDIDATES = [{'number': 2, 'branch': 'bench/b04-02-hq-vbmi-permute', 'sha': '061b559faeca3f190f122e959e574d279badf44a'}, {'number': 3, 'branch': 'bench/b04-03-rank-pext', 'sha': '0b0796881dbc5fcf87f6474b96f16664043bdd3b'}, {'number': 4, 'branch': 'bench/b04-04-state-cacheline', 'sha': 'b5c382555b876bb0dfb01078f80c0cf987a6b7a0'}, {'number': 5, 'branch': 'bench/b04-05-stack-hot-fields', 'sha': '79b4fe9bb44d7106b0ba9756eaaaf16eadb46652'}, {'number': 6, 'branch': 'bench/b04-06-list-count32', 'sha': '7c46b64273660161922298bd9e75a6ca0ecd0be7'}, {'number': 7, 'branch': 'bench/b04-07-finny-header-first', 'sha': '6b817ab731875cfd8afd112e960cca83ac537d8a'}, {'number': 8, 'branch': 'bench/b04-08-computed-header', 'sha': '835c9c082597727b61d32d6252f5c322dc6de629'}, {'number': 11, 'branch': 'bench/b04-11-tt-probe-outline', 'sha': 'cc8a3701d3c837f7d084cab6c45cbddbb31877fc'}, {'number': 12, 'branch': 'bench/b04-12-direct-ep-legality', 'sha': 'cbd899abad7cd4ba5c1be4500df4b2392a5d152e'}, {'number': 13, 'branch': 'bench/b04-13-quiet-threat-table', 'sha': '79d80dc054743ee99092517161cf2c0fa8402198'}, {'number': 16, 'branch': 'bench/b04-16-final-hadd', 'sha': '5f0d50175dc8fb5cb043c5c5da11398fff5f3a81'}, {'number': 21, 'branch': 'bench/b04-21-tt-save-key-snapshot', 'sha': '878b6f3d896a1d52432435632f467cac9f510de6'}, {'number': 22, 'branch': 'bench/b04-22-cuckoo-parallel-load', 'sha': '53b16136a1375126068cc21ec96c2e836c4180a2'}]
def compose(repo, sha):
    command(['git', 'checkout', '--detach', '--force', THREE], repo)
    patch = sp.check_output(['git', 'diff', MASTER, sha, '--', 'src'], cwd=repo)
    if not patch:
        raise RuntimeError('Empty source patch: ' + sha)
    sp.run(['git', 'apply', '--check', '-'], input=patch, cwd=repo, check=True)
    sp.run(['git', 'apply', '-'], input=patch, cwd=repo, check=True)
    # Candidate 03 needs an ISA guard for non-BMI2 compilation. The active ICL
    # expression is identical to the separately published step-2 branch.
    attacks = repo / 'src/attacks.h'
    old = '        Bitboard rowOccupancy = rankAttacksLookup[_pext_u64(occupied, 0x7eULL << shift)];'
    if old in attacks.read_text():
        text = attacks.read_text().replace(old,
            '#if defined(USE_PEXT)\n' + old + '\n#else\n'
            '        Bitboard rowOccupancy = rankAttacksLookup[(occupied >> (shift + 1)) & 0x3f];\n#endif')
        attacks.write_text(text)
    command(['git', 'diff', '--check'], repo)
    source_hash = digest(command(['git', 'diff', '--binary', THREE, '--', 'src'], repo))
    return source_hash


def build_combination(repo, args, log):
    if shutil.disk_usage(args.workdir).free < 1024 ** 3:
        raise RuntimeError('Less than 1 GiB free; free space before resuming.')
    with open(log, 'w') as output:
        proc = sp.Popen(['make', '-j%d' % args.jobs, 'profile-build',
                         'ARCH=' + args.arch, 'COMP=gcc', 'CXX=g++'],
                        cwd=repo / 'src', stdout=output, stderr=sp.STDOUT,
                        start_new_session=True)
        try:
            if proc.wait(timeout=3600):
                raise RuntimeError('Build failed; see ' + str(log))
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    return repo / 'src/stockfish'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs', type=int, default=200)
    p.add_argument('--base', type=Path, default=Path.home() / 'stockfish-old-latest')
    p.add_argument('--workdir', type=Path, default=Path.home() / 'stockfish-additions')
    p.add_argument('--jobs', type=int, default=min(4, os.cpu_count() or 1))
    p.add_argument('--arch', default='x86-64-avx512icl')
    p.add_argument('--only', type=int, nargs='+', help='optional batch-04 candidate numbers')
    p.add_argument('--sequential', action='store_true', help='alternate on one fixed logical CPU')
    args = p.parse_args(argv)
    if args.runs < 2 or args.jobs < 1:
        p.error('--runs must be at least 2 and --jobs positive')
    for tool in ('git', 'make', 'g++'):
        if not shutil.which(tool):
            p.error('Missing dependency: ' + tool)
    base = args.base.expanduser().resolve()
    if not base.is_file() or not os.access(base, os.X_OK):
        p.error('Missing executable baseline: ' + str(base))
    work = args.workdir.expanduser().resolve()
    args.workdir = work
    work.mkdir(parents=True, exist_ok=True)
    chosen = [x for x in CANDIDATES if not args.only or x['number'] in args.only]
    if not chosen or (args.only and set(args.only) - {x['number'] for x in CANDIDATES}):
        p.error('--only must select from: ' + ', '.join(str(x['number']) for x in CANDIDATES))
    with open(work / 'runner.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            p.error('Another additions runner is using this work directory')
        physical = cpu_sets()
        concurrent = len(physical) >= 2 and not args.sequential
        sets = physical[:2] if concurrent else [[min(os.sched_getaffinity(0))]]
        baseline_hash = file_hash(base)
        config = dict(version=1, baseline_commit=THREE, baseline_path=str(base),
                      baseline_sha256=baseline_hash, master=MASTER, candidates=chosen,
                      arch=args.arch, compiler=command(['g++', '--version']),
                      depth=13, threads=1, hash_mib=16, cpus=sets, concurrent=concurrent,
                      hostname=platform.node(), kernel=platform.release(),
                      build_environment={k: os.environ.get(k, '') for k in
                          ('CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'MAKEFLAGS')},
                      script_sha256=file_hash(Path(__file__)))
        run = work / 'runs' / digest(config)
        run.mkdir(parents=True, exist_ok=True)
        atomic(run / 'config.json', json.dumps(config, indent=2))
        print('BASE: original three-patch executable ' + str(base), flush=True)
        print('Each TEST: original three + ONE selected addition.', flush=True)
        print('Candidates: %d; pairs each: %d; CPUs: %s; mode: %s' %
              (len(chosen), args.runs, sets, 'concurrent' if concurrent else 'sequential'), flush=True)
        print('Results: ' + str(run), flush=True)
        check = run_engines([(base, sets[0])], 1, 13, False)[0]
        if check['nodes'] != 1559611:
            raise RuntimeError('Baseline signature is not 1559611: ' + str(check))
        repo = work / 'repo'
        marker = work / 'managed-repo.json'
        if repo.exists():
            if not marker.exists() or json.loads(marker.read_text()) != {'origin': SOURCE}:
                raise RuntimeError('Refusing to alter an unrecognized checkout: ' + str(repo))
            if command(['git', 'remote', 'get-url', 'origin'], repo) != SOURCE:
                raise RuntimeError('Checkout origin changed')
        else:
            print('Cloning shared build checkout...', flush=True)
            command(['git', 'clone', '--no-checkout', SOURCE, str(repo)])
            atomic(marker, json.dumps({'origin': SOURCE}))
        command(['git', 'fetch', 'origin', '+refs/heads/bench/b04-*:refs/remotes/origin/bench/b04-*'], repo)
        # Freeze the tested commit rather than silently following later branch updates.
        for item in chosen:
            command(['git', 'cat-file', '-e', item['sha'] + '^{commit}'], repo)
        command(['git', 'cat-file', '-e', THREE + '^{commit}'], repo)
        # Reuse downloaded nets as ordinary copies, never alter the other checkout.
        (repo / 'src').mkdir(exist_ok=True)
        for net in (Path.home() / 'stockfish-batch04/repo/src').glob('*.nnue'):
            target = repo / 'src' / net.name
            if not target.exists():
                shutil.copy2(net, target)
        rows, failures = [], []
        for item in chosen:
            name = item['branch'].split('/')[-1]
            path = run / (name + '.jsonl')
            print('\n' + item['branch'] + ' on original three', flush=True)
            try:
                pairs = load_pairs(path)
                print('%d/%d pairs saved' % (len(pairs), args.runs), flush=True)
                if len(pairs) < args.runs:
                    source_hash = compose(repo, item['sha'])
                    atomic(run / (name + '-source.json'), json.dumps(dict(
                        base=THREE, addition=item['sha'], combined_source_hash=source_hash), indent=2))
                    binary_record = run / (name + '-binary.json')
                    prior_binary = json.loads(binary_record.read_text()) if binary_record.exists() else {}
                    candidate_binary = repo / 'src/stockfish'
                    if (pairs and candidate_binary.exists()
                        and prior_binary.get('sha256') == file_hash(candidate_binary)):
                        print('Reusing the interrupted candidate executable.', flush=True)
                        test = candidate_binary
                    else:
                        print('Building combination (PGO)...', flush=True)
                        test = build_combination(repo, args, run / (name + '-build.log'))
                    if pairs and prior_binary.get('sha256') != file_hash(test):
                        # Never mix timings from separately generated executables.
                        with tempfile.NamedTemporaryFile(dir=run, prefix=name + '-previous-',
                                                         suffix='.jsonl', delete=False) as archived:
                            archive_path = Path(archived.name)
                        path.replace(archive_path)
                        pairs = []
                        print('Rebuilt executable differs: archived old pairs and restarting this candidate.', flush=True)
                    if run_engines([(test, sets[0])], 1, 13, False)[0]['nodes'] != 1559611:
                        raise RuntimeError('Combined bench signature mismatch')
                    atomic(run / (name + '-binary.json'), json.dumps(dict(
                        sha256=file_hash(test), base_sha256=baseline_hash), indent=2))
                    print('Signature matched. Warming up...', flush=True)
                    for i in range(4):
                        pair(base, test, sets, i, 1, 13, concurrent)
                    while len(pairs) < args.runs:
                        entry = pair(base, test, sets, len(pairs), 1, 13, concurrent)
                        with open(path, 'a') as output:
                            output.write(json.dumps(entry) + '\n')
                            output.flush()
                            os.fsync(output.fileno())
                        pairs.append(entry)
                        if len(pairs) % 10 == 0 or len(pairs) == args.runs:
                            print('%d/%d: incremental speedup %+.3f%%' %
                                  (len(pairs), args.runs, summarize(pairs)['speedup_pct']), flush=True)
                    if file_hash(base) != baseline_hash:
                        raise RuntimeError('Baseline executable changed during testing')
                result = summarize(pairs[:args.runs])
                rows.append(dict(branch=item['branch'], addition_commit=item['sha'],
                                 base_commit=THREE, **result))
                write_summary(work, run, rows)
                print('Increment over ORIGINAL THREE: %+.3f%%; approximate 95%% interval %+.3f%% to %+.3f%%' %
                      (result['speedup_pct'], result['geometric_low_95'], result['geometric_high_95']), flush=True)
            except (RuntimeError, sp.SubprocessError) as error:
                failures.append(dict(branch=item['branch'], error=str(error)))
                print('FAILED: ' + str(error), flush=True)
            atomic(run / 'failures.json', json.dumps(failures, indent=2))
        print('\nFinished: %d successful, %d failed. CSV: %s' %
              (len(rows), len(failures), work / 'summary.csv'), flush=True)
        return bool(failures)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\nStopped. Repeat the same command to resume completed pairs.', flush=True)
        raise SystemExit(130)
