"""本地有声审片工作台：真实媒体播放、逐行复核及设计草稿；从不自动批准。"""
import argparse
from datetime import datetime, timezone
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import shutil
import uuid

try:
    from .pipeline_contracts import digest, atomic_batch
except ImportError:
    from pipeline_contracts import digest, atomic_batch

DESIGN_FILES = ('development', 'sequence_direction', 'story_plan', 'music_structure', 'style', 'visual_edit_plan')


def design_template(name, duration):
    base = {'draft_only': True, 'status': 'pending', 'artistic_approval': False, 'notes': ''}
    if name == 'development':
        base.update(treatment={k: '' for k in ('concept', 'form', 'emotional_arc', 'visual_motif',
                                              'climax', 'performance_rationale')},
                    sources=[], source_ledger=[], references=[], visual_rules=[], scenes=[], director={})
        base['treatment']['status'] = 'proposed'
    elif name == 'sequence_direction':
        base.update(duration_sec=duration, sources={}, shots=[],
                    required_shot_fields=['id', 'window', 'action', 'audience_gain', 'camera_reason',
                                          'cut_reason', 'lighting', 'art', 'performance', 'camera_xyz',
                                          'actor_start_xy', 'actor_end_xy', 'reference_ids'])
    elif name == 'story_plan':
        base.update(shots=[], continuity_bible={k: '' for k in ('character_lock', 'wardrobe_lock',
                    'prop_lock', 'location_topology', 'lighting_bible')},
                    departments=['screenwriting', 'directing', 'cinematography', 'lighting',
                                 'production_design', 'character_design', 'blocking', 'editing'])
    elif name == 'music_structure':
        base.update(duration_sec=duration, sections=[], cues=[], observed_audio=False)
    elif name == 'visual_edit_plan':
        base.update(shots=[], director_cut_approved=False, timing_status='pending_director_review',
                    performance_policy={})
    return base


def build(project, output, media=None, reference=None):
    project, output = Path(project).resolve(), Path(output).resolve()
    timeline_path = project/'state/timeline.json'
    timeline = json.loads(timeline_path.read_text(encoding='utf-8'))
    audio = (project/timeline['master_audio']).resolve()
    sources = {'audio': audio}
    if media:
        sources['candidate'] = Path(media).resolve()
    if reference:
        sources['reference'] = Path(reference).resolve()
    for name, path in sources.items():
        if not path.is_file():
            raise ValueError(f'{name}: 媒体不存在')
        allowed = ('.mp3', '.wav', '.m4a', '.flac', '.ogg') if name == 'audio' else ('.mp4', '.webm', '.mov')
        if path.suffix.lower() not in allowed:
            raise ValueError(f'{name}: 不支持的本地媒体格式')
    if output.exists():
        raise ValueError('工作台目录已存在，请使用新目录，不覆盖旧审片会话')
    # 只复制实际媒体，不拼母带、不变速，不把技术预览冒充内嵌原生音轨。
    output.mkdir(parents=True)
    versions = {str(timeline_path): digest(timeline_path)}
    review_path = project/'state/timing_review.json'
    versions[str(review_path)] = digest(review_path) if review_path.is_file() else None
    if review_path.is_file():
        review = json.loads(review_path.read_text(encoding='utf8'))
        if review.get('timeline_sha256') == versions[str(timeline_path)]:
            observations = {row['index']:row for row in review.get('lines', [])}
            for line in timeline['lines']:
                observed = observations.get(line['index'], {})
                if all(observed.get(k) == line.get(k) for k in ('index','text','start','end','speaker')):
                    line['observation'] = observed.get('observation', '')
    if timeline.get('machine_candidate_source'):
        try:
            from .timing_evidence import alignment_versions
        except ImportError:
            from timing_evidence import alignment_versions
        versions.update({str(p): v for p,v in alignment_versions(project, timeline).items()})
    for path in [project/'input/lyrics.txt', *sorted((project/'input').glob('*.png'))]:
        versions[str(path)] = digest(path) if path.is_file() else None
    assets = {}
    for name, path in sources.items():
        before = digest(path)
        filename = name+path.suffix.lower()
        target = output/filename
        shutil.copyfile(path, target)
        if digest(path) != before or digest(target) != before:
            raise ValueError('复制期间媒体发生变化，不得审查该会话')
        assets[name] = {'file': filename, 'sha256': before}
        versions[str(path)] = before
    drafts = {}
    for name in DESIGN_FILES:
        path = project/'state'/f'{name}.json'
        versions[str(path)] = digest(path) if path.is_file() else None
        drafts[name] = (json.loads(path.read_text(encoding='utf-8')) if path.is_file()
                        else design_template(name, timeline['duration_sec']))
    if any((digest(p) if Path(p).is_file() else None) != value for p, value in versions.items()):
        raise ValueError('构建期间项目输入变化，请使用新会话')
    manifest = {'schema_version': 1, 'session_id': uuid.uuid4().hex,
                'project_id': str(project), 'created_at': datetime.now(timezone.utc).isoformat(),
                'inputs': versions, 'assets': assets, 'timeline': timeline,
                'design_drafts': drafts, 'decision': 'pending',
                'scope': '真实本地播放器与观察记录；不认证观察者身份、注意力或AI实际听看'}
    (output/'session.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    # JSON只作为数据读取，防止歌词、文件名或设计文字闭合script标签。
    data = json.dumps(manifest, ensure_ascii=False).replace('<', '\\u003c').replace('&', '\\u0026')
    html = HTML.replace('__SESSION__', data)
    (output/'index.html').write_text(html, encoding='utf-8')
    return {'session_id': manifest['session_id'], 'index': str(output/'index.html'),
            'decision': 'pending', 'video_backend_used': False}


def import_draft(project, bundle, draft_path):
    project, bundle, draft_path = Path(project).resolve(), Path(bundle).resolve(), Path(draft_path)
    if draft_path.stat().st_size > 4*1024*1024:
        raise ValueError('草稿超过4MiB')
    session = json.loads((bundle/'session.json').read_text(encoding='utf-8'))
    draft = json.loads(draft_path.read_text(encoding='utf-8'))
    if session['project_id'] != str(project) or draft.get('session_id') != session['session_id']:
        raise ValueError('草稿与项目或会话不匹配')
    for path, expected in session['inputs'].items():
        if (digest(path) if Path(path).is_file() else None) != expected:
            raise ValueError('审片依据已变化，不能导入旧会话: '+path)
    for asset in session['assets'].values():
        if digest(bundle/asset['file']) != asset['sha256']:
            raise ValueError('播放器媒体已变化')
    if draft.get('decision') != 'pending':
        raise ValueError('工作台只接收待核草稿，不能在此批准')
    rows = draft.get('lines')
    originals = session['timeline']['lines']
    if not isinstance(rows, list) or len(rows) != len(originals):
        raise ValueError('必须保留全部歌词行')
    duration = session['timeline']['duration_sec']
    for row, original in zip(rows, originals):
        if any(row.get(k) != original.get(k) for k in ('index', 'text', 'type')):
            raise ValueError('歌词身份改变，应先修正式歌词来源')
        for key in ('start', 'end'):
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('时间必须是有限数值')
        if not 0 <= row['start'] < row['end'] <= duration:
            raise ValueError('复核时间超出原曲')
    interludes = draft.get('interludes', [])
    if not isinstance(interludes, list):
        raise ValueError('间奏须为区间列表')
    for row in interludes:
        if (not isinstance(row, dict) or not str(row.get('observation', '')).strip()
                or any(isinstance(row.get(k), bool) or not isinstance(row.get(k), (int, float))
                       or not math.isfinite(row[k]) for k in ('start', 'end'))
                or not 0 <= row['start'] < row['end'] <= duration):
            raise ValueError('间奏须有实际区间及观察说明')
    ordered = sorted(interludes, key=lambda row: row['start'])
    if any(a['end'] > b['start'] for a,b in zip(ordered, ordered[1:])):
        raise ValueError('间奏区间重叠，禁止导入')
    record = {'schema_version': 1, 'session_id': session['session_id'],
              'session_sha256': digest(bundle/'session.json'), 'draft_sha256': digest(draft_path),
              'project_id': str(project), 'decision': 'pending', 'agent_inspected': False,
              'dynamic_verified': False, 'artistic_approval': False, 'payload': draft,
              'scope': '未认证的浏览器观察草稿；不提升正式timeline或导演批准'}
    folder = project/'state/review_workbench'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder/f'{uuid.uuid4().hex}.json'
    # 每次新文件，不覆盖历史草稿，更不改正式批准。
    expected = {Path(p): value for p, value in session['inputs'].items()}
    expected.update({bundle/asset['file']: asset['sha256'] for asset in session['assets'].values()})
    expected[bundle/'session.json'] = record['session_sha256']
    expected[draft_path] = record['draft_sha256']
    expected[target] = None
    atomic_batch({target: json.dumps(record, ensure_ascii=False, indent=2)}, expected=expected)
    return target


class MediaHandler(SimpleHTTPRequestHandler):
    """支持浏览器拖动播放所需的单区间HTTP Range；只读指定工作台目录。"""
    def send_head(self):
        root = Path(self.directory).resolve()
        path = Path(self.translate_path(self.path)).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            self.send_error(403)
            return None
        if path.is_dir():
            path = path/'index.html'
        try:
            path.resolve().relative_to(root)
        except ValueError:
            self.send_error(403)
            return None
        if not path.is_file():
            self.send_error(404)
            return None
        length = path.stat().st_size
        start, end = 0, length-1
        header = self.headers.get('Range')
        if header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
            if not match or not any(match.groups()) or length == 0:
                self.send_error(416)
                return None
            a, b = match.groups()
            if a:
                start = int(a)
                end = min(int(b), end) if b else end
            else:
                start = max(0, length-int(b))
            if start > end or start >= length:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{length}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return None
        self.send_response(206 if header else 200)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Content-Length', str(end-start+1))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if header:
            self.send_header('Content-Range', f'bytes {start}-{end}/{length}')
        self.end_headers()
        handle = path.open('rb')
        handle.seek(start)
        self.remaining = end-start+1
        return handle

    def copyfile(self, source, outputfile):
        remaining = self.remaining
        while remaining:
            chunk = source.read(min(256*1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>本地有声审片工作台</title><style>
body{margin:24px;background:#16191f;color:#eee;font:16px system-ui}button,input,select,textarea{font:inherit;padding:6px;background:#272d38;color:#fff;border:1px solid #778}button{cursor:pointer}video{width:100%;max-height:420px;background:#000}audio{width:100%}.players{display:grid;grid-template-columns:1fr 1fr;gap:20px}td{padding:6px;border-bottom:1px solid #444}input[type=number]{width:90px}textarea{width:96%;min-height:70px}#design{height:260px}.note{color:#ffd185}section{margin:24px 0}#status{white-space:pre-wrap}
</style><h1>本地有声审片工作台</h1>
<p class="note">全部记录保持待核。真实播放器可听看；播放事件不能证明观察者完整审片，也不证明 AI 已听看。候选片可与原曲同步对照；偏移定义为候选时间减原曲时间。只选一路声音监听，导出记录不改变媒体音轨。</p>
<p><label><input id="syncEnabled" type="checkbox">原曲与候选同步</label> <label>候选偏移秒 <input id="syncOffset" type="number" step="0.001" value="0"></label> <label>监听<select id="syncListen"><option value="candidate">候选自身音轨</option><option value="audio">原曲母带</option></select></label> <button id="syncAlign">用当前两处位置校准偏移</button> <span id="syncClock"></span></p><div class="players" id="players"></div><section><h2>原曲与歌词时间复核</h2><audio id="audio" controls preload="metadata"></audio>
<p>点行号跳到原曲候选起点；修改起止时间、演唱者并写边界听辨记录。修改只进入草稿。</p><table><thead><tr><th>行</th><th>歌词</th><th>开始</th><th>结束</th><th>演唱者</th><th>观察</th></tr></thead><tbody id="lyrics"></tbody></table></section><section><h2>间奏区间复核</h2><p>只记录实际听辨的区间，不把歌词空档自动认作间奏。</p><input id="gapStart" type="number" step="0.001" min="0" placeholder="开始秒"><input id="gapEnd" type="number" step="0.001" min="0" placeholder="结束秒"><input id="gapNote" placeholder="声音及边界观察"><button id="addGap">添加待核间奏</button><ol id="gaps"></ol></section>
<section><h2>逐镜观察</h2><select id="target"></select> <button id="mark">取当前时间</button> <input id="time" type="number" min="0" step="0.001" value="0"><select id="dimension"><option>口型与主唱</option><option>搓碟声画动作</option><option>群众反应与镜长</option><option>正反打与轴线</option><option>运镜与人物路径</option><option>光色美术与对标氛围</option><option>剪辑接点与情绪推进</option></select><textarea id="observation" placeholder="写具体可观察事实，不用“已看完/没问题”代替"></textarea><button id="add">添加观察</button><ol id="findings"></ol></section>
<section><h2>设计与导演序列草稿</h2><p>缺失文件显示待填草稿，不写入正式 state。须完成真实创作和现有契约检查，不能以本工作台代替八部门交接。</p><select id="designName"></select><button id="saveDesign">保存当前设计草稿</button><textarea id="design"></textarea></section>
<p><button id="export">导出全部待核草稿 JSON</button></p><div id="status" role="status"></div>
<script id="session" type="application/json">__SESSION__</script><script>
const S=JSON.parse(document.getElementById('session').textContent),$=id=>document.getElementById(id);
const D={schema_version:1,session_id:S.session_id,decision:'pending',lines:structuredClone(S.timeline.lines),findings:[],interludes:structuredClone(S.timeline.lines.filter(r=>r.type==='interlude').map(r=>({start:r.start,end:r.end,observation:r.observation||'',status:'pending'}))),playback_events:[],design_drafts:structuredClone(S.design_drafts)};
if(S.timeline.machine_candidate_source)D.machine_timing_source=structuredClone(S.timeline.machine_candidate_source);
const players={audio:$('audio')};$('audio').src=S.assets.audio.file;
function log(name,e){if(D.playback_events.length<20000)D.playback_events.push({media:name,event:e.type,time_sec:e.target.currentTime,muted:e.target.muted,volume:e.target.volume,rate:e.target.playbackRate,wall_time:new Date().toISOString()});}
for(const [name,a] of Object.entries(S.assets)){if(name!=='audio'){const box=document.createElement('div'),h=document.createElement('h2'),v=document.createElement('video');h.textContent=name==='candidate'?'候选片（自身原声）':'对标片（自身原声）';v.controls=true;v.preload='metadata';v.src=a.file;box.append(h,v);$('players').append(box);players[name]=v;}}
for(const [name,p] of Object.entries(players)){const o=document.createElement('option');o.value=name;o.textContent={audio:'原曲',candidate:'候选片',reference:'对标片'}[name];$('target').append(o);for(const event of ['play','pause','ended','seeking','seeked','ratechange','volumechange','timeupdate'])p.addEventListener(event,e=>log(name,e));p.addEventListener('play',()=>onPlayerPlay(name));p.addEventListener('error',()=>{$('status').textContent='媒体无法解码：'+name+'。请选择浏览器支持的媒体副本，不得据此批准。';});}
// 原曲为共享时间基准；对标片保留独立时间，避免把不同版本强行对齐。
D.playback_sync={enabled:false,candidate_offset_sec:0,listen:'candidate'};
function syncSettings(){const n=Number($('syncOffset').value);if(!Number.isFinite(n)||Math.abs(n)>3600)throw Error('同步偏移须为正负3600秒以内有限值');D.playback_sync={enabled:$('syncEnabled').checked,candidate_offset_sec:n,listen:$('syncListen').value};return D.playback_sync;}
function syncPair(){return $('syncEnabled').checked&&Boolean(players.candidate);}
function applyListen(){if(!syncPair()){players.audio.muted=false;if(players.candidate)players.candidate.muted=false;return;}const c=syncSettings();players.audio.muted=c.listen!=='audio';players.candidate.muted=c.listen!=='candidate';}
function followAudio(){if(!syncPair())return;const c=syncSettings(),v=players.candidate,t=players.audio.currentTime+c.candidate_offset_sec;if(Number.isFinite(v.duration)&&t>=0&&t<v.duration){if(Math.abs(v.currentTime-t)>.08)v.currentTime=t;v.playbackRate=players.audio.playbackRate;if(!players.audio.paused&&v.paused)v.play().catch(e=>$('status').textContent=e.message);}else{v.pause();}$('syncClock').textContent=`原曲 ${players.audio.currentTime.toFixed(3)}s / 候选 ${v.currentTime.toFixed(3)}s（目标 ${t.toFixed(3)}s）`;}
function onPlayerPlay(name){if(!syncPair()||name==='reference'){for(const [n,p] of Object.entries(players))if(n!==name)p.pause();return;}try{syncSettings();if(players.reference)players.reference.pause();applyListen();if(name==='candidate'){const t=players.candidate.currentTime-D.playback_sync.candidate_offset_sec;if(t<0||t>=players.audio.duration){players.candidate.pause();throw Error('候选当前位置在原曲同步区间之外');}if(Math.abs(players.audio.currentTime-t)>.08)players.audio.currentTime=t;if(players.audio.paused)players.audio.play().catch(e=>$('status').textContent=e.message);}followAudio();}catch(e){$('status').textContent=e.message;}}
for(const ev of ['timeupdate','seeking','ratechange'])players.audio.addEventListener(ev,()=>{try{followAudio();}catch(e){$('status').textContent=e.message;}});
players.audio.addEventListener('pause',()=>{if(syncPair()&&players.candidate&&!players.candidate.paused)players.candidate.pause();});
if(players.candidate){players.candidate.addEventListener('pause',()=>{if(syncPair()&&!players.audio.paused&&players.candidate.currentTime>0&&players.candidate.currentTime<players.candidate.duration)players.audio.pause();});players.candidate.addEventListener('seeking',()=>{if(syncPair()){const t=players.candidate.currentTime-Number($('syncOffset').value);if(t>=0&&t<=players.audio.duration&&Math.abs(players.audio.currentTime-t)>.08)players.audio.currentTime=t;}});}
for(const id of ['syncEnabled','syncOffset','syncListen'])$(id).onchange=()=>{try{syncSettings();applyListen();followAudio();}catch(e){$('status').textContent=e.message;}};
$('syncAlign').onclick=()=>{if(!players.candidate){$('status').textContent='缺少候选片';return;}$('syncOffset').value=(players.candidate.currentTime-players.audio.currentTime).toFixed(3);syncSettings();$('status').textContent='同步偏移已记录在待核草稿';};
function showGaps(){$('gaps').replaceChildren();D.interludes.forEach((row,i)=>{const li=document.createElement('li');li.textContent=`${row.start.toFixed(3)}–${row.end.toFixed(3)}s：${row.observation} `;const remove=document.createElement('button');remove.textContent='删除此间奏草稿';remove.onclick=()=>{D.interludes.splice(i,1);showGaps();$('status').textContent='已删除间奏草稿；正式时间轴未改变。';};li.append(remove);$('gaps').append(li);});}showGaps();
$('addGap').onclick=()=>{const start=Number($('gapStart').value),end=Number($('gapEnd').value),observation=$('gapNote').value.trim();if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||start>=end||end>S.timeline.duration_sec||!observation){$('status').textContent='填写有效间奏范围和观察。';return;}if(D.interludes.some(r=>start<r.end&&end>r.start)){$('status').textContent='间奏范围重叠，请先删除待修改的旧区间。';return;}D.interludes.push({start,end,observation,status:'pending'});showGaps();$('status').textContent='已添加待核间奏草稿；正式时间轴未改变。';};
D.lines.forEach((line,i)=>{const tr=document.createElement('tr'),jump=document.createElement('button');jump.textContent=line.index;jump.onclick=()=>{players.audio.currentTime=Number(D.lines[i].start);followAudio();players.audio.play().catch(e=>$('status').textContent=e.message);};const first=document.createElement('td');first.append(jump);tr.append(first);const text=document.createElement('td');text.textContent=line.text||'[间奏]';tr.append(text);for(const key of ['start','end','speaker','observation']){const td=document.createElement('td'),input=document.createElement('input');input.type=['start','end'].includes(key)?'number':'text';input.step='0.001';input.value=line[key]??'';input.oninput=()=>D.lines[i][key]=input.type==='number'?Number(input.value):input.value;td.append(input);tr.append(td);}$('lyrics').append(tr);});
$('mark').onclick=()=>$('time').value=players[$('target').value].currentTime.toFixed(3);
$('add').onclick=()=>{const t=Number($('time').value),obs=$('observation').value.trim();if(!Number.isFinite(t)||t<0||!obs){$('status').textContent='填写有效时间与具体观察。';return;}const row={media:$('target').value,time_sec:t,dimension:$('dimension').value,observation:obs,status:'pending'};D.findings.push(row);const li=document.createElement('li');li.textContent=`${row.media} ${t.toFixed(3)}s ${row.dimension}：${obs}`;$('findings').append(li);$('observation').value='';};
for(const name of Object.keys(D.design_drafts)){const o=document.createElement('option');o.value=name;o.textContent=name;$('designName').append(o);}
let active=$('designName').value;function showDesign(){$('design').value=JSON.stringify(D.design_drafts[active],null,2);}function saveDesign(){try{const value=JSON.parse($('design').value);if(!value||Array.isArray(value)||typeof value!=='object')throw Error('设计须为JSON对象');D.design_drafts[active]=value;return true;}catch(e){$('status').textContent='设计JSON错误：'+e.message;return false;}}
$('saveDesign').onclick=()=>{if(saveDesign())$('status').textContent='设计草稿已暂存；导出后才保留。';};$('designName').onchange=()=>{if(!saveDesign()){$('designName').value=active;return;}active=$('designName').value;showDesign();};showDesign();
$('export').onclick=()=>{if(!saveDesign())return;try{syncSettings();}catch(e){$('status').textContent=e.message;return;}D.decision='pending';D.exported_at=new Date().toISOString();const blob=new Blob([JSON.stringify(D,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='review-draft-'+S.session_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('status').textContent='已导出待核草稿。正式批准未改变。';};
window.addEventListener('beforeunload',e=>{e.preventDefault();e.returnValue='请先导出草稿';});
</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('build')
    create.add_argument('project', type=Path)
    create.add_argument('output', type=Path)
    create.add_argument('--media', type=Path)
    create.add_argument('--reference', type=Path)
    serve = sub.add_parser('serve')
    serve.add_argument('bundle', type=Path)
    serve.add_argument('--port', type=int, default=8765)
    ingest = sub.add_parser('import-draft')
    ingest.add_argument('project', type=Path)
    ingest.add_argument('bundle', type=Path)
    ingest.add_argument('draft', type=Path)
    args = parser.parse_args()
    if args.action == 'build':
        print(json.dumps(build(args.project, args.output, args.media, args.reference), ensure_ascii=False))
    elif args.action == 'import-draft':
        print(import_draft(args.project, args.bundle, args.draft))
    else:
        if not (args.bundle/'session.json').is_file() or not (args.bundle/'index.html').is_file():
            parser.error('不是有效工作台目录')
        server = ThreadingHTTPServer(('127.0.0.1', args.port), functools.partial(MediaHandler, directory=str(args.bundle.resolve())))
        print(f'本地审片：http://127.0.0.1:{server.server_port}/ （只读，不自动批准）', flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
