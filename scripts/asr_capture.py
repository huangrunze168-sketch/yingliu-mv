"""实际本地ASR及逐行匹配候选；不自动批准歌词时间，不覆盖母带或正式timeline。"""
import argparse
from datetime import datetime, timezone
from difflib import SequenceMatcher
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import unicodedata
import uuid

try:
    from .pipeline_contracts import atomic_batch, digest, require_no_transaction
    from .lyrics_canonical import parse
except ImportError:
    from pipeline_contracts import atomic_batch, digest, require_no_transaction
    from lyrics_canonical import parse


def normalize(text):
    return ''.join(c.lower() for c in unicodedata.normalize('NFKC', text) if c.isalnum())


def line_candidates(official, words):
    recognized, word_for_char = '', []
    for i, word in enumerate(words):
        token = normalize(word['word'])
        recognized += token
        word_for_char.extend([i]*len(token))
    texts = [normalize(row['text']) for row in official]
    mapping = {}
    for block in SequenceMatcher(None, ''.join(texts), recognized, autojunk=False).get_matching_blocks():
        mapping.update({block.a+i: block.b+i for i in range(block.size)})
    offset, candidates = 0, []
    for row, text in zip(official, texts):
        indices = [word_for_char[mapping[i]] for i in range(offset, offset+len(text)) if i in mapping]
        candidates.append({**row, 'start_candidate': words[min(indices)]['start'] if indices else None,
                           'end_candidate': words[max(indices)]['end'] if indices else None,
                           'matched_character_ratio': len(indices)/len(text) if text else 0,
                           'matched_word_indices': sorted(set(indices)), 'pending_review': True})
        offset += len(text)
    return candidates


def _capture(project, audio, lyrics, model_path):
    from faster_whisper import WhisperModel
    project, audio, lyrics, model_path = (Path(p).resolve() for p in (project, audio, lyrics, model_path))
    require_no_transaction(project)
    if not (model_path/'model.bin').is_file():
        raise ValueError('须指定真实本地模型目录，不自动下载或猜测模型')
    run_id = uuid.uuid4().hex
    path = project/'state/asr_execution.json'
    raw_path = project/'analysis/asr_runs'/run_id/'raw.json'
    candidate_path = project/'analysis/asr_runs'/run_id/'alignment_candidates.json'
    inputs = {p: digest(p) for p in (audio, lyrics, model_path/'model.bin',
                                    Path(__file__), Path(__file__).with_name('lyrics_canonical.py'))}
    previous = path.read_bytes() if path.is_file() else None
    version = digest(path) if previous else None
    record = {'schema_version': 1, 'run_id': run_id, 'status': 'running',
              'project_id': str(project), 'pid': os.getpid(),
              'started_at': datetime.now(timezone.utc).isoformat(),
              'engine': 'faster_whisper', 'engine_version': importlib.metadata.version('faster-whisper'),
              'inputs': {str(k): v for k, v in inputs.items()}, 'production_timing_verified': False}
    updates = {path: json.dumps(record, ensure_ascii=False, indent=2)}
    if previous:
        updates[path.parent/'asr_history'/f'{run_id}.json'] = previous
    atomic_batch(updates, expected={path: version, **inputs})
    version = digest(path)
    try:
        official = parse(lyrics.read_text(encoding='utf8'))
        model = WhisperModel(str(model_path), device='cpu', compute_type='int8', local_files_only=True)
        iterator, info = model.transcribe(str(audio), language='zh', word_timestamps=True,
                                         vad_filter=False, beam_size=5)
        segments, words = [], []
        for item in iterator:
            current = [{'start': float(w.start), 'end': float(w.end), 'word': w.word,
                        'probability': float(w.probability)} for w in item.words or []]
            segments.append({'start': float(item.start), 'end': float(item.end), 'text': item.text,
                             'avg_logprob': float(item.avg_logprob), 'words': current})
            words.extend(current)
        duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                         'format=duration', '-of', 'default=nw=1:nk=1', str(audio)]))
        raw = {'schema_version': 1, 'run_id': run_id, 'audio_path': str(audio),
               'audio_sha256': inputs[audio], 'lyrics_path': str(lyrics), 'lyrics_sha256': inputs[lyrics],
               'engine': record['engine'], 'engine_version': record['engine_version'],
               'duration_sec': duration, 'language_probability': float(info.language_probability),
               'model_path': str(model_path), 'model_sha256': inputs[model_path/'model.bin'],
               'segments': segments, 'words': words, 'production_timing_verified': False}
        candidates = {'schema_version': 1, 'run_id': run_id,
                      'method': 'official_text_to_actual_asr_character_matches',
                      'lines': line_candidates(official, words), 'production_timing_verified': False,
                      'limits': '匹配率不是时间正确率；可能跨句错配或共享词，全部待核，不写正式timeline。'}
        raw_text, candidate_text = (json.dumps(v, ensure_ascii=False, indent=2) for v in (raw, candidates))
        import hashlib
        record.update(status='completed_unreviewed', finished_at=datetime.now(timezone.utc).isoformat(),
                      raw_path=str(raw_path), raw_sha256='sha256:'+hashlib.sha256(raw_text.encode()).hexdigest(),
                      candidate_path=str(candidate_path), candidate_sha256='sha256:'+hashlib.sha256(candidate_text.encode()).hexdigest(),
                      word_count=len(words), official_line_count=len(official))
        atomic_batch({raw_path: raw_text, candidate_path: candidate_text,
                      path: json.dumps(record, ensure_ascii=False, indent=2)}, expected={path: version, **inputs})
        return record
    except Exception as exc:
        record.update(status='failed', errors=[f'{type(exc).__name__}: {exc}'])
        atomic_batch({path: json.dumps(record, ensure_ascii=False, indent=2)}, expected={path: version})
        raise


def capture(project, audio, lyrics, model_path):
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('项目目录不存在')
    lock = project/'.asr-execution.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, json.dumps({'pid': os.getpid()}).encode())
        return _capture(project, audio, lyrics, model_path)
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--audio', required=True, type=Path)
    parser.add_argument('--lyrics', required=True, type=Path)
    parser.add_argument('--model-path', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(capture(args.project_dir, args.audio, args.lyrics, args.model_path), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
