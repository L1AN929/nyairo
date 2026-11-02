"""Run owned suites and the official Hermes runner without personal credentials."""
import argparse, os, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--full-hermes',action='store_true');a=p.parse_args()
 suites=[('closeout','components','',['-m','unittest','-q','test_closeout']),
  ('native','components','native:native/src',['-m','unittest','discover','-s','native/src/tests','-q']),
  ('memory','components','native',['-m','unittest','-q','memory_runtime_v1.test_runtime']),
  ('supply','components','supply',['-m','unittest','discover','-s','supply/lifesupply/tests','-q']),
  ('world','components','world:world/service:world/service/bridge:world/scripts',['-m','unittest','discover','-s','world/service','-q']),
  ('life','components','',['check_life.py']),
  ('life-v02','scripts','',['life_v02_isolated_check.py']),
  ('life-memory-v02','components','life/scripts:native:native/src:native/memory_runtime_v1',['check_life_memory_v02.py']),
  ('life-b7','components','life/scripts:native:native/src:native/memory_runtime_v1:.:../chiyo_bundle',['check_life_b7.py']),
  ('life-b8','components','life/scripts:native:native/src:native/memory_runtime_v1:.:../chiyo_bundle',['check_life_b8.py']),
  ('life-b8-causal','components','life/scripts:native:native/src:native/memory_runtime_v1:.:../chiyo_bundle',['check_life_b8_causal.py']),
  ('life-b8-6a','components','life/scripts:native:native/src:native/memory_runtime_v1:.:../chiyo_bundle',['check_life_b8_6a.py']),
  # host-boundaries 按文件各起一个子进程（scripts/owned_test_runner.py）。
  # 单进程把全部 233 个 test_*.py 装进同一个解释器时，vendor/hermes 的 tests 包会
  # 抢走 `tests.*` 导入，且 `phone`/`scripts` 等名字相互污染；覆盖口径不变。
  ('host-boundaries','','',['scripts/owned_test_runner.py','-j','4']),
  ('cognition-faults','','.:vendor/hermes',['-m','pytest','tests/test_cognition_reliability.py','-q'])]
 failed=[]
 home=tempfile.mkdtemp(prefix='chiyo-validation-')
 print('VALIDATION_HOME',home,flush=True)
 env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','TZ','SYSTEMROOT')}
 env.update(HOME=home,HERMES_HOME=home,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
 for name,subdir,paths,args in suites:
  cwd=ROOT/subdir;env['PYTHONPATH']=os.pathsep.join(str(cwd/x) for x in paths.split(':') if x)
  print('SUITE',name,flush=True)
  if subprocess.run([sys.executable,'-B',*args],cwd=cwd,env=env).returncode:failed.append(name)
 env.pop('PYTHONPATH',None);env['HERMES_PYTHON']=sys.executable
 paths=[] if a.full_hermes else ['tests/agent/test_context_engine.py','tests/agent/test_context_engine_host_contract.py','tests/agent/test_context_engine_select_context.py','tests/run_agent/test_plugin_context_engine_init.py']
 if subprocess.run(['bash','scripts/run_tests.sh','-j','2',*paths],cwd=ROOT/'vendor/hermes',env=env).returncode:failed.append('Hermes')
 print('FAILED_SUITES',failed,flush=True);return bool(failed)
if __name__=='__main__':raise SystemExit(main())
