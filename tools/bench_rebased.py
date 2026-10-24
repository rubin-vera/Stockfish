#!/usr/bin/env python3
"""Build and benchmark the rebased three-patch combination against its master parent."""
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



MASTER = "dbe600b7438662e9ade60b31fa0625259dcd0077"
PATCH = "feb9881584af08b66189ad8eb9e8b08c1aa208d1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=int, default=100)
    parser.add_argument('--jobs', type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument('--arch', default='x86-64-avx512icl')
    parser.add_argument('--sequential', action='store_true')
    args = parser.parse_args()
    if args.runs < 2 or args.jobs < 1:
        parser.error('--runs must be >= 2 and --jobs must be positive')
    for tool in ('git', 'make', 'g++'):
        if not shutil.which(tool):
            parser.error('Missing dependency: ' + tool)
    home = Path.home()
    if shutil.disk_usage(home).free < 1024**3:
        parser.error('At least 1 GiB of free disk space is required')
    work = Path(tempfile.mkdtemp(prefix='stockfish-rebased-bench-', dir=home))
    print('Results and build logs:', work, flush=True)
    repo = work / 'repo'
    command(['git', 'clone', '--depth', '2', '--single-branch', '--branch',
             'speedup-three-patches-rebased',
             'https://github.com/rubin-vera/Stockfish.git', str(repo)])
    if command(['git', 'rev-parse', 'HEAD'], repo) != PATCH:
        raise RuntimeError('Remote branch changed; this runner pins a specific rebase')
    if command(['git', 'rev-parse', 'HEAD^'], repo) != MASTER:
        raise RuntimeError('Unexpected master parent')
    src = repo / 'src'
    net = 'nn-c9e480e92425.nnue'
    for old in (home / 'stockfish-batch04/repo/src',
                home / 'stockfish-additions/repo/src'):
        if (old / net).is_file():
            shutil.copy2(old / net, src / net)
            break
    sets = cpu_sets()
    concurrent = len(sets) >= 2 and not args.sequential
    sets = sets[:2] if concurrent else [[min(os.sched_getaffinity(0))]]
    config = dict(master=MASTER, patch=PATCH, arch=args.arch,
                  compiler=command(['g++', '--version']).splitlines()[0],
                  cpus=sets, concurrent=concurrent, runs=args.runs,
                  threads=1, hash_mib=16, depth=13)
    atomic(work / 'config.json', json.dumps(config, indent=2))
    print('CPUs:', sets, '| Mode:', 'concurrent, swapping CPUs' if concurrent
          else 'sequential, alternating order', flush=True)
    binaries = []
    for label, sha in (('master', MASTER), ('rebased', PATCH)):
        print('Building ' + label + ' (PGO)...', flush=True)
        command(['git', 'checkout', '--detach', sha], repo)
        log = work / (label + '-build.log')
        with log.open('w') as output:
            result = sp.run(['make', '-j' + str(args.jobs), 'profile-build',
                             'ARCH=' + args.arch, 'COMP=gcc', 'CXX=g++'],
                            cwd=src, stdout=output, stderr=sp.STDOUT)
        if result.returncode:
            raise RuntimeError('Build failed; see ' + str(log))
        binary = work / label
        shutil.copy2(src / 'stockfish', binary)
        binaries.append(binary)
    base, test = binaries
    identities = {binary.name: file_hash(binary) for binary in binaries}
    atomic(work / 'binaries.json', json.dumps(identities, indent=2))
    print('Checking signatures and warming up...', flush=True)
    for i in range(4):
        warm = pair(base, test, sets, i, 1, 13, concurrent)
        if warm['base']['nodes'] != 1559611:
            raise RuntimeError('Unexpected bench signature: ' + str(warm))
    pairs = []
    with (work / 'pairs.jsonl').open('w') as output:
        for i in range(args.runs):
            result = pair(base, test, sets, i, 1, 13, concurrent)
            pairs.append(result)
            output.write(json.dumps(result) + '\n')
            output.flush()
            os.fsync(output.fileno())
            if (i + 1) % 10 == 0 or i + 1 == args.runs:
                summary = summarize(pairs)
                print(f"{i+1}/{args.runs}: {summary['geometric_pct']:+.3f}%", flush=True)
    if any(file_hash(binary) != identities[binary.name] for binary in binaries):
        raise RuntimeError('A benchmark executable changed during the test')
    summary = summarize(pairs)
    atomic(work / 'summary.json', json.dumps(summary, indent=2))
    with (work / 'summary.csv').open('w', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=list(summary))
        writer.writeheader()
        writer.writerow(summary)
    print(f"\nCurrent master: {summary['base_nps']:,.0f} NPS")
    print(f"Rebased three:  {summary['test_nps']:,.0f} NPS")
    print(f"Geometric speedup: {summary['geometric_pct']:+.3f}%")
    print(f"Approx. 95% interval: {summary['geometric_low_95']:+.3f}% to "
          f"{summary['geometric_high_95']:+.3f}%")
    print('Results:', work / 'summary.csv', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, sp.CalledProcessError, OSError) as error:
        raise SystemExit(str(error))
