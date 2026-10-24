import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('runner',Path(__file__).with_name('bench_additions.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class RunnerTests(unittest.TestCase):
 def test_manifest(self):
  self.assertEqual([x['number'] for x in r.CANDIDATES],[2,3,4,5,6,7,8,11,12,13,16,21,22])
 def test_end_to_end(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);repo=root/'fixture';repo.mkdir();src=repo/'src';src.mkdir()
   def git(*a):return r.command(['git','-c','user.name=Test','-c','user.email=test@example.com',*a],repo)
   git('init','-b','master')
   engine='#!/usr/bin/env python3\nimport sys\nprint("Nodes searched: 1559611\\nNodes/second: 1000000",file=sys.stderr)\n'
   (src/'engine.py').write_text(engine);(src/'attacks.h').write_text('// fixture\n')
   (src/'Makefile').write_text('profile-build:\n\tcp engine.py stockfish\n\tchmod +x stockfish\n')
   (repo/'.gitignore').write_text('src/stockfish\n')
   git('add','.');git('commit','-m','master');master=git('rev-parse','HEAD')
   (src/'original-three.txt').write_text('Keep original three\n');git('add','.');git('commit','-m','three');three=git('rev-parse','HEAD')
   git('checkout','-b','bench/b04-02-fixture',master)
   (src/'engine.py').write_text(engine.replace('1000000','1010000'));git('add','.');git('commit','-m','candidate');sha=git('rev-parse','HEAD')
   base=root/'base';base.write_text(engine);base.chmod(0o755)
   saved=(r.SOURCE,r.MASTER,r.THREE,r.CANDIDATES)
   r.SOURCE=str(repo);r.MASTER=master;r.THREE=three;r.CANDIDATES=[dict(number=2,branch='bench/b04-02-fixture',sha=sha)]
   try:
    args=['--workdir',str(root/'work'),'--base',str(base),'--runs','2','--sequential']
    with contextlib.redirect_stdout(io.StringIO()):self.assertFalse(r.main(args))
    work=root/'work';pairs=list((work/'runs').glob('*/*.jsonl'));self.assertEqual(len(pairs),1)
    self.assertEqual(len(r.load_pairs(pairs[0])),2)
    self.assertTrue((work/'repo/src/original-three.txt').exists())
    before=pairs[0].stat().st_mtime_ns
    with contextlib.redirect_stdout(io.StringIO()):self.assertFalse(r.main(args))
    self.assertEqual(before,pairs[0].stat().st_mtime_ns)
    record=next((work/'runs').glob('*/*-binary.json'))
    metadata=json.loads(record.read_text());metadata['sha256']='different previous build';record.write_text(json.dumps(metadata))
    with contextlib.redirect_stdout(io.StringIO()):self.assertFalse(r.main(args+['--runs','3']))
    self.assertEqual(len(r.load_pairs(pairs[0])),3)
    archives=list((work/'runs').glob('*/*-previous-*.jsonl'))
    self.assertEqual(len(archives),1)
    self.assertEqual(len(r.load_pairs(archives[0])),2)
    self.assertIn('1.0',(work/'summary.csv').read_text())
   finally:r.SOURCE,r.MASTER,r.THREE,r.CANDIDATES=saved

if __name__=='__main__':unittest.main()
