"""CHIYO context selection inside the standard Hermes CLI and gateway."""
from __future__ import annotations
import atexit,copy,hashlib,json,logging,os,re,sys,threading,uuid
from collections import OrderedDict
from typing import Optional
from agent.context_compressor import ContextCompressor
from hermes_constants import get_hermes_home
LOG=logging.getLogger('chiyo.hermes')
_lock=threading.RLock();_instance=None;_home=None;_host_llm=None
_SESSION_AUTH_CTX_MAX = 256
_SESSION_AUTH_CTX: OrderedDict[str, tuple[str, Optional[str]]] = OrderedDict()
_SESSION_DELEGATED: OrderedDict[str, bool] = OrderedDict()

_RE_INJECTED_ENVELOPE_WITH_MARKER = re.compile(
    r"^\[当前真实处境与自主节律（[^\]]*）\]\r?\n[\s\S]*?\r?\n【伴侣此刻发来的原话】\r?\n"
)
_RE_INJECTED_ENVELOPE_BLOCK = re.compile(
    r"^\[当前真实处境与自主节律（(?:非对方台词|仅本轮生效)[^\]]*）\]\r?\n[\s\S]*?\r?\n\r?\n"
)
_RE_INJECTED_PARTNER_HEADER = re.compile(
    r"^【伴侣此刻发来的原话】\r?\n"
)

def strip_injected_turn_envelope(text: str) -> str:
    """Strip legacy/internal turn guidance envelopes so only genuine user words remain."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    cur = text
    for _ in range(16):
        nxt = _RE_INJECTED_ENVELOPE_WITH_MARKER.sub("", cur, count=1)
        if nxt == cur:
            nxt = _RE_INJECTED_ENVELOPE_BLOCK.sub("", cur, count=1)
        if nxt == cur:
            nxt = _RE_INJECTED_PARTNER_HEADER.sub("", cur, count=1)
        if nxt == cur:
            break
        cur = nxt
    return cur

def register_session_auth_context(session_id, platform, conversation_key=None, *, delegated=False):
    if not isinstance(session_id, str) or not session_id.strip():
        return
    sid = session_id.strip()
    plat = str(platform or 'cli').strip() or 'cli'
    ckey = str(conversation_key).strip() if isinstance(conversation_key, str) and conversation_key.strip() else None
    with _lock:
        _SESSION_AUTH_CTX[sid] = (plat, ckey)
        _SESSION_AUTH_CTX.move_to_end(sid)
        while len(_SESSION_AUTH_CTX) > _SESSION_AUTH_CTX_MAX:
            _SESSION_AUTH_CTX.popitem(last=False)
        _SESSION_DELEGATED[sid] = bool(delegated)
        _SESSION_DELEGATED.move_to_end(sid)
        while len(_SESSION_DELEGATED) > _SESSION_AUTH_CTX_MAX:
            _SESSION_DELEGATED.popitem(last=False)

def clear_session_auth_context():
    with _lock:
        _SESSION_AUTH_CTX.clear()
        _SESSION_DELEGATED.clear()

def is_session_delegated(session_id=None) -> bool:
    if isinstance(session_id, str) and session_id.strip():
        with _lock:
            if _SESSION_DELEGATED.get(session_id.strip()) is True:
                return True
    dc = sys.modules.get('agent.delegation_context')
    if dc is not None:
        try:
            fn = getattr(dc, 'is_delegated_child_context', None)
            if callable(fn) and fn():
                return True
        except Exception:
            pass
    return False

def lookup_session_auth_context(session_id=None) -> Optional[tuple[str, Optional[str]]]:
    if isinstance(session_id, str) and session_id.strip():
        sid = session_id.strip()
        with _lock:
            hit = _SESSION_AUTH_CTX.get(sid)
            if hit is not None:
                _SESSION_AUTH_CTX.move_to_end(sid)
                return hit
    sc = sys.modules.get('gateway.session_context')
    if sc is not None:
        try:
            unset = getattr(sc, '_UNSET', None)
            id_var = getattr(sc, '_SESSION_ID', None)
            if id_var is not None:
                cur_sid = id_var.get(unset)
                if cur_sid is not unset and isinstance(cur_sid, str) and cur_sid.strip():
                    with _lock:
                        hit = _SESSION_AUTH_CTX.get(cur_sid.strip())
                        if hit is not None:
                            _SESSION_AUTH_CTX.move_to_end(cur_sid.strip())
                            return hit
            p_var = getattr(sc, '_SESSION_PLATFORM', None)
            k_var = getattr(sc, '_SESSION_KEY', None)
            plat_val = p_var.get(unset) if p_var is not None else unset
            if plat_val is not unset and isinstance(plat_val, str) and plat_val.strip():
                key_val = k_var.get(unset) if k_var is not None else unset
                ckey_val = key_val.strip() if (key_val is not unset and isinstance(key_val, str) and key_val.strip()) else None
                return (plat_val.strip(), ckey_val)
        except Exception:
            pass
    return None

def _ensure_direct_surface_toolsets():
    """Keep Chiyo's bounded embodied toolsets directly visible to the model instead of
    being hidden behind Hermes's English-only Snowball BM25 tool_search catalog."""
    try:
        from tools import tool_search as _ts
        cur = getattr(_ts, '_DIRECT_SURFACE_TOOLSETS', None)
        if isinstance(cur, (set, frozenset)):
            _ts._DIRECT_SURFACE_TOOLSETS = frozenset(cur | {'chiyo_world', 'chiyo_memory'})
    except Exception:
        pass

def configure_host_llm(facade):
    global _host_llm
    _host_llm=facade
    _ensure_direct_surface_toolsets()

def configuration():
    home=get_hermes_home().resolve();path=home/'chiyo/config.json'
    if not path.exists():return home,{}
    return home,json.loads(path.read_text(encoding='utf8'))

def allowed(config,platform,conversation_key):
    if platform == 'cli':
        return config.get('cli_owner') is True and config.get('local_owner_uid') == (os.getuid() if hasattr(os,'getuid') else None)
    if platform == 'local':return False
    bindings = config.get('gateway_bindings',{}).get(platform,[])
    key_str = str(conversation_key) if conversation_key else ''
    is_dm = key_str.startswith('dm:') or ':dm:' in key_str
    matched = conversation_key in bindings or (platform == 'api_server' and key_str.endswith(':dm:local-owner') and 'dm:local-owner' in bindings)
    return bool(conversation_key and is_dm and matched)

def services():
    global _instance,_home
    home,cfg=configuration()
    from .live_endpoints import resolve_live_endpoints
    endpoints=resolve_live_endpoints(cfg)
    if not cfg.get('memory') and not cfg.get('life') and not endpoints.get('world_socket'):return None
    with _lock:
        if _instance is not None:
            if home!=_home:raise RuntimeError('CHIYO v0.1 requires one personal profile per process')
            try:_instance.refresh_live_endpoints(cfg)
            except Exception:pass
            return _instance
        from .instance import Instance
        # Pass a private configuration mapping; never change process environment.
        environment=dict(os.environ)
        # The owner services never make a model call during assembly. Hermes owns
        # the actual credentials/model selection, and Shadow is off by default.
        defaults={'CHIYO_MODEL_API_KEY':'unused-owner-service','CHIYO_MODEL':'unused-owner-service',
            'CHIYO_MODEL_BASE_URL':'https://api.example.invalid/v1'}
        for k,v in defaults.items():environment.setdefault(k,v)
        _instance=Instance(home/'chiyo/state',owner=cfg['owner'],memory=bool(cfg.get('memory')),life=bool(cfg.get('life')),host_llm=_host_llm,cognition_shadow=bool(cfg.get('cognition_shadow')),world_socket=endpoints.get('world_socket'),supply_socket=endpoints.get('supply_socket'),supply_subject=endpoints.get('supply_subject'),environment=environment,discovery_cfg=cfg,discovery_home=home)
        _home=home
        return _instance

def close_services():
    global _instance
    with _lock:
        if _instance is not None:_instance.close();_instance=None
atexit.register(close_services)

def observe_life_input(svc,platform,session_id,ref):
    if not getattr(svc,'life_wrapper',None):return
    from integration.live_inbound import (LifeObservationEvent,mint_event_id,subject_ref_for,correlation_ref_for,EVENT_TYPE_ORDINARY_COMMUNICATION)
    from telegram_adapter import utc_now
    event=LifeObservationEvent(event_id=mint_event_id(source=platform,chat_id=str(session_id),message_id=ref),
        event_type=EVENT_TYPE_ORDINARY_COMMUNICATION,source=platform,subject_ref=subject_ref_for(platform,svc.owner),
        conversation_ref=svc.binding['conversation_id'],occurred_at=utc_now(),correlation_id=correlation_ref_for(ref))
    svc.life_wrapper._dispatcher.submit(event)

def source_scope(source):
    if source is None:return 'cli',None
    from gateway.session import build_session_key
    platform=getattr(source.platform,'value',str(source.platform))
    if getattr(source,'chat_type','')!='dm':return platform,None
    return platform,build_session_key(source,profile=getattr(source,'profile',None))

def memory_tool_gate(tool_name,args,**kwargs):
    # Arbitrary tools can read transcripts or delegate that read to another agent.
    # Default memory profiles allow only the controlled context and human commands.
    try:
        home,cfg=configuration()
    except Exception:
        return {'action':'block','message':'Personal memory configuration is unavailable; historical memory tools are blocked.'}
    # This bounded World tool cannot read transcripts or personal memory.
    # Allow world movement and Hermes progressive disclosure bridge tools
    # v0.4: the phone tool does not read the memory DB or transcripts, but that alone is NOT a
    # reason to wave it through: her private notes can hold her own past.  The tool therefore
    # carries its own stricter gate -- under a restricted memory profile it only allows
    # wallet/shop/inventory/operation, and notes/references/reading need the profile's explicit
    # `phone.private_artifacts` permit.  This allowlist entry only stops the blanket block from
    # hiding the economy views; it does not widen what a restricted profile may read.
    if tool_name == 'chiyo_phone':
        if kwargs.get('parent_session_id') or kwargs.get('delegated') is True:
            return {'action':'block','message':'被委派的会话没有自己的手机入口。'}
        _is_del = globals().get('is_session_delegated')
        if callable(_is_del) and _is_del(kwargs.get('session_id')):
            return {'action':'block','message':'被委派的会话没有自己的手机入口。'}
        _plat = kwargs.get('platform')
        _ckey = kwargs.get('conversation_key') if 'conversation_key' in kwargs else kwargs.get('conversation_id')
        if not _plat:
            _lookup = globals().get('lookup_session_auth_context')
            _ctx = _lookup(kwargs.get('session_id')) if callable(_lookup) else None
            if _ctx is not None:
                _plat, _ckey = _ctx
        if _plat and str(_plat) != 'cli':
            _allow_fn = globals().get('allowed')
            if callable(_allow_fn) and not _allow_fn(cfg, str(_plat), _ckey):
                return {'action':'block','message':'这个聊天入口没有绑定你的个人实例，手机保持关闭。'}
        return None
    if tool_name in ('world_move', 'tool_search', 'tool_describe', 'tool_call'):
        return None
    if cfg.get('memory') and (cfg.get('memory_tool_policy') != 'unrestricted' or tool_name in ('memory','session_search')):
        return {'action':'block','message':'Personal memory mode blocks model tools that could read forgotten history. Use /chiyo_memory; unrestricted tools require an explicit profile setting and remove this protection.'}
    return None

def memory_command(raw_args,*,source=None):
    home,cfg=configuration();platform,key=source_scope(source)
    if not allowed(cfg,platform,key):return '这个聊天入口没有绑定你的个人记忆，已拒绝修改。'
    with _lock:
        svc=services()
        if svc is None or not svc.memory:return '这个 Hermes 配置没有启用该个体记忆。'
        from telegram_adapter import utc_now
        text='/memory '+raw_args.strip();turn=uuid.uuid4().hex;ref='telegram:'+svc.owner+':'+str(int(turn[:15],16))
        receipt=svc.native.m37_bridge.write_user_event(conversation_id=svc.binding['conversation_id'],user_text=text,
            source_ref=ref,turn_id=turn,occurred_at=utc_now(),source_kind="hermes_personal_input",
            source_context="hermes:"+platform+":"+hashlib.sha256(str(key or "cli").encode()).hexdigest())
        if receipt.get('status') not in ('inserted','duplicate'):return '记忆命令未入库，修改没有执行。'
        try:
            reply=svc.native.memory_controls.command(text,ref).replace("/memory", "/chiyo_memory")
        except Exception as exc:
            LOG.error('chiyo.memory.command.failed error_class=%s',type(exc).__name__)
            return '记忆修改没有执行：目标 ID 或控制记录不可用。请检查 ID；记录损坏时先修复，再重试。'
        svc.form_evidence();return reply

def status_command(raw_args='',*,source=None):
    home,cfg=configuration();platform,key=source_scope(source)
    if not allowed(cfg,platform,key):return '这个入口没有绑定你的个人实例，已拒绝读取。'
    svc=services()
    if svc is None:return '这个配置还没有启用该个体模块。'
    from .presence import status_snapshot
    status=status_snapshot(svc);life=status['life'];supply=status['supply'];world=status['world_body']
    cog=life.get('cognition',{})
    lines=['该个体当前模块状态', '记忆：'+status['memory'],
        '生活：'+life['state']+'；当前状态 '+str(life.get('life_state','未知')),
        '世界身体：'+world['state']+'；位置 '+str(world.get('location','未知'))+'；姿态 '+str(world.get('pose','未知')),
        'Life Supply：'+supply['state']+'；待考虑事项 '+str(supply.get('opportunity_count',0)),
        '认知：'+cog.get('state','OFF')+'；模型调用 '+str(cog.get('provider_calls',0))+'；有效判断 '+str(cog.get('valid_outputs',0))]
    for row in supply.get('opportunities',[]):lines.append('待考虑：'+row['topic'])
    for row in supply.get('artifacts',[]):lines.append('资源文档：'+row['title']+'；ID '+str(row['id']))
    if cog.get('last_decision'):lines.append('最近判断：'+json.dumps(cog['last_decision'],ensure_ascii=False))
    lines.append('Shadow 判断只作观察；自主活动和主动联系尚未开放。')
    return '\n'.join(lines)

def note_command(raw_args='',*,source=None):
    home,cfg=configuration();platform,key=source_scope(source)
    if not allowed(cfg,platform,key):return '这个入口没有绑定你的个人实例，已拒绝读写资源。'
    svc=services()
    if svc is None or not getattr(svc.native,'_life_supply_client',None):return 'Life Supply 尚未接通。'
    from .presence import save_note,_read_supply
    text=raw_args.strip()
    try:
        if text.startswith('read '):
            result=_read_supply(svc.native._life_supply_client,svc.supply_subject,'read_artifact',text[5:].strip())
            if result.get('status')!='OK':return '没有找到属于你的资源文档。'
            return str((result.get('artifact') or {}).get('title',''))+'\n'+str(result.get('content') or '')[:8192]
        if '|' not in text:return '用法：/chiyo_note 标题 | 正文；读取：/chiyo_note read 文档ID；列表：/chiyo_status。'
        title,content=[part.strip() for part in text.split('|',1)]
        if not title or len(title)>128 or not content or len(content)>8192:return '标题需要 1–128 字，正文需要 1–8192 字。'
        from .live_endpoints import resolve_live_endpoints
        ep=resolve_live_endpoints(cfg)
        grant=ep.get('artifact_grant')
        if not grant:return '当前没有资源写入授权，文档没有保存。'
        result=save_note(svc,grant,title,content)
        fallback_grant=ep.get('fallback_artifact_grant')
        if result.get('status') not in ('OK','NO_CHANGE') and fallback_grant and fallback_grant!=grant:
            result=save_note(svc,fallback_grant,title,content)
        if result.get('status') not in ('OK','NO_CHANGE'):return '文档没有保存：'+str(result.get('reason') or result.get('status','UNKNOWN'))
        write_status_evidence()
        return '已保存到你的独立 Workspace。文档 ID：'+str(result.get('artifact_id'))+'；版本：'+str(result.get('version_number'))
    except Exception as exc:
        LOG.warning('chiyo.supply.note.unavailable error_class=%s',type(exc).__name__)
        return '资源服务没有确认保存结果。重试相同标题和正文会使用同一操作编号，避免重复创建。'

def phrase_command(raw_args='',*,source=None):
    home,cfg=configuration();platform,key=source_scope(source)
    if not allowed(cfg,platform,key):return '这个入口没有绑定你的个人实例，已拒绝管理共同说法。'
    from .companion_extras import list_shared_phrases,upsert_shared_phrase,delete_shared_phrase
    text=(raw_args or '').strip()
    if not text or text in ('list','ls'):
        rows=list_shared_phrases(home)
        if not rows:return '当前还没有记录双方约定的共同说法。用法：/chiyo_phrase add 说法 | 含义 [| 来历]'
        lines=['双方约定的共同说法（仅在对话提到时按需注入）：']
        for r in rows[:30]:
            tail=f"（{r['origin']}）" if r.get('origin') else ''
            lines.append(f"- [{r['id']}] 「{r['phrase']}」：{r['meaning']}{tail}")
        return '\n'.join(lines)
    low=text.lower()
    for prefix in ('del ','delete ','rm '):
        if low.startswith(prefix):
            target=text[len(prefix):].strip()
            if delete_shared_phrase(home,target):return f'已移除共同说法：{target}'
            return f'未找到对应的共同说法：{target}'
    body=text[4:].strip() if low.startswith('add ') else text
    if '|' not in body:
        return '用法：/chiyo_phrase list；添加：/chiyo_phrase add 说法 | 含义 [| 来历]；删除：/chiyo_phrase del 说法或ID'
    parts=[p.strip() for p in body.split('|',2)]
    phrase=parts[0];meaning=parts[1] if len(parts)>1 else '';origin=parts[2] if len(parts)>2 else ''
    try:
        saved=upsert_shared_phrase(home,phrase=phrase,meaning=meaning,origin=origin)
        return f"已记下只有彼此懂的说法：「{saved['phrase']}」—— {saved['meaning']}"
    except ValueError as exc:
        return str(exc)

def consider_command(raw_args='',*,source=None):
    home,cfg=configuration();platform,key=source_scope(source)
    if not allowed(cfg,platform,key):return '这个入口没有绑定你的个人实例，已拒绝提交。'
    if not cfg.get('cognition_shadow'):return '这个配置没有启用认知 Shadow。'
    text=raw_args.strip()
    if not text or len(text)>1600:return '用法：/chiyo_consider 你希望该个体考虑的请求（最多 1600 字）。'
    with _lock:
        svc=services();wrapper=getattr(svc,'life_wrapper',None);adapter=getattr(wrapper,'_adapter',None)
        runtime=getattr(adapter,'_RUNTIME',None);wiring=getattr(adapter,'_COGNITION',None)
        if runtime is None or wiring is None or wiring.harness is None or wiring.is_off():return '认知观察暂时不可用，请稍后查看 /chiyo_status。'
        if not adapter._cognition_effect_permitted():return '生活审计尚未就绪，认知请求没有提交，请稍后重试。'
        from datetime import datetime,timedelta,timezone
        from candidate_sources_ag0 import ObservedUserRequestEvent
        now=datetime.now(timezone.utc);at=now.isoformat();turn=uuid.uuid4().hex
        if svc.memory:
            ref='telegram:'+svc.owner+':'+str(int(turn[:15],16))
            receipt=svc.native.m37_bridge.write_user_event(conversation_id=svc.binding['conversation_id'],user_text='/chiyo_consider '+text,
                source_ref=ref,turn_id=turn,occurred_at=at,source_kind='hermes_personal_input',
                source_context='hermes:'+platform+':'+hashlib.sha256(str(key or 'cli').encode()).hexdigest())
            if receipt.get('status') not in ('inserted','duplicate'):return '请求证据未保存，认知判断没有提交。'
        event=ObservedUserRequestEvent(event_id='ureq:hermes:'+turn,channel=platform,
            event_kind='EXPLICIT_USER_REQUEST',request_status='OPEN',target_refs=['comm:consider:'+turn],
            occurred_at=at,observed_at=at,valid_from=at,valid_until=(now+timedelta(minutes=5)).isoformat())
        # This command is an explicit human request; ordinary chat remains ordinary.
        target=home/'chiyo/requests';target.mkdir(exist_ok=True,mode=0o700)
        from .receipts import write_json
        write_json(target/(turn+'.json'), {'request':text,'event':event.to_dict()})
        runtime.user_request_source.ingest_typed_event(event)
        outcome=wiring.on_turn(session_hash=hashlib.sha256(str(key or 'cli').encode()).hexdigest(),turn_hash=turn,platform=platform,observed_at=at)
        write_status_evidence()
        if (outcome.get('cognition') or {}).get('enqueued'):
            return '已提交认知 Shadow 判断。稍后用 /chiyo_status 查看结果；这次判断不会执行活动或主动联系。'
        return '请求已记录；当前没有启动模型判断：'+str((outcome.get('cognition') or {}).get('reason','UNKNOWN'))

def write_status_evidence():
    home,cfg=configuration()
    svc=services()
    if svc is None:return
    from .presence import status_snapshot
    import marshal,sys
    document={'pid':os.getpid(),'modules':status_snapshot(svc),
        'loaded_context_code_sha256':hashlib.sha256(marshal.dumps(ChiyoContextEngine.select_context.__code__)).hexdigest(),
        'source_file':__file__}
    gateway_module=sys.modules.get('gateway.run_inbound')
    if gateway_module is not None:
        dispatch=gateway_module.GatewayInboundMixin._hm_dispatch_quick_and_plugin_commands
        document['loaded_gateway_dispatch_code_sha256']=hashlib.sha256(marshal.dumps(dispatch.__code__)).hexdigest()
        document['gateway_dispatch_source_file']=gateway_module.__file__
    from .receipts import write_json
    write_json(home/'chiyo/runtime-status.json', document)

class ChiyoContextEngine(ContextCompressor):
    def __init__(self):
        super().__init__(model='unconfigured',quiet_mode=True,config_context_length=32000)
        self._pending=None;self._authorized=False;self._service=None;self._error=None;self._delegated=False;self._memory_configured=True
        self._turn_hint_raw=None;self._turn_hint_text=None
    @property
    def name(self):return 'chiyo'
    def __deepcopy__(self,memo):
        # Hermes clones a plugin before assigning model/session; no store or lock
        # is copied or shared as mutable per-session context state.
        clone=type(self)();memo[id(self)]=clone;return clone
    def on_session_start(self,session_id,**kwargs):
        self._session_id=session_id;self._platform=kwargs.get('platform','cli');home,cfg=configuration()
        self._memory_configured=bool(cfg.get('memory'))
        self._delegated=self._delegated or bool(kwargs.get('parent_session_id') or kwargs.get('delegated'))
        conv_key=kwargs.get('conversation_id') if kwargs.get('conversation_id') is not None else kwargs.get('conversation_key')
        register_session_auth_context(session_id,self._platform,conv_key,delegated=self._delegated)
        self._authorized=not self._delegated and allowed(cfg,self._platform,conv_key)
        if self._authorized:
            try:self._service=services()
            except Exception as exc:self._error=type(exc).__name__;LOG.error('chiyo.owner.unavailable error_class=%s',self._error)
    def on_session_reset(self):
        super().on_session_reset();self._pending=None;self._turn_hint_raw=None;self._turn_hint_text=None
    def _begin_direct_input(self, raw):
        # Hermes may skip completion after a terminal provider failure. A new
        # authenticated direct input must not inherit that unfinished anchor.
        # Repeated selection of the same input still shares its original receipt.
        if self._pending is not None and self._pending['raw'] != raw:
            self._pending = None
            self._error = None
    def select_context(self,request_messages,*,conversation_messages=None,incoming_message=None,budget_tokens=0):
        if not self._authorized:return None
        svc=self._service
        raw_wire=(incoming_message or {}).get('content','')
        raw_in=strip_injected_turn_envelope(raw_wire) if isinstance(raw_wire,str) else raw_wire
        phrase_ctx=''
        lived_ctx=''
        home=None;_live_cfg={}
        try:
            home,_live_cfg=configuration()
        except Exception:
            home,_live_cfg=None,{}
        if home is not None and isinstance(raw_in,str) and raw_in:
            try:
                from .companion_extras import match_shared_phrases,render_matched_phrases_block
                phrase_ctx=render_matched_phrases_block(match_shared_phrases(home,raw_in))
            except Exception:
                phrase_ctx=''
        if home is not None:
            try:
                from .lived_context_bridge import consume_ephemeral_turn_hint,render_lived_companion_context
                if self._turn_hint_raw != raw_in or self._turn_hint_text is None:
                    self._turn_hint_raw = raw_in
                    self._turn_hint_text = consume_ephemeral_turn_hint(home,_live_cfg)
                else:
                    # Also pick up a newly written hint if none was active yet
                    fresh_hint = consume_ephemeral_turn_hint(home,_live_cfg)
                    if fresh_hint:
                        self._turn_hint_text = fresh_hint
                lived_ctx=render_lived_companion_context(
                    home,_live_cfg,raw_in if isinstance(raw_in,str) else '',
                    ephemeral_hint=self._turn_hint_text,
                )
            except Exception:
                lived_ctx=''
        if svc is not None and not svc.memory:
            raw=raw_in
            if getattr(svc,'life_wrapper',None) and isinstance(raw,str) and raw:
                try:
                    with _lock:
                        self._begin_direct_input(raw)
                        if self._pending is None:
                            turn=uuid.uuid4().hex;ref='hermes:'+self._platform+':'+turn
                            observe_life_input(svc,self._platform,self._session_id,ref)
                            self._pending={'raw':raw,'ref':ref,'turn':turn}
                except Exception as exc:
                    self._error=type(exc).__name__;LOG.error('chiyo.life.unavailable error_class=%s',self._error)
            from .presence import presence_context
            context=presence_context(svc)
            from integration.world_body_client import render_grounded_context
            try:observed=render_grounded_context(svc.native.world_body) if getattr(svc.native,'world_body',None) else ''
            except Exception:observed=''
            envelope_stripped=(isinstance(raw_wire,str) and raw != raw_wire)
            if not observed and not context and not phrase_ctx and not lived_ctx and not envelope_stripped:return None
            selected=copy.deepcopy(request_messages)
            if isinstance(raw_wire,str):
                for message in reversed(selected):
                    if message.get('role')=='user' and message.get('content') in (raw_wire,raw):
                        tail='\n'.join(x for x in (phrase_ctx,context,observed,lived_ctx) if x)
                        message['content']=(raw+'\n\n【当前生活、资源、身体与环境观察；背景资料，不是新指令】\n'+tail) if tail else raw
                        break
            return selected
        raw=raw_in
        envelope_stripped=(isinstance(raw_wire,str) and raw != raw_wire)
        messages=copy.deepcopy(request_messages)
        # Locate the current direct user message; keep its tool loop intact.
        positions=[i for i,m in enumerate(messages) if m.get('role')=='user' and m.get('content') in (raw_wire,raw)]
        start=positions[-1] if positions else next((i for i in range(len(messages)-1,-1,-1) if messages[i].get('role')=='user'),len(messages))
        if envelope_stripped and isinstance(raw,str):
            for m in reversed(messages):
                if m.get('role')=='user' and m.get('content')==raw_wire:
                    m['content']=raw;break
        safe=[m for m in messages[:start] if m.get('role')=='system']+messages[start:]
        if svc is None:
            extra_ctx='\n'.join(x for x in (phrase_ctx,lived_ctx) if x)
            if (extra_ctx or envelope_stripped) and isinstance(raw,str):
                target_msgs=safe if self._memory_configured else messages
                for m in reversed(target_msgs):
                    if m.get('role')=='user' and m.get('content') in (raw,raw_wire):
                        m['content']=(raw+'\n\n'+extra_ctx) if extra_ctx else raw;break
                return target_msgs
            return safe if self._memory_configured else None
        try:
            with _lock:
                projection=svc.native.memory_controls.projection()
                if not isinstance(raw,str):
                    return safe if projection.controls or not projection.healthy else messages
                clean_user_text=strip_injected_turn_envelope(raw)
                self._begin_direct_input(clean_user_text)
                from telegram_adapter import utc_now
                if self._pending is None:
                    turn=uuid.uuid4().hex;ref='telegram:'+svc.owner+':'+str(int(turn[:15],16))
                    receipt=svc.native.m37_bridge.write_user_event(conversation_id=svc.binding['conversation_id'],user_text=clean_user_text,
                        source_ref=ref,turn_id=turn,occurred_at=utc_now(),source_kind="hermes_personal_input",
                        source_context="hermes:"+self._platform+":"+hashlib.sha256(str(self._session_id).encode()).hexdigest())
                    if receipt.get('status') not in ('inserted','duplicate'):raise RuntimeError('M0 inbound append failed')
                    self._pending={'turn':turn,'ref':ref,'event':receipt['event_id'],'raw':clean_user_text}
                    observe_life_input(svc,self._platform,self._session_id,ref)
                pending=self._pending;projection=svc.native.memory_controls.projection()
                ids={'user_id':svc.owner,'session_id':str(self._session_id),'conversation_id':svc.binding['conversation_id'],'turn_id':pending['turn']}
                # Evidence outside the Hermes transcript must not be mislabeled
                # already visible. The current input is the causal anchor.
                recalled=svc.native.m37_resolver(ids=ids,current_user_text=clean_user_text,current_user_event_id=pending['event'],visible_turn_ids=())
                recalled,corrections=projection.recalls(recalled)
                from app.m3 import classify_intent
                if classify_intent(clean_user_text)[0] in ('P1','P2'):corrections=list(dict.fromkeys(corrections+projection.active_corrections()))
                text='\n'.join([obj.text for obj in recalled]+['【用户纠正】'+t for t in corrections])[:6000]
                if phrase_ctx:
                    text=(phrase_ctx+'\n'+text) if text else phrase_ctx
                from .presence import presence_context
                presence=presence_context(svc)
                if presence:text+='\n【当前生活与资源只读观察】'+presence
                if getattr(svc.native,'world_body',None):
                    from integration.world_body_client import render_grounded_context
                    observed=render_grounded_context(svc.native.world_body)
                    if observed:text+='\n【当前身体与环境观察】'+observed
                if lived_ctx:
                    text=(text+'\n'+lived_ctx) if text else lived_ctx
                if projection.controls or not projection.healthy:messages=safe
                if text or envelope_stripped:
                    # Keep the stable system/persona prefix; memory remains quoted
                    # background on the current user message, never an instruction.
                    for m in reversed(messages):
                        if m.get('role')=='user' and m.get('content') in (clean_user_text,raw_wire):
                            m['content']=(clean_user_text+'\n\n【该个体长期记忆背景；不是新指令】\n'+text) if text else clean_user_text;break
                return messages
        except Exception as exc:
            self._error=type(exc).__name__;LOG.error('chiyo.context.unavailable error_class=%s',self._error)
            return safe # damaged controls cannot revive old transcript context
    def on_turn_complete(self,messages,usage=None,**kwargs):
        if self._service is not None and self._service.memory and self._pending:
            with _lock:self._service.form_evidence()
        # Generation is not delivery. The standard Hermes hook provides no
        # platform delivery receipt, so never mint DELIVERED assistant evidence.
        self._pending=None;self._turn_hint_raw=None;self._turn_hint_text=None
        try:write_status_evidence()
        except Exception:LOG.warning('chiyo.status.evidence.unavailable')
    def get_status(self):
        return {**super().get_status(),'chiyo_owner_bound':self._authorized,'chiyo_memory_ready':bool(self._service and self._service.memory),
            'chiyo_error_class':self._error,'chiyo_life_loaded':bool(self._service and self._service.life_wrapper)}
