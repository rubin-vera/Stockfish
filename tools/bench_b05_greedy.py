#!/usr/bin/env python3
"""Select batch05 additions by measured incremental improvements."""
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



MASTER = 'dbe600b7438662e9ade60b31fa0625259dcd0077'
ORDER = [23, 4, 9, 24, 8, 19, 21, 14, 11, 15, 12, 3, 7, 5, 13, 10, 1, 2, 20, 18, 17]


def compose(repo, candidates, selected):
    import difflib
    from collections import defaultdict
    command(['git', 'reset', '--hard', MASTER], repo)
    changes = defaultdict(list)
    for number in selected:
        item = candidates[number]
        if command(['git', 'show', '-s', '--format=%P', item['commit']], repo) != MASTER:
            raise RuntimeError('Unexpected parent for candidate ' + str(number))
        for path in item['source_paths']:
            original = sp.check_output(['git', 'show', MASTER + ':' + path], cwd=repo).decode().splitlines(keepends=True)
            changed = sp.check_output(['git', 'show', item['commit'] + ':' + path], cwd=repo).decode().splitlines(keepends=True)
            for kind, a, b, x, y in difflib.SequenceMatcher(None, original, changed, autojunk=False).get_opcodes():
                if kind != 'equal':
                    changes[path].append((a, b, changed[x:y]))
    for path, edits in changes.items():
        edits.sort(key=lambda x: (x[0], x[1]))
        for a, b in zip(edits, edits[1:]):
            if a[1] > b[0] or a[0] == a[1] == b[0] == b[1]:
                raise RuntimeError('Overlapping source edits in ' + path)
        original = sp.check_output(['git', 'show', MASTER + ':' + path], cwd=repo).decode().splitlines(keepends=True)
        for a, b, replacement in reversed(edits):
            original[a:b] = replacement
        (repo / path).write_text(''.join(original))
    return sp.check_output(['git', 'diff', '--', 'src'], cwd=repo)


def main():
    import urllib.request
    parser = argparse.ArgumentParser(description='Greedy incremental batch05 composition with fresh-build controls')
    parser.add_argument('--runs', type=int, default=25, help='screening pairs per proposed addition')
    parser.add_argument('--confirm', type=int, default=100, help='fresh measurement pairs before accepting an addition')
    parser.add_argument('--control-runs', type=int, default=50)
    parser.add_argument('--control-limit', type=float, default=0.2, help='stop if fresh-master control differs by more than this percentage')
    parser.add_argument('--min-gain', type=float, default=0.05, help='minimum confirmed incremental percentage')
    parser.add_argument('--only', type=int, nargs='+', help='optional candidate IDs, in desired order')
    parser.add_argument('--sequential', action='store_true')
    args = parser.parse_args()
    if min(args.runs, args.confirm, args.control_runs) < 2 or args.control_limit <= 0 or args.min_gain < 0:
        parser.error('Pair counts must be >= 2, control limit positive, and minimum gain nonnegative')
    order = args.only if args.only is not None else ORDER
    if not order or len(order) != len(set(order)) or set(order) - set(ORDER):
        parser.error('--only must contain distinct IDs from the 21 positive candidates')
    for tool in ('git', 'make', 'g++'):
        if not shutil.which(tool):
            parser.error('Missing tool: ' + tool)
    work = Path(tempfile.mkdtemp(prefix='stockfish-b05-greedy-', dir=Path.home()))
    print('Work directory:', work, flush=True)
    url = 'https://raw.githubusercontent.com/rubin-vera/Stockfish/9a4f29fce555c459784e889102d94f7c595216d1/experiments/batch05/manifest.json'
    manifest = json.loads(urllib.request.urlopen(url, timeout=60).read())
    if manifest['base_commit'] != MASTER:
        raise RuntimeError('Wrong manifest baseline')
    candidates = {c['number']: c for c in manifest['candidates']}
    repo = work / 'repo'
    command(['git', 'clone', '--no-checkout', 'https://github.com/rubin-vera/Stockfish.git', str(repo)])
    command(['git', 'checkout', '--detach', MASTER], repo)
    sets = cpu_sets()[:2]
    concurrent = len(sets) == 2 and not args.sequential
    if not concurrent:
        sets = [[min(os.sched_getaffinity(0))]]
    config = dict(vars(args), master=MASTER, order=order, cpus=sets, concurrent=concurrent,
                  compiler=command(['g++', '--version']).splitlines()[0],
                  arch='x86-64-avx512icl', threads=1, depth=13, hash_mib=16)
    atomic(work / 'config.json', json.dumps(config, indent=2))
    print('CPUs:', sets, '| Concurrent:', concurrent, flush=True)
    print('Addition order:', order, flush=True)
    best = work / 'best'
    trial = work / 'trial'
    baseline = work / 'master'
    accepted = []
    reports = []

    def build(numbers, label, destination):
        if shutil.disk_usage(work).free < 1024**3:
            raise RuntimeError('Less than 1 GiB free; work saved in ' + str(work))
        patch = compose(repo, candidates, numbers)
        (work / (label + '.patch')).write_bytes(patch)
        print('Building', label, 'PGO; patches', numbers, flush=True)
        with (work / (label + '-build.log')).open('w') as log:
            proc = sp.Popen(['make', '-j4', 'profile-build', 'ARCH=x86-64-avx512icl', 'COMP=gcc', 'CXX=g++'],
                            cwd=repo / 'src', stdout=log, stderr=sp.STDOUT, start_new_session=True)
            try:
                code = proc.wait(timeout=3600)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
        if code:
            raise RuntimeError('Build failed: ' + str(work / (label + '-build.log')))
        shutil.copy2(repo / 'src/stockfish', destination)
        return file_hash(destination)

    def measure(base, test, runs, label):
        identities = (file_hash(base), file_hash(test))
        print(label + ': warming up', flush=True)
        for i in range(4):
            result = pair(base, test, sets, i, 1, 13, concurrent)
            if result['base']['nodes'] != 1559611:
                raise RuntimeError('Unexpected bench signature')
        pairs = []
        with (work / (label + '-pairs.jsonl')).open('w') as output:
            for i in range(runs):
                p = pair(base, test, sets, i, 1, 13, concurrent)
                pairs.append(p)
                output.write(json.dumps(p) + '\n'); output.flush()
                if (i + 1) % 10 == 0 or i + 1 == runs:
                    print(f"  {i+1}/{runs}: {summarize(pairs)['geometric_pct']:+.3f}%", flush=True)
        if identities != (file_hash(base), file_hash(test)):
            raise RuntimeError('Executable changed during measurement')
        result = dict(label=label, base_binary_sha256=identities[0], test_binary_sha256=identities[1], **summarize(pairs))
        reports.append(result)
        atomic(work / 'measurements.json', json.dumps(reports, indent=2))
        print(f"{label}: {result['geometric_pct']:+.3f}% [{result['geometric_low_95']:+.3f}%, {result['geometric_high_95']:+.3f}%]", flush=True)
        return result

    build([], 'master-first', baseline)
    build([], 'master-second', trial)
    control = measure(baseline, trial, args.control_runs, 'fresh-master-control')
    if abs(control['geometric_pct']) > args.control_limit:
        raise RuntimeError('STOP: two fresh masters differ by more than the control limit. Do not interpret this as a code speedup. Control and logs saved in ' + str(work))
    shutil.copy2(baseline, best)
    decisions = []
    for number in order:
        proposed = accepted + [number]
        label = 'add-%02d' % number
        print('\nTrying', proposed, 'against', accepted or ['master'], flush=True)
        try:
            build(proposed, label, trial)
            screen = measure(best, trial, args.runs, label + '-screen')
            keep = False
            confirmation = None
            if screen['geometric_pct'] > 0:
                # Use a new set of pairs; do not pool the screen into the confirmation.
                confirmation = measure(best, trial, args.confirm, label + '-confirm')
                keep = confirmation['geometric_low_95'] > 0 and confirmation['geometric_pct'] >= args.min_gain
            record = dict(candidate=number, proposed=proposed, accepted=keep,
                          screen=screen, confirmation=confirmation)
            if keep:
                accepted = proposed
                shutil.copy2(trial, best)
                patch = compose(repo, candidates, accepted)
                (work / 'best.patch').write_bytes(patch)
                atomic(work / 'best.json', json.dumps(dict(base_commit=MASTER, candidates=accepted,
                       source_commits={str(n): candidates[n]['commit'] for n in accepted},
                       binary_sha256=file_hash(best)), indent=2))
            decisions.append(record)
            atomic(work / 'decisions.json', json.dumps(decisions, indent=2))
            print('KEEP' if keep else 'SKIP', number, '| Current set:', accepted, flush=True)
        except (RuntimeError, sp.CalledProcessError) as error:
            # Fail closed on mismatched signatures; preserve the current best.
            atomic(work / 'failure.json', json.dumps(dict(candidate=number,error=str(error)), indent=2))
            raise
    if not accepted:
        print('No confirmed additions. Retain master. Results:', work, flush=True)
        return
    # Rebuild BOTH the selected combination and master for a final independent-build check.
    build(accepted, 'final-combination-rebuild', trial)
    build([], 'final-master-rebuild', baseline)
    final = measure(baseline, trial, args.confirm, 'final-fresh-build-vs-master')
    atomic(work / 'final.json', json.dumps(dict(candidates=accepted,
           source_patch=str(work / 'best.patch'), fresh_build_result=final,
           confirmed_positive=final['geometric_low_95'] > 0), indent=2))
    print('\nSelected patch IDs:', accepted, flush=True)
    print('Fresh-build result:', f"{final['geometric_pct']:+.3f}%", flush=True)
    print('Send best.json, decisions.json, and final.json. Files:', work, flush=True)
    print('This is a greedy search; it does not exhaust all combinations.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, sp.CalledProcessError) as error:
        raise SystemExit(str(error))
