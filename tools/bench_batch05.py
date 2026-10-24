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




def main():
    import urllib.request
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=25)
    args = parser.parse_args()
    if args.runs < 2:
        parser.error('--runs must be at least 2')
    work = Path(tempfile.mkdtemp(prefix='stockfish-b05-', dir=Path.home()))
    print('Results:', work, flush=True)
    url = 'https://raw.githubusercontent.com/rubin-vera/Stockfish/9a4f29fce555c459784e889102d94f7c595216d1/experiments/batch05/manifest.json'
    manifest = json.loads(urllib.request.urlopen(url).read())
    base_sha = manifest['base_commit']
    assert base_sha == 'dbe600b7438662e9ade60b31fa0625259dcd0077'
    atomic(work / 'manifest.json', json.dumps(manifest, indent=2))
    repo = work / 'repo'
    command(['git', 'clone', '--no-checkout', 'https://github.com/rubin-vera/Stockfish.git', str(repo)])
    sets = cpu_sets()[:2]
    concurrent = len(sets) == 2
    if not concurrent:
        sets = [[min(os.sched_getaffinity(0))]]
    print('CPUs:', sets, 'Concurrent:', concurrent, flush=True)
    def compile_engine(sha, label):
        if shutil.disk_usage(work).free < 1024**3:
            raise RuntimeError('Less than 1 GiB free disk space')
        command(['git', 'checkout', '--detach', sha], repo)
        print('Building ' + label + ' (PGO)...', flush=True)
        log = work / (label + '-build.log')
        with log.open('w') as out:
            r = sp.run(['make', '-j4', 'profile-build', 'ARCH=x86-64-avx512icl', 'COMP=gcc', 'CXX=g++'], cwd=repo/'src', stdout=out, stderr=sp.STDOUT)
        if r.returncode:
            raise RuntimeError('Build failed: ' + str(log))
        return repo / 'src/stockfish'
    base = work / 'master'
    shutil.copy2(compile_engine(base_sha, 'master'), base)
    rows = []
    failures = []
    for c in manifest['candidates']:
        label = '%02d' % c['number']
        print('\n' + c['branch'], flush=True)
        try:
            assert command(['git','show','-s','--format=%P',c['commit']],repo) == base_sha
            test = compile_engine(c['commit'], label)
            identity = file_hash(test)
            for i in range(4):
                warm = pair(base,test,sets,i,1,13,concurrent)
                if warm['base']['nodes'] != 1559611:
                    raise RuntimeError('Unexpected master signature')
            pairs = []
            with (work / (label + '-pairs.jsonl')).open('w') as out:
                for i in range(args.runs):
                    item = pair(base,test,sets,i,1,13,concurrent)
                    pairs.append(item)
                    out.write(json.dumps(item)+'\n');out.flush()
                    if (i+1)%5 == 0 or i+1 == args.runs:
                        print(f"{i+1}/{args.runs}: {summarize(pairs)['geometric_pct']:+.3f}%",flush=True)
            row = dict(branch=c['branch'],commit=c['commit'],binary_sha256=identity,**summarize(pairs))
            rows.append(row)
            with (work/'summary.csv').open('w',newline='') as out:
                writer=csv.DictWriter(out,fieldnames=list(row));writer.writeheader();writer.writerows(rows)
            print(f"Speedup: {row['geometric_pct']:+.3f}%; approximate 95% interval: {row['geometric_low_95']:+.3f}% to {row['geometric_high_95']:+.3f}%",flush=True)
        except (RuntimeError,sp.CalledProcessError,AssertionError,OSError) as error:
            failures.append(dict(branch=c['branch'],error=str(error)))
            atomic(work/'failures.json',json.dumps(failures,indent=2))
            print('FAILED:',error,flush=True)
    print(f"\nFinished: {len(rows)} successful, {len(failures)} failed. Summary: {work/'summary.csv'}",flush=True)

if __name__ == '__main__':
    main()
