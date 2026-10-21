"""把已设计的剧组事实编译为执行指令，不生成剧情、不自动批准审美。"""
import json

FIELDS = {
    'screenwriting': ('编剧', [('objective', '人物要完成'), ('obstacle', '阻力'), ('turn', '本镜转折'), ('audience_gain', '新增信息')]),
    'directing': ('导演', [('performance_beat', '表演节拍'), ('subtext', '潜台词'), ('staging_intent', '观看目的')]),
    'production_design': ('场景美术', [('foreground_story', '前景'), ('midground_story', '中景'), ('background_story', '后景'), ('hero_prop_state', '关键道具状态'), ('material_response', '材质反应'), ('color_accent', '色彩落点')]),
    'character_design': ('人物美术', [('silhouette_state', '剪影'), ('wardrobe_state', '服装锁与状态'), ('grooming_state', '妆发连续性')]),
    'blocking': ('演员调度', [('start_mark', '起点'), ('action_path', '行动路径'), ('end_mark', '终点'), ('gaze_target', '视线目标'), ('screen_direction', '屏幕方向')]),
    'lighting': ('灯光', [('source_used', '实际来源'), ('key_direction', '入射方向'), ('key_quality', '光质'), ('fill_strategy', '补光与保留暗部'), ('exposure_target', '曝光保护'), ('shadow_behavior', '阴影变化'), ('continuity_anchor', '连续性锚点')]),
    'cinematography': ('摄影', [('camera_position', '物理机位'), ('shot_size', '景别'), ('subject_placement', '画幅落点'), ('focus_target', '焦点'), ('depth_strategy', '层次与遮挡'), ('camera_movement', '运动'), ('movement_motivation', '运动原因')]),
    'editing': ('剪辑', [('editorial_role', '镜头职责'), ('incoming_relation', '接入关系'), ('outgoing_relation', '接出关系'), ('music_cue', '音乐触发'), ('cut_motivation', '切点理由'), ('duration_intent', '保留时长的目的')]),
}


def execution_lines(crew):
    lines = []
    for department, (title, fields) in FIELDS.items():
        facts = crew.get(department, {})
        values = []
        for key, label in fields:
            value = facts.get(key)
            if value in (None, '', [], {}): continue
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            values.append(f'{label}：{text}')
        if values: lines.append(title + '｜' + '；'.join(values))
    return lines


def scene_execution(master):
    """美术和灯光不能只存在附带JSON里，须进入实际母版提示词。"""
    lines = [master['master_prompt']]
    for key, title in [('art_direction', '场景美术执行'), ('lighting_plan', '灯光执行')]:
        facts = master.get(key, {})
        if facts:
            lines.append(title + '：' + '；'.join(f'{k}={v}' for k, v in facts.items()))
    return '\n'.join(lines)


def cut_rehearsal(shots):
    cuts = []
    for previous, following in zip(shots, shots[1:]):
        a, b = previous['crew'], following['crew']
        cuts.append({'from': previous['id'], 'to': following['id'],
            'cut_time_sec': following['window']['start'],
            'relation': {'outgoing': a['editing']['outgoing_relation'], 'incoming': b['editing']['incoming_relation']},
            'music_cue': b['editing']['music_cue'],
            'attention_transfer': {'from': previous['visual_center'], 'to': following['visual_center']},
            'actor_state': {'previous_end': a['blocking']['end_mark'], 'next_start': b['blocking']['start_mark']},
            'information_gain': b['screenwriting']['audience_gain'],
            'same_scene': previous['scene_id'] == following['scene_id'],
            'review_question': '切点是否推动信息、动作或情绪？跨场/时间跳跃不强行接续同一人物路径。',
            'watch_verified': False})
    return cuts


def validate_motion_package(prompt, motion, source_sha256):
    direction = prompt.get('direction', {})
    expected = {'shot_id': prompt['meta']['shot_id'], 'window': prompt['meta']['window'],
                'crew': direction.get('crew', {}), 'crew_execution': execution_lines(direction.get('crew', {})),
                'director_method': direction.get('method', {}), 'scene_master': direction.get('scene_master', {}),
                'continuity': prompt.get('continuity', {}), 'visual_rules': prompt.get('visual_rules', []),
                'motion_design': prompt.get('motion_design', {}), 'subject_lock': prompt.get('subject', ''),
                'audio_policy': prompt.get('audio_policy', ''), 'source_prompt_sha256': source_sha256}
    return [f'视频运动包丢失或改变已批准事实: {key}' for key, value in expected.items()
            if key not in motion or motion[key] != value]
