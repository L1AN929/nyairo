"""General plugin: no global/core module monkey patching."""

#: Cache-safe system prompt section. Registered only when this profile actually
#: has a World/Body service; it tells the model that an explicit request to go
#: somewhere is an ACTION (call the tool), not a sentence to answer politely.
_WORLD_MOVE_GUIDANCE=(
    "【移动与出门：world_move 工具】\n"
    "如果本次会话的可用工具里有 world_move，那么用户让你出门、走过去、去街上/便利店/公园/学校/"
    "某个地方时，你要调用 world_move（location 传地点名或 location_id）再回话；"
    "只口头答应、只描述画面不会产生任何位移。\n"
    "- 真实步行计时开启时，一次调用只出发当前一段：到达中转点后要再调用一次最终目的地才会继续走。\n"
    "- 位置、在途状态和剩余时间以工具返回为准，不要自行编造到达或声称已经在路上。\n"
    "如果本次会话没有 world_move 工具，就如实说现在不能移动，不要假装已经出门。"
)


_PHONE_AND_WORLD_GUIDANCE=(
    "【凪浦世界与随身手机：chiyo_phone / world_move 工具】\n"
    "如果本次会话的可用工具里有 chiyo_phone，那么当用户让你去某个房间或街区地点、坐下/站起/"
    "躺下休息、开关灯具、记随笔备忘、翻阅/保存共读书签或批注、购买/使用物品、写日记时，"
    "这是需要真实执行的动作——请先调用 chiyo_phone 完成操作，再根据真实回执自然回话，不要只口头假装做过：\n"
    "- 世界与房间动作：不确定目标 destination_location_id 或灯具/物品 object_id 时，先调 "
    "chiyo_phone(action=\"world\") 查看可达地点与可用动作，再调 chiyo_phone(action=\"world_action\", "
    "world_action=\"MOVE\"|\"POSE\"|\"REST\"|\"TOGGLE_FIXTURE\"|\"PICK_UP\"|\"PLACE\"|\"ARRANGE\", "
    "params={...}, operation_key=\"...\")。注意：REST 小憩需先处于 seated/lying 姿态（sleep_like 需 lying）。\n"
    "- 随笔/共读/商店/日记：可用 note_save、reading_material / reading_position / reading_annotate、"
    "shop / store / purchase / store_purchase / use、diary_write（每次写操作给唯一 operation_key）。\n"
    "- 所有位置、余额、灯光与物品变化一律以工具返回的 status（applied / rejected）为准，绝不编造结果。"
)


def _world_move_guidance(session_info=None):
    """Guidance text, or '' when this profile has no World/Body or Phone service configured."""
    try:
        import os
        from chiyo_bundle.hermes_plugin import configuration
        _unused,settings=configuration()
        phone_cfg=settings.get('phone') if isinstance(settings,dict) else None
        if isinstance(phone_cfg,dict) and phone_cfg.get('enabled') is True:
            return _PHONE_AND_WORLD_GUIDANCE
        if not (settings.get('world_body_socket') or os.environ.get('WORLD_BODY_SOCKET')):
            return ''
    except Exception:
        return ''
    return _WORLD_MOVE_GUIDANCE


def register(ctx):
    import sys
    from chiyo_bundle.hermes_plugin import ChiyoContextEngine,memory_command,status_command,consider_command,note_command,close_services,configure_host_llm,memory_tool_gate,write_status_evidence
    phrase_command = getattr(sys.modules.get('chiyo_bundle.hermes_plugin'), 'phrase_command', None)
    configure_host_llm(ctx.llm)
    ctx.register_context_engine(ChiyoContextEngine())
    try:
        if hasattr(ctx,'register_system_prompt_section'):
            # Frozen per session by the host; the wording is safe both with and
            # without the toolset enabled, so a platform that keeps chiyo_world
            # off still gets "say you cannot move", never a fabricated walk.
            ctx.register_system_prompt_section('chiyo.world-move',_world_move_guidance,
                                               position='after_memory',max_chars=1200)
    except Exception:
        import logging
        logging.getLogger('chiyo.hermes').warning('chiyo.world_move.prompt_section.unavailable', exc_info=True)
    for command in ('chiyo_memory','chiyo-memory'):
        ctx.register_command(command,memory_command,description='List, correct or logically forget personal memories',args_hint='list | delete ID | correct ID TEXT')
    ctx.register_hook("pre_tool_call",memory_tool_gate)
    try:
        from chiyo_bundle.world_move_tool import MOVE_TOOL_SCHEMA, move_handler
        ctx.register_tool(name='world_move', toolset='chiyo_world',
                          schema=MOVE_TOOL_SCHEMA, handler=move_handler,
                          description='Move in the virtual world with real walking time', emoji='🚶')
    except Exception:
        import logging
        logging.getLogger('chiyo.hermes').warning('chiyo.world_move.registration.unavailable', exc_info=True)
    try:
        # v0.4: her own phone.  The tool is a thin loopback client of the local phone service and
        # is gated by the profile's own explicit `phone` section (off by default).
        from chiyo_bundle.phone_tool import PHONE_TOOL_SCHEMA, phone_handler
        ctx.register_tool(name='chiyo_phone', toolset='chiyo_world',
                          schema=PHONE_TOOL_SCHEMA, handler=phone_handler,
                          description='Open her own local virtual phone: wallet, notes, references, '
                                      'reading, virtual store, owned items and explicit actions',
                          emoji='📱')
    except Exception:
        import logging
        logging.getLogger('chiyo.hermes').warning('chiyo.phone.registration.unavailable', exc_info=True)
    ctx.register_command('chiyo_status',status_command,description='Read current life, world/body, resource and cognition status')
    ctx.register_command('chiyo_consider',consider_command,description='Submit an explicit request for cognition Shadow observation',args_hint='REQUEST')
    ctx.register_command('chiyo_note',note_command,description='Save or read a personal ManagedArtifact',args_hint='TITLE | CONTENT ; read ID')
    if callable(phrase_command):
        for pcmd in ('chiyo_phrase','chiyo-phrase'):
            ctx.register_command(pcmd,phrase_command,description='Manage shared private phrases understood between you and your companion',args_hint='list | add PHRASE | MEANING [| ORIGIN] | del PHRASE')
    ctx.on_unload(close_services)
    try:write_status_evidence()
    except Exception:
        import logging
        logging.getLogger('chiyo.hermes').warning('chiyo.startup.status.unavailable')
