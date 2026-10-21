"""先组织、后导演：前置提案不读取导演设计，结构选择绑定后续执行。"""
import argparse
import copy
from pathlib import Path
try:
    from . import film_grammar_bridge as bridge
except ImportError:
    import film_grammar_bridge as bridge


def propose(project, beats_path, index_path, output):
    project=Path(project).resolve(); beats_path=Path(beats_path).resolve()
    index_path=Path(index_path).resolve(); output=Path(output).resolve()
    paths=[project/'state/timeline.json',project/'state/music_structure.json',beats_path,index_path]
    versions={p:bridge.digest(p) for p in paths}
    timeline,music,beats,library=[bridge.load(p) for p in paths]
    if beats.get('method')!='zy_story_beats': raise ValueError('先完成ZY故事节拍，method须为zy_story_beats')
    sources=beats.get('zy_sources',[])
    vendor=Path(__file__).resolve().parents[1]/'vendor/zy-cinematic-realism'
    if not sources: raise ValueError('故事节拍须记录实际ZY来源文件')
    for source in sources:
        path=(vendor/source['path']).resolve();path.relative_to(vendor.resolve())
        if bridge.digest(path)!=source['sha256']: raise ValueError('ZY故事来源版本失效')
        versions[path]=source['sha256']
    versions.update({Path(p):v for p,v in library['source_versions'].items()})
    rows=copy.deepcopy(beats['shots']);last=0;ids=set()
    for row in rows:
        sid=row['shot_id'];window=row['window']
        if sid in ids or window[0]!=last or window[1]<=window[0]: raise ValueError('故事镜头须唯一且连续覆盖歌曲')
        ids.add(sid);last=window[1]
        for field in ('scene_id','story_goal','current_action','music_refs','editorial_role_proposal','source_application','organization_sequence_id'):
            if not row.get(field): raise ValueError(sid+': 缺少故事组织字段 '+field)
        method=row.get('method')
        if method not in bridge.METHODS: raise ValueError('须由创作角色明确选择组织方法')
        chosen=next((p for p in library['passages'] if p['id']==row.get('source_id')),None)
        if chosen is None: raise ValueError(sid+': 须明确选择真实来源段落')
        row['source_evidence']=copy.deepcopy(chosen)
        row['organization_instruction']=bridge.METHODS[method]['instruction']
        row.setdefault('generation_constraints',['保留已选信息顺序、动作承接和人物空间'])
    if not rows or abs(last-timeline['duration_sec'])>.001: raise ValueError('组织镜头必须覆盖全曲')
    result={'schema_version':2,'workflow_stage':'organization_proposed','project_id':str(project),
            'source_index_path':str(index_path),'inputs':{str(p):v for p,v in versions.items()},
            'decision':'pending','artistic_approval':False,'shots':rows,
            'continuity_issues':bridge.continuity_errors(rows)}
    bridge.atomic_batch({output:bridge.encoded(result)},expected={**versions,output:None})
    return result


def select(project, draft):
    project=Path(project).resolve();draft=Path(draft).resolve();plan=bridge.load(draft)
    if plan.get('schema_version')!=2 or plan.get('project_id')!=str(project): raise ValueError('需要当前项目的前置组织提案')
    if plan.get('decision')!='accept_structure' or not str(plan.get('reviewer','')).strip(): raise ValueError('先明确选择结构')
    errors=bridge.continuity_errors(plan['shots'])
    library=bridge.load(plan['source_index_path']);sources={p['id']:p for p in library['passages']}
    for row in plan['shots']:
        if sources.get(row['source_evidence']['id'])!=row['source_evidence']: errors.append('来源段落已改变')
    if errors: raise ValueError('; '.join(errors))
    versions={Path(p):v for p,v in plan['inputs'].items()};versions[draft]=bridge.digest(draft)
    if any(bridge.digest(p)!=v for p,v in versions.items()): raise ValueError('组织提案依赖过期')
    target=project/'state/organization_structure.json'
    selected=copy.deepcopy(plan);selected['workflow_stage']='structure_selected'
    policy=project/'state/creative_workflow.json'
    bridge.atomic_batch({target:bridge.encoded(selected),policy:bridge.encoded({'film_grammar_required':True,'organization_order':2})},
        expected={**versions,target:bridge.digest(target) if target.is_file() else None,policy:bridge.digest(policy) if policy.is_file() else None})
    return {'status':'structure_selected_director_execution_required','binding':{'path':target.relative_to(project).as_posix(),'sha256':bridge.digest(target)}}


def bind(project):
    project=Path(project).resolve();target=project/'state/organization_structure.json'
    plan=bridge.load(target);binding={'path':target.relative_to(project).as_posix(),'sha256':bridge.digest(target)}
    development=bridge.load(project/'state/development.json')
    if development.get('organization_structure')!=binding: raise ValueError('导演开发须绑定先前选择的结构版本')
    # adopt核验实际故事、剪辑、空间调度与前置结构一致，原子提交执行记录。
    return bridge.adopt(project,target)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('propose','select','bind'))
    parser.add_argument('--project',type=Path,required=True)
    for name in ('beats','index','output','draft'): parser.add_argument('--'+name,type=Path)
    args=parser.parse_args()
    needed={'propose':('beats','index','output'),'select':('draft',),'bind':()}[args.action]
    if any(getattr(args,k) is None for k in needed): parser.error('缺少参数')
    result=propose(args.project,args.beats,args.index,args.output) if args.action=='propose' else select(args.project,args.draft) if args.action=='select' else bind(args.project)
    print(bridge.encoded(result))
