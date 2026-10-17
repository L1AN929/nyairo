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


def _world_move_guidance(session_info=None):
    """Guidance text, or '' when this profile has no World/Body service configured."""
    try:
        import os
        from chiyo_bundle.hermes_plugin import configuration
        _unused,settings=configuration()
        if not (settings.get('world_body_socket') or os.environ.get('WORLD_BODY_SOCKET')):
            return ''
    except Exception:
        return ''
    return _WORLD_MOVE_GUIDANCE


def register(ctx):
    from chiyo_bundle.hermes_plugin import ChiyoContextEngine,memory_command,status_command,consider_command,note_command,close_services,configure_host_llm,memory_tool_gate,write_status_evidence
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
    ctx.register_command('chiyo_status',status_command,description='Read current life, world/body, resource and cognition status')
    ctx.register_command('chiyo_consider',consider_command,description='Submit an explicit request for cognition Shadow observation',args_hint='REQUEST')
    ctx.register_command('chiyo_note',note_command,description='Save or read a personal ManagedArtifact',args_hint='TITLE | CONTENT ; read ID')
    ctx.on_unload(close_services)
    try:write_status_evidence()
    except Exception:
        import logging
        logging.getLogger('chiyo.hermes').warning('chiyo.startup.status.unavailable')
