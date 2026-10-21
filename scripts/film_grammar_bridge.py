"""歌曲/故事→有来源的镜头组织建议→导演交接；不凭标注猜测电影故事或自动批准。"""
import argparse
import copy
import json
import math
import uuid
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
try:
    from .pipeline_contracts import digest, atomic_batch, number, require_no_transaction
except ImportError:
    from pipeline_contracts import digest, atomic_batch, number, require_no_transaction

# 以下是本项目编写的组织方法，不冒称从某部电影中自动识别到的导演意图。
METHODS = {
    'exchange': {'name':'交锋与反应', 'keywords':['对抗','交锋','反击','回应','争','battle','对手'],
                 'roles':['建立双方关系','主导者提出信息','对方接收与反应','变化后的双方关系'],
                 'instruction':'先让观众读懂双方地理；交替观看发起者与接收者，反应必须发生在触发之后。'},
    'reveal': {'name':'信息揭示', 'keywords':['发现','揭示','秘密','看见','揭开','揭晓'],
              'roles':['限制观众所知','人物察觉线索','揭示信息对象','人物反应及新目标'],
              'instruction':'保留信息→察觉→对象→反应；用视线和具体线索连接，不能仅切换景别。'},
    'action': {'name':'动作与后果', 'keywords':['拿','推','走','搓','转','开','合','触发','击'],
               'roles':['交代动作空间','动作起因','动作执行细节','动作后果与接收者'],
               'instruction':'明确动作起止、手与道具状态；在动作可承接处切换视点，不回放已完成动作。'},
    'performance': {'name':'表演覆盖与观众关系', 'keywords':['唱','演','节奏','人声','副歌','rap'],
                    'roles':['建立表演空间','主唱表达','动作或观众反馈','重回变化后的主唱'],
                    'instruction':'主唱镜服从真实演唱窗口；反馈镜承担新信息，不机械按歌词逐行轮换正面特写。'},
}
SIZE_MAP={'extreme long shot':'远景','long shot':'远景','medium long shot':'中远景','medium shot':'中景',
          'medium closeup':'近景','closeup':'特写','extreme closeup':'大特写'}
NS={'a':'http://experience.univ-lyon1.fr/advene/ns'}


def encoded(value):
    return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def analyze(video, output):
    """实际解码切点；仅记录剪辑边界，不宣称检测到机位、反应或导演意图。"""
    from scenedetect import detect, AdaptiveDetector
    import importlib.metadata
    video,output=Path(video).resolve(),Path(output).resolve()
    before=digest(video)
    scenes=detect(str(video),AdaptiveDetector())
    if not scenes: raise ValueError('没有取得有效视频区间')
    rows=[{'start_frame':a.get_frames(),'end_frame':b.get_frames(),
           'start_sec':a.get_seconds(),'end_sec':b.get_seconds()} for a,b in scenes]
    result={'schema_version':1,'kind':'local_cut_analysis','source_path':str(video),'source_sha256':before,
            'engine':'PySceneDetect','engine_version':importlib.metadata.version('scenedetect'),
            'detector':'AdaptiveDetector','shots':rows,'media_observed':False,
            'scope':'actual_decoded_cut_boundaries_not_story_or_camera_interpretation'}
    atomic_batch({output:encoded(result)},expected={video:before,output:None})
    return result


def index(ada_repo, output, *, cut_analysis=None):
    repo,output=Path(ada_repo).resolve(),Path(output).resolve()
    packages=sorted((repo/'annotations/manual-annotations_advene_azp_files').rglob('*.azp'))
    if not packages: raise ValueError('没有ProjectAdA人工标注包')
    expected,passages={},[]
    license_path=repo/'License'
    if license_path.is_file(): expected[license_path]=digest(license_path)
    for package in packages:
        expected[package]=digest(package)
        with zipfile.ZipFile(package) as archive:
            info=archive.getinfo('content.xml')
            if info.file_size>32*1024*1024: raise ValueError('标注XML过大')
            raw=archive.read(info)
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper(): raise ValueError('禁止XML实体')
        annotations=[]
        for node in ET.fromstring(raw).findall('.//a:annotation',NS):
            fragment=node.find('a:millisecond-fragment',NS);content=node.find('a:content',NS)
            kind=node.get('type','').lstrip('#')
            # 不导入对白或字幕；只保留镜头边界、景别与运镜标注。
            if fragment is None or kind not in {'Shot','FieldSize','CameraMovement'}: continue
            a,b=int(fragment.get('begin')),int(fragment.get('end'))
            if a<0 or b<=a: raise ValueError('标注窗口非法')
            annotations.append({'id':node.get('id'),'type':kind,'start_ms':a,'end_ms':b,'value':content.text or '' if content is not None else ''})
        shots=sorted([r for r in annotations if r['type']=='Shot'],key=lambda r:r['start_ms'])
        # 候选段落只在连续镜头间构造；不把标注缺口当连续动作。
        for i in range(0,len(shots),4):
            group=shots[i:i+4]
            if len(group)<2 or any(a['end_ms']!=b['start_ms'] for a,b in zip(group,group[1:])): continue
            begin,end=group[0]['start_ms'],group[-1]['end_ms']
            fields=[r for r in annotations if r['type']!='Shot' and r['start_ms']<end and r['end_ms']>begin]
            sizes=[]
            for shot in group:
                values=[r['value'] for r in fields if r['type']=='FieldSize' and r['start_ms']<shot['end_ms'] and r['end_ms']>shot['start_ms']]
                sizes.append(values)
            passages.append({'id':f'{package.stem}:{begin}-{end}','source_path':str(package),'source_sha256':expected[package],
                             'source_kind':'ProjectAdA_annotation','license':'CC BY-SA 3.0 (annotations only)',
                             'range_ms':[begin,end],'shot_count':len(group),'mean_shot_sec':(end-begin)/1000/len(group),
                             'size_sequence':sizes,'annotations':group+fields,
                             'semantic_story_verified':False,'media_observed':False})
    if not passages: raise ValueError('没有可用的连续标注镜头段落')
    analysis=None
    if cut_analysis:
        path=Path(cut_analysis).resolve();expected[path]=digest(path);analysis=load(path)
        source=Path(analysis['source_path']);expected[source]=analysis['source_sha256']
        if analysis.get('kind')!='local_cut_analysis' or digest(source)!=analysis['source_sha256']:
            raise ValueError('本地切点分析来源失效')
    result={'schema_version':1,'passages':passages,'local_cut_analysis':analysis,
            'source_versions':{str(p):v for p,v in expected.items()},'artistic_approval':False}
    atomic_batch({output:encoded(result)},expected={**expected,output:None})
    return result


def choose_method(text):
    scores={key:sum(word.lower() in text.lower() for word in value['keywords'])*(3 if key in {'exchange','reveal'} else 1) for key,value in METHODS.items()}
    winner=max(scores,key=scores.get)
    return winner if scores[winner] else 'performance', scores


def organization_text(row):
    if not row: return ''
    return ('镜头组织：'+row['editorial_role_proposal']+'；'+row['organization_instruction']
            +'；承接状态：'+json.dumps(row['action_start'],ensure_ascii=False)+' → '+json.dumps(row['action_end'],ensure_ascii=False)
            +'；人物起止：'+json.dumps([row['start_positions'],row['end_positions']],ensure_ascii=False)
            +'；空间轴侧：'+str(row['axis_side'])+'；参考转译：'+row['source_application']
            +'；'+ '；'.join(row['generation_constraints']))


def continuity_errors(shots):
    errors=[]
    for i,row in enumerate(shots):
        if not row.get('axis_side') or row['axis_side']=='pending': errors.append(row['shot_id']+': 缺少实际机位轴侧')
        for field in ('start_positions','end_positions'):
            if not isinstance(row.get(field),dict) or not row[field]: errors.append(row['shot_id']+': 缺少人物起止位置')
            else:
                for point in row[field].values():
                    if not isinstance(point,list) or len(point)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in point):
                        errors.append(row['shot_id']+': 人物坐标无效')
        if not row.get('action_start') or not row.get('action_end'): errors.append(row['shot_id']+': 缺少可见动作起止状态')
        if i:
            previous=shots[i-1]
            same_space=previous['scene_id']==row['scene_id']
            relation=row.get('incoming_relation','continuous')
            if relation not in {'continuous','ellipsis','scene_change'}: errors.append(row['shot_id']+': 衔接类型无效')
            if same_space and relation=='continuous':
                for actor in set(previous.get('end_positions',{}))&set(row.get('start_positions',{})):
                    if previous['end_positions'][actor]!=row['start_positions'][actor]: errors.append(row['shot_id']+': 人物位置跳变 '+actor)
                if previous.get('action_end')!=row.get('action_start'): errors.append(row['shot_id']+': 动作状态不承接')
                if previous.get('axis_side')!=row.get('axis_side') and not row.get('axis_bridge'):
                    errors.append(row['shot_id']+': 越轴缺少桥接设计')
            elif not str(row.get('transition_reason','')).strip(): errors.append(row['shot_id']+': 跳时/跨场缺少可见过渡依据')
    return errors


def binding_errors(plan, story, edit, sequence, library):
    errors=[];sources={r['id']:r for r in library['passages']}
    if plan.get('artistic_approval') is not False: errors.append('镜头组织交接不能自动批准艺术质量')
    smap={r['id']:r for r in story['shots']};emap={r['id']:r for r in edit['shots']};qmap={r['id']:r for r in sequence['shots']}
    if [r['shot_id'] for r in plan['shots']]!=[r['id'] for r in edit['shots']]: return ['电影组织镜头集合不一致']
    for row in plan['shots']:
        sid=row['shot_id'];s,e,q=smap[sid],emap[sid],qmap[sid]
        evidence=row.get('source_evidence',{})
        if sources.get(evidence.get('id'))!=evidence: errors.append(sid+': 来源段落不是当前索引中的真实标注')
        if row.get('method') not in METHODS or not str(row.get('source_application','')).strip(): errors.append(sid+': 缺少组织方法及参考转译依据')
        if (row.get('scene_id')!=e['scene_id'] or row.get('window')!=q['window']
                or row.get('music_refs')!={'start':e['start_ref'],'end':e['end_ref']}
                or row.get('story_goal')!=s['story_intent']
                or row.get('current_action')!=s['current_action']
                or row.get('action_start')!=s.get('state_before') or row.get('action_end')!=s.get('state_after')):
            errors.append(sid+': 音乐/动作事实与导演设计不一致，先修订设计再检索')
        blocking=s.get('crew',{}).get('blocking',{});subject=blocking.get('subject_id','protagonist')
        cast=blocking.get('actor_positions',{})
        expected_start=cast if isinstance(cast,dict) and cast else {subject:q['actor_start_xy']}
        expected_end=blocking.get('actor_end_positions',expected_start) if cast else {subject:q['actor_end_xy']}
        if row.get('start_positions')!=expected_start or row.get('end_positions')!=expected_end:
            errors.append(sid+': 组织方案坐标与实际导演调度不一致')
    return errors


def propose(project, index_path, output, *, beats_path=None):
    project,index_path,output=Path(project).resolve(),Path(index_path).resolve(),Path(output).resolve()
    paths=[project/'state'/f'{name}.json' for name in ('timeline','music_structure','story_plan','visual_edit_plan','sequence_direction')]
    versions={p:digest(p) for p in paths+[index_path]}
    timeline,music,story,edit,sequence=[load(p) for p in paths]
    library=load(index_path)
    versions.update({Path(p):v for p,v in library['source_versions'].items()})
    if any(digest(p)!=v for p,v in versions.items()): raise ValueError('电影参考或当前设计已变化')
    smap={r['id']:r for r in story['shots']};qmap={r['id']:r for r in sequence['shots']}
    if [r['id'] for r in edit['shots']]!=[r['id'] for r in sequence['shots']]: raise ValueError('导演序列与剪辑镜头不一致')
    drafts=[]
    # 在同场景同歌曲段落内先选择共同组织方法，不对每镜轮换不同套路。
    groups=[]
    for item in edit['shots']:
        key=(item['scene_id'],item.get('segment',''))
        if not groups or groups[-1]['key']!=key: groups.append({'key':key,'items':[]})
        groups[-1]['items'].append(item)
    if beats_path:
        beats_path=Path(beats_path).resolve();versions[beats_path]=digest(beats_path)
        beats=load(beats_path)['beats'];by_id={r['id']:r for r in edit['shots']}
        if [sid for beat in beats for sid in beat['shot_ids']]!=[r['id'] for r in edit['shots']]:
            raise ValueError('叙事节拍必须按时间顺序恰好覆盖全部镜头')
        groups=[]
        for beat in beats:
            if not str(beat.get('story_goal','')).strip(): raise ValueError('叙事节拍缺少故事目标')
            if beat.get('method') is not None and beat['method'] not in METHODS: raise ValueError('未知镜头组织方法')
            groups.append({'key':beat['id'],'items':[by_id[sid] for sid in beat['shot_ids']],
                           'story_goal':beat['story_goal'],'selected_method':beat.get('method')})
    organization={}
    for group in groups:
        text=group.get('story_goal','')+'；'+'；'.join(str(smap[item['id']].get(k,'')) for item in group['items'] for k in ('story_intent','story_beat','current_action','obstacle'))
        method,scores=choose_method(text)
        method=group.get('selected_method') or method
        for j,item in enumerate(group['items']):
            role=METHODS[method]['roles'][0 if j==0 else -1 if j==len(group['items'])-1 else min(j,2)]
            if len(group['items'])==1: role=smap[item['id']].get('audience_information') or '完成本段故事变化，保持必要表演信息'
            organization[item['id']]=(method,scores,role,str(group['key']))
    for i,item in enumerate(edit['shots']):
        s,q=smap[item['id']],qmap[item['id']]
        text='；'.join(str(s.get(k,'')) for k in ('story_intent','story_beat','current_action','obstacle'))
        method,scores,role,sequence_id=organization[item['id']]
        duration=q['window'][1]-q['window'][0]
        def rank(p):
            variation=len({v for values in p['size_sequence'] for v in values})
            # 匹配的是标注中的覆盖变化和节奏，不把这分数当剧情相似度。
            return (abs(p['mean_shot_sec']-duration)+(.5 if method in {'exchange','reveal','action'} and variation<2 else 0),p['id'])
        ranked=sorted(library['passages'],key=rank)[:3]
        chosen=ranked[0]
        blocking=s.get('crew',{}).get('blocking',{})
        subject=blocking.get('subject_id','protagonist')
        positions={subject:q['actor_start_xy']};ends={subject:q['actor_end_xy']}
        cast=blocking.get('actor_positions',{})
        if isinstance(cast,dict) and cast:
            positions=copy.deepcopy(cast);ends=copy.deepcopy(blocking.get('actor_end_positions',cast))
        drafts.append({'shot_id':s['id'],'scene_id':item['scene_id'],'window':q['window'],
                       'method':method,'editorial_role_proposal':role,'selection_scores':scores,
                       'organization_sequence_id':sequence_id,
                       'selection_scope':'heuristic_story_goal_match_needs_director_selection',
                       'story_goal':s['story_intent'],'current_action':s['current_action'],
                       'music_refs':{'start':item['start_ref'],'end':item['end_ref']},
                       'source_candidates':[{'id':p['id'],'mean_shot_sec':p['mean_shot_sec'],'size_sequence':p['size_sequence']} for p in ranked],
                       'source_evidence':copy.deepcopy(chosen),
                       'source_application':'仅借鉴实际标注的景别变化与镜长；叙事职责由本项目提案，不声称原片具有同一剧情',
                       'organization_instruction':METHODS[method]['instruction'],
                       'start_positions':positions,'end_positions':ends,
                       'axis_side':s.get('crew',{}).get('cinematography',{}).get('camera_side','pending'),
                       'action_start':s.get('state_before',''),'action_end':s.get('state_after',''),
                       'incoming_relation':'continuous' if not i or edit['shots'][i-1]['scene_id']==item['scene_id'] else 'scene_change',
                       'transition_reason':'','axis_bridge':'',
                       'director_tasks':['决定信息释放顺序，再选择机位与景别','校准所有具名人物起止位置与动作状态','在连续性成立后逐镜落实导演四轴'],
                       'generation_constraints':['逐镜关键帧锁定空间起点','动作结束状态承接下一镜，禁止无依据瞬移','不把电影画面/对白作为默认模型输入'],
                       'status':'proposed'})
    local_analysis=library.get('local_cut_analysis')
    rhythm=None
    if local_analysis:
        import statistics
        lengths=[r['end_sec']-r['start_sec'] for r in local_analysis['shots']]
        rhythm={'source_path':local_analysis['source_path'],'source_sha256':local_analysis['source_sha256'],
                'shot_count':len(lengths),'median_shot_sec':statistics.median(lengths),
                'comparison':[{'shot_id':r['shot_id'],'current_duration_sec':r['window'][1]-r['window'][0],
                               'ratio_to_benchmark_median':(r['window'][1]-r['window'][0])/statistics.median(lengths)} for r in drafts],
                'scope':'actual_cut_rhythm_comparison_not_automatic_music_retiming'}
    result={'schema_version':1,'project_id':str(project),'decision':'pending','artistic_approval':False,
            'source_index_path':str(index_path),'inputs':{str(p):v for p,v in versions.items()},
            'music_status':music.get('status'),'timing_verified':timeline.get('production_timing_verified',False),
            'shots':drafts,'continuity_issues':continuity_errors(drafts),
            'benchmark_rhythm':rhythm,
            'scope':'source_grounded_scale_rhythm_retrieval_plus_authored_story_structure_proposal'}
    atomic_batch({output:encoded(result)},expected={**versions,output:None})
    return result


def require(project, storyboard=None, *, required=False):
    """兼容旧项目；一旦声明采用桥接层，所有依赖、选段和当前镜头必须一致。"""
    project=Path(project).resolve()
    policy_path=project/'state/creative_workflow.json'
    policy_versions={}
    if policy_path.is_file():
        policy=load(policy_path)
        if not isinstance(policy,dict) or type(policy.get('film_grammar_required')) is not bool:
            raise ValueError('创作工作流策略格式无效')
        required=required or policy['film_grammar_required']
        policy_versions[policy_path]=digest(policy_path)
    story=load(project/'state/story_plan.json')
    binding=story.get('film_grammar_bridge')
    if not binding:
        if required: raise ValueError('电影语料辅助工作流尚未选择镜头组织方案；先完成参考转译和结构选择')
        return {}
    path=(project/binding['path']).resolve();path.relative_to(project)
    if digest(path)!=binding['sha256']: raise ValueError('电影组织设计已改变')
    plan=load(path)
    if plan.get('decision')!='accept_structure' or not str(plan.get('reviewer','')).strip(): raise ValueError('导演尚未选择镜头组织方案')
    errors=continuity_errors(plan['shots'])
    errors+=binding_errors(plan,story,load(project/'state/visual_edit_plan.json'),load(project/'state/sequence_direction.json'),load(plan['source_index_path']))
    if errors: raise ValueError('; '.join(errors))
    versions={**policy_versions,path:binding['sha256']}
    # 采纳后的story_plan是输出；其旧版本只用于历史，不造成指纹自引用。
    for p,v in plan['inputs'].items():
        source=Path(p)
        if source==project/'state/story_plan.json': continue
        if digest(source)!=v: raise ValueError('组织设计参考/音乐/剪辑依据过期: '+str(source))
        versions[source]=v
    targets=story['shots'] if storyboard is None else storyboard['shots']
    if [s['id'] for s in targets]!=[s['shot_id'] for s in plan['shots']]: raise ValueError('组织设计没有覆盖全部当前镜头')
    for formal,row in zip(targets,plan['shots']):
        if formal.get('film_grammar')!=row: raise ValueError('导演/分镜未保留镜头组织事实: '+formal['id'])
    if policy_path.is_file() and load(policy_path).get('organization_order') == 2:
        receipt_path=project/'state/director_execution.json'
        receipt=load(receipt_path)
        structure=project/'state/organization_structure.json'
        if receipt.get('structure_sha256')!=digest(structure) or plan.get('schema_version')!=2:
            raise ValueError('导演执行没有绑定当前前置组织结构')
        for name,version in receipt['execution_versions'].items():
            source=project/name
            if digest(source)!=version: raise ValueError('导演执行设计改变，须重新绑定结构: '+name)
            versions[source]=version
        versions[receipt_path]=digest(receipt_path);versions[structure]=digest(structure)
    return versions


def adopt(project, draft_path):
    project,draft_path=Path(project).resolve(),Path(draft_path).resolve()
    require_no_transaction(project)
    plan=load(draft_path);version=digest(draft_path)
    if plan.get('project_id')!=str(project) or plan.get('decision')!='accept_structure' or not str(plan.get('reviewer','')).strip():
        raise ValueError('先由导演逐镜选择并填写reviewer和decision=accept_structure；不批准艺术质量')
    policy_path=project/'state/creative_workflow.json'
    ordered=policy_path.is_file() and load(policy_path).get('organization_order')==2
    if ordered:
        structure=project/'state/organization_structure.json'
        if draft_path!=structure or plan.get('workflow_stage')!='structure_selected' or plan.get('schema_version')!=2:
            raise ValueError('必须先选择前置组织结构，再提交导演执行')
        development=load(project/'state/development.json')
        if development.get('organization_structure')!={'path':'state/organization_structure.json','sha256':version}:
            raise ValueError('导演开发未绑定先前选择的结构版本')
    issues=continuity_errors(plan['shots'])
    issues+=binding_errors(plan,load(project/'state/story_plan.json'),load(project/'state/visual_edit_plan.json'),load(project/'state/sequence_direction.json'),load(plan['source_index_path']))
    if issues: raise ValueError('; '.join(issues))
    expected={Path(p):v for p,v in plan['inputs'].items()};expected[draft_path]=version
    if any(digest(p)!=v for p,v in expected.items()): raise ValueError('提案依赖已过期，请重新检索')
    story_path=project/'state/story_plan.json';story=load(story_path)
    if [r['id'] for r in story['shots']]!=[r['shot_id'] for r in plan['shots']]: raise ValueError('组织提案镜头集合不一致')
    run_id=uuid.uuid4().hex;target=project/'state/film_grammar'/f'{run_id}.json'
    text=encoded(plan)
    import hashlib
    story['film_grammar_bridge']={'path':str(target.relative_to(project)),'sha256':'sha256:'+hashlib.sha256(text.encode()).hexdigest()}
    for s,row in zip(story['shots'],plan['shots']): s['film_grammar']=copy.deepcopy(row)
    try:
        from .workbench_adoption import invalidations
    except ImportError:
        from workbench_adoption import invalidations
    updates,tracked,memberships=invalidations(project,run_id);expected.update(tracked)
    archive=project/'state/adoption_history'/run_id/'story_plan.before.json'
    updates.update({archive:story_path.read_bytes(),target:text,story_path:encoded(story)})
    expected.update({archive:None,target:None})
    if ordered:
        execution_paths=[project/'state'/f'{name}.json' for name in ('development','visual_edit_plan','sequence_direction')]
        execution_versions={p.relative_to(project).as_posix():digest(p) for p in execution_paths}
        execution_versions['state/story_plan.json']='sha256:'+hashlib.sha256(encoded(story).encode()).hexdigest()
        expected.update({p:digest(p) for p in execution_paths});expected[policy_path]=digest(policy_path)
        receipt=project/'state/director_execution.json'
        expected[receipt]=digest(receipt) if receipt.is_file() else None
        updates[receipt]=encoded({'structure_sha256':version,'execution_versions':execution_versions,'artistic_approval':False})

    def unchanged():
        for (folder,pattern),files in memberships.items():
            if sorted((project/folder).glob(pattern))!=files: raise ValueError('采纳期间审批文件集合改变')
    atomic_batch(updates,expected=expected,before_commit=unchanged)
    return {'status':'structure_selected_recompile_required','record':str(target),'artistic_approval':False,
            'next':['storyboard_build','prompt_build','chapter_plan_build','chapter_prompt_build']}


def preview(project, draft_path, output):
    """用当前逐镜导演提示词编译待选制作包；不登记正式章节，不批准或派发。"""
    project,draft_path,output=Path(project).resolve(),Path(draft_path).resolve(),Path(output).resolve()
    plan=load(draft_path)
    if plan['project_id']!=str(project): raise ValueError('提案属于其他项目')
    versions={Path(p):v for p,v in plan['inputs'].items()};versions[draft_path]=digest(draft_path)
    if any(digest(p)!=v for p,v in versions.items()): raise ValueError('待选制作包依赖已过期')
    issues=continuity_errors(plan['shots'])+binding_errors(plan,load(project/'state/story_plan.json'),load(project/'state/visual_edit_plan.json'),
                                                        load(project/'state/sequence_direction.json'),load(plan['source_index_path']))
    if issues: raise ValueError('; '.join(issues))
    try:
        from .chapter_prompt_build import compile_chapter
    except ImportError:
        from chapter_prompt_build import compile_chapter
    prompts={}
    for row in plan['shots']:
        path=project/'prompts'/f"{row['shot_id']}.json";versions[path]=digest(path)
        value=load(path)
        if value['meta']['window']!={'start':row['window'][0],'end':row['window'][1]}: raise ValueError('现有提示词时间窗口已过期')
        value['direction']['film_grammar']=copy.deepcopy(row);prompts[row['shot_id']]=value
    edit=load(project/'state/visual_edit_plan.json');by_id={r['id']:r for r in edit['shots']}
    style_path=project/'state/style.json';versions[style_path]=digest(style_path)
    aspect=load(style_path)['aspect_ratio']
    character_names=None
    if (project/'state/reference_roles.json').is_file():
        try:
            from .reference_roles import require as require_roles
        except ImportError:
            from reference_roles import require as require_roles
        roles,role_versions=require_roles(project);versions.update(role_versions)
        character_names=[r['name'] for r in roles['characters']]
    has_reference=bool(character_names) or bool(list((project/'input').glob('protagonist.*')))
    groups=[]
    for row in plan['shots']:
        if not groups or groups[-1][-1].get('organization_sequence_id')!=row.get('organization_sequence_id') or groups[-1][-1]['method']!=row['method'] or groups[-1][-1]['scene_id']!=row['scene_id']: groups.append([])
        groups[-1].append(row)
    packets=[]
    for i,group in enumerate(groups,1):
        chapter={'id':f'DRAFT{i:02d}','shot_ids':[r['shot_id'] for r in group],
                 'window':{'start':group[0]['window'][0],'end':group[-1]['window'][1]},
                 'continuity_lock':load(project/'state/story_plan.json').get('continuity_bible',{}),
                 'shot_deltas':[{'id':r['shot_id'],'cut_relation':by_id[r['shot_id']].get('cut_relation','pending'),
                                 'editorial_role':r['editorial_role_proposal']} for r in group]}
        packet=compile_chapter(chapter,prompts,aspect,has_reference,character_names=character_names)
        packet.update(status='proposed',dispatch_allowed=False,backend_duration_checked=False)
        packets.append(packet)
    result={'schema_version':1,'project_id':str(project),'decision':'pending','artistic_approval':False,
            'dispatch_allowed':False,'packets':packets,'inputs':{str(p):v for p,v in versions.items()},
            'next_required':['导演选择与正式重编译','逐镜关键帧与全曲预演','实际后端能力/时长/原生请求核验','代表样片动态审片']}
    atomic_batch({output:encoded(result)},expected={**versions,output:None})
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('analyze','index','propose','preview','adopt','check'))
    parser.add_argument('--project',type=Path);parser.add_argument('--ada-repo',type=Path)
    parser.add_argument('--video',type=Path);parser.add_argument('--analysis',type=Path)
    parser.add_argument('--index',type=Path);parser.add_argument('--draft',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--beats',type=Path,help='明确的歌曲叙事节拍与镜头归组，可指定组织方法')
    args=parser.parse_args()
    needed={'analyze':('video','output'),'index':('ada_repo','output'),'propose':('project','index','output'),'preview':('project','draft','output'),'adopt':('project','draft'),'check':('project',)}[args.action]
    if any(getattr(args,k) is None for k in needed): parser.error('缺少参数: '+', '.join('--'+k for k in needed))
    result=(analyze(args.video,args.output) if args.action=='analyze' else index(args.ada_repo,args.output,cut_analysis=args.analysis) if args.action=='index'
            else propose(args.project,args.index,args.output,beats_path=args.beats) if args.action=='propose' else preview(args.project,args.draft,args.output) if args.action=='preview'
            else adopt(args.project,args.draft) if args.action=='adopt'
            else {'status':'checked','versions':{str(p):v for p,v in require(args.project).items()}})
    print(encoded(result))
