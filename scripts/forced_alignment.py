"""中文官方歌词的本地MMS强制对齐；输出独立候选与置信度，不自动批准权威时间。"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import traceback
import uuid

try:
    from .pipeline_contracts import atomic_batch, digest
    from .lyrics_canonical import parse
    from .local_perception import stream_hash
except ImportError:
    from pipeline_contracts import atomic_batch, digest
    from lyrics_canonical import parse
    from local_perception import stream_hash


def align(project, model_dir):
    project, model_dir = Path(project).resolve(), Path(model_dir).resolve()
    timeline_path, lyrics_path = project/'state/timeline.json', project/'input/lyrics.txt'
    timeline = json.loads(timeline_path.read_text(encoding='utf-8'))
    audio = (project/timeline['master_audio']).resolve()
    expected = {p: digest(p) for p in (timeline_path, lyrics_path, audio)}
    official = parse(lyrics_path.read_text(encoding='utf-8'))
    run_id = uuid.uuid4().hex
    root = project/'analysis/forced_alignment'/run_id
    root.mkdir(parents=True)
    path = project/'state/forced_alignment_jobs'/f'{run_id}.json'
    record = {'schema_version': 1, 'run_id': run_id, 'pid': os.getpid(), 'project_id': str(project),
              'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
              'inputs': {str(p):v for p,v in expected.items()}, 'model': 'torchaudio.pipelines.MMS_FA',
              'review_decision': 'pending', 'production_timing_verified': False,
              'limits': '真实独立CTC候选；罗马化、多音字及伴奏可能误配，不以强制匹配冒充听辨批准'}
    version = None
    def save():
        nonlocal version
        atomic_batch({path: json.dumps(record, ensure_ascii=False, indent=2)}, expected={path: version})
        version = digest(path)
    save()
    try:
        wave = root/'source.wav'
        command = ['ffmpeg','-nostdin','-v','error','-i',str(audio),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wave)]
        decoded = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
        record['decode'] = {'command': command, 'returncode': decoded.returncode, 'stderr': decoded.stderr}
        save()
        if decoded.returncode != 0:
            raise ValueError('原曲实际解码失败')
        import torch
        import soundfile as sf
        import torchaudio
        from uroman import Uroman
        torch.set_num_threads(4)
        romanizer = Uroman()
        words, groups = [], []
        for row in official:
            normalized = re.sub(r"[^a-z' ]", ' ', romanizer.romanize_string(row['text'], lcode='cmn').lower())
            tokens = normalized.split()
            if not tokens:
                raise ValueError('官方歌词没有可对齐的实际罗马化内容')
            groups.append({**row, 'word_start_index': len(words), 'word_end_index': len(words)+len(tokens),
                           'romanized': ' '.join(tokens)})
            words += tokens
        bundle = torchaudio.pipelines.MMS_FA
        model_dir.mkdir(parents=True, exist_ok=True)
        model = bundle.get_model(dl_kwargs={'model_dir': str(model_dir)}).eval()
        weights = [*model_dir.glob('*.pth'), *model_dir.glob('*.pt')]
        if len(weights) != 1:
            raise ValueError('强制对齐模型目录须只包含对应MMS权重')
        record.update(model_file=str(weights[0]), model_sha256=stream_hash(weights[0]),
                      engine_versions={n: importlib.metadata.version(n) for n in ('torch','torchaudio','uroman')},
                      script_sha256=digest(Path(__file__)), romanized_words=words)
        save()
        samples, rate = sf.read(str(wave), dtype='float32')
        if rate != bundle.sample_rate:
            raise ValueError('实际输入采样率不匹配')
        waveform = torch.from_numpy(samples).unsqueeze(0)
        with torch.inference_mode():
            emission, _ = model(waveform)
            spans = bundle.get_aligner()(emission[0], bundle.get_tokenizer()(words))
        if len(spans) != len(words):
            raise ValueError('强制对齐结果没有覆盖所有词')
        ratio = len(samples)/emission.size(1)/rate
        raw_words = []
        for word, parts in zip(words, spans):
            if not parts:
                raise ValueError('存在没有对齐的罗马化词')
            weight = sum(len(s) for s in parts)
            raw_words.append({'word': word, 'start': parts[0].start*ratio, 'end': parts[-1].end*ratio,
                              'score': sum(s.score*len(s) for s in parts)/weight,
                              'spans': [{'token': s.token, 'start_frame': s.start, 'end_frame': s.end, 'score': s.score} for s in parts]})
        lines = []
        for group in groups:
            selected = raw_words[group['word_start_index']:group['word_end_index']]
            lines.append({**group, 'start_candidate': selected[0]['start'], 'end_candidate': selected[-1]['end'],
                          'mean_token_score': sum(w['score'] for w in selected)/len(selected),
                          'minimum_word_score': min(w['score'] for w in selected), 'pending_review': True})
        result_path = root/'alignment.json'
        result_path.write_text(json.dumps({'run_id':run_id,'source_audio_sha256':expected[audio],
                                          'duration_sec':len(samples)/rate,'emission_frames':emission.size(1),
                                          'words':raw_words,'lines':lines,'decision':'pending'},ensure_ascii=False,indent=2),encoding='utf-8')
        if any(digest(p) != v for p,v in expected.items()):
            raise ValueError('对齐期间输入改变')
        record.update(status='completed_alignment_unreviewed', finished_at=datetime.now(timezone.utc).isoformat(),
                      result_path=str(result_path), result_sha256=digest(result_path), lines=len(lines))
        save()
    except BaseException as exc:
        record.update(status='failed', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
        save()
        raise
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path)
    parser.add_argument('--model-dir',type=Path,required=True)
    args = parser.parse_args()
    result = align(args.project,args.model_dir)
    print(json.dumps({'status':result['status'],'run_id':result['run_id'],'lines':result['lines']},ensure_ascii=False))
