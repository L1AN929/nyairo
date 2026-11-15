"""General plugin: no global/core module monkey patching."""

from chiyo_bundle.phone_tool import (
    LIFE_UPDATE_PRIVATE_SURFACES,
    LIFE_UPDATE_SURFACES,
)

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
    "【随身手机与世界：chiyo_phone / world_move】\n"
    "若工具含 chiyo_phone，涉及出门移动、坐卧开关灯、随笔书签商店日记，或读写穿搭声景、拒绝锁、"
    "话头心事、账本病休、院子手作、回忆与共同认知或亲密边界时，先调 chiyo_phone 拿回执再答，勿假装：\n"
    "- 基础：chiyo_phone(action=\"world\") 看地点，world_action"
    "（MOVE/POSE/REST/TOGGLE_FIXTURE/PICK_UP/PLACE/ARRANGE/TOILET，带 operation_key）；"
    "另有 note_save、reading_material/reading_position/reading_annotate、shop/store_purchase/use、diary_write、work_context。\n"
    "- 只读：desk_presence、desk_notes、home_window、home_keepsakes、"
    "home_letters、home_workbench、home_curios、home_trio、home_reunion_anchor、home_recipes、"
    "home_tentative_plans、wardrobe、pocket_lists、outing_bench_bookstore、outing_routes_market、"
    "outing_gallery_blueprint、longterm_habits、longterm_gifts_lantern、longterm_resume_hub、"
    "body_somatic、body_garment_window、body_care、body_sensory、character_pack、autonomy_rhythm、"
    "emotional_tension、stance_containment、adult_intimacy、v03_clock、v03_ecology、v03_embodied_social、"
    "v04_neighborhood、v04_builder_trylive、v04_elastic_play、v02_agency_depth、v02_crossmodal、"
    "v02_runtime_migration、v02_recall_projection、v02_common_ground。\n"
    "- 生活写：chiyo_phone(action=\"phone_life_update\", target_surface=\"<面板>\", "
    "payload={\"action\": \"...\"}, operation_key=\"...\")，仅改自身侧，以 status（applied/rejected）为准。"
)


def _world_move_guidance(session_info=None):
    """Guidance text, or '' when this profile has no World/Body or Phone service configured."""
    try:
        import os
        from chiyo_bundle.hermes_plugin import configuration
        from chiyo_bundle.live_endpoints import is_world_move_explicitly_disabled, resolve_live_endpoints
        _unused,settings=configuration()
        phone_cfg=settings.get('phone') if isinstance(settings,dict) else None
        if isinstance(phone_cfg,dict) and phone_cfg.get('enabled') is True:
            return _PHONE_AND_WORLD_GUIDANCE
        if is_world_move_explicitly_disabled(settings):
            return ''
        resolved_world=resolve_live_endpoints(settings).get('world_socket') if isinstance(settings,dict) else None
        if not (settings.get('world_body_socket') or resolved_world or os.environ.get('WORLD_BODY_SOCKET')):
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
                                               position='after_memory',max_chars=1800)
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
                          description='Open own local virtual phone: world actions, notes, reading, store, '
                                      '0.2/0.3 home & somatic & autonomy & ecology views, and phone_life_update',
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
