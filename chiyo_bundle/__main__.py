"""Synthetic contract driver; ordinary use goes through the Hermes CLI/gateway."""
import argparse,json,sys
from pathlib import Path
from .instance import Instance
LIFE_VERBS=('enable','disable','status','run-once','list-recent-actions')
def _life(args):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'components/life/scripts'))
    import autonomous_life_control as control
    if args.action=='enable':print(json.dumps(control.set_enabled(args.state,True),ensure_ascii=False));return
    if args.action=='disable':print(json.dumps(control.set_enabled(args.state,False),ensure_ascii=False));return
    if args.action=='status':print(json.dumps(control.read_status(args.state),ensure_ascii=False,indent=1));return
    if args.action=='list-recent-actions':
        print(json.dumps(control.list_recent_actions(args.state,limit=args.limit),ensure_ascii=False,indent=1));return
    # run-once is the development entry: one real consideration window through the real
    # runner, obtained from the same assembly the Hermes plugin uses.  It never assumes a
    # global cycle object, and it never builds a second canonical writer: the runtime it
    # asks for is the one that holds the lease, and a lease held elsewhere fails closed.
    try:
        from life_runtime_adapter import autonomous_life, autonomous_status, runner_tick
        from autonomous_life_assembly import run_once as _run_once
    except Exception as exc:
        print(json.dumps({'ok': False, 'reason': 'ASSEMBLY_UNAVAILABLE',
                          'detail': '%s: %s' % (type(exc).__name__, exc)}, ensure_ascii=False))
        return
    if args.action == 'status':
        assembled = autonomous_life()
        print(json.dumps({'control': control.read_status(args.state),
                          'assembled': autonomous_status()}, ensure_ascii=False, indent=1))
        return
    assembled = autonomous_life()
    print(json.dumps(_run_once(assembled), ensure_ascii=False, indent=1))
def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['chat','doctor','life']);p.add_argument('--state',required=True);p.add_argument('--owner',default='local-owner');p.add_argument('--memory',action='store_true');p.add_argument('--life',action='store_true');p.add_argument('--tools',action='store_true');p.add_argument('--text')
 p.add_argument('--action',choices=LIFE_VERBS);p.add_argument('--limit',type=int,default=20)
 a=p.parse_args()
 if a.command=='life':
  if not a.action:p.error('life requires --action {enable,disable,status,run-once,list-recent-actions}')
  _life(a);return
 i=Instance(a.state,owner=a.owner,memory=a.memory,life=a.life,tools=a.tools)
 try:
  if a.command=='doctor':print(json.dumps(i.health(),ensure_ascii=False));return
  while True:
   try:text=a.text or input('你: ')
   except (EOFError,KeyboardInterrupt):break
   r=i.chat(text);print(r['reply'],flush=True);i.confirm_visible(r['request_id'])
   if a.text:break
 finally:i.close()
if __name__=='__main__':main()
