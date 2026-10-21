"""实际原曲信号分析与cue候选；不把节拍/能量计算冒充语义听辨或导演批准。"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import subprocess
import uuid

try:
    from .pipeline_contracts import atomic_batch, digest, require_no_transaction
except ImportError:
    from pipeline_contracts import atomic_batch, digest, require_no_transaction


def capture(project):
    project = Path(project).resolve()
    require_no_transaction(project)
    timeline_path = project/'state/timeline.json'
    timeline_version = digest(timeline_path)
    timeline = json.loads(timeline_path.read_text(encoding='utf-8'))
    source = (project/timeline['master_audio']).resolve()
    source_version = digest(source)
    target = project/'state/music_structure.json'
    if target.exists():
        raise FileExistsError('已有音乐结构，禁止覆盖；变更须走草稿采纳并撤销旧审批')
    run_id = uuid.uuid4().hex
    output = project/'analysis/music_capture'/run_id
    output.mkdir(parents=True)
    wave = output/'analysis.wav'
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source), '-vn', '-ac', '1', '-ar', '16000',
               '-c:a', 'pcm_s16le', str(wave)]
    started = datetime.now(timezone.utc).isoformat()
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    receipt_path = output/'execution.json'
    receipt = {'run_id': run_id, 'project_id': str(project), 'command': command, 'started_at': started,
               'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr,
               'source_path': str(source), 'source_sha256': source_version,
               'status': 'signal_analysis_pending', 'artistic_approval': False}
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        if result.returncode != 0:
            raise ValueError('原曲实际解码失败')
        import librosa
        import numpy as np
        audio, sr = librosa.load(wave, sr=16000, mono=True)
        hop = 256
        envelope = librosa.onset.onset_strength(y=audio, sr=sr, hop_length=hop)
        tempo, beats = librosa.beat.beat_track(onset_envelope=envelope, sr=sr, hop_length=hop)
        onsets = librosa.onset.onset_detect(onset_envelope=envelope, sr=sr, hop_length=hop)
        beat_times = librosa.frames_to_time(beats, sr=sr, hop_length=hop).tolist()
        onset_times = librosa.frames_to_time(onsets, sr=sr, hop_length=hop).tolist()
        rms = librosa.feature.rms(y=audio, hop_length=hop)[0]
        duration = len(audio)/sr
        if abs(duration-float(timeline['duration_sec'])) > .05:
            raise ValueError('实际解码长度与时间轴不一致')
        features_path = output/'features.json'
        features = {'duration_sec': duration, 'sample_rate': sr, 'samples': len(audio), 'hop_length': hop,
                    'estimated_bpm': float(np.asarray(tempo).reshape(-1)[0]), 'beat_times': beat_times,
                    'onset_times': onset_times, 'onset_envelope': envelope.tolist(), 'rms': rms.tolist()}
        features_path.write_text(json.dumps(features, ensure_ascii=False), encoding='utf-8')
        cues = [{'id': f'{prefix}{index:04d}', 'time_sec': value, 'kind': kind,
                 'evidence': f'实际{run_id}信号分析，详见{features_path.relative_to(project).as_posix()}',
                 'review_status': 'pending_semantic_review'}
                for prefix, kind, times in [('B', 'machine_beat_candidate', beat_times),
                                            ('O', 'machine_onset_candidate', onset_times)]
                for index, value in enumerate(times, 1) if 0 <= value <= timeline['duration_sec']]
        cues.sort(key=lambda row: row['time_sec'])
        record = {'schema_version': 1, 'duration_sec': timeline['duration_sec'], 'status': 'signal_analysis_unreviewed',
                  'estimated_bpm': features['estimated_bpm'], 'cues': cues, 'sections': [],
                  'observed_audio': False, 'semantic_review': 'pending', 'artistic_approval': False,
                  'source_audio_sha256': source_version, 'features_path': str(features_path),
                  'features_sha256': digest(features_path), 'execution_path': str(receipt_path),
                  'limits': '仅真实信号候选；不猜间奏/主唱/语义，不把谱能量冒充情绪或听辨'}
        receipt.update(status='completed_signal_analysis_unreviewed', finished_at=datetime.now(timezone.utc).isoformat(),
                       decoded_audio_sha256=digest(wave), features_sha256=digest(features_path),
                       engine_versions={'librosa': importlib.metadata.version('librosa'), 'numpy': np.__version__},
                       script_sha256=digest(Path(__file__)))
        text = json.dumps(receipt, ensure_ascii=False, indent=2)
        atomic_batch({target: json.dumps(record, ensure_ascii=False, indent=2), receipt_path: text,
                      project/'state/music_execution.json': text},
                     expected={target: None, source: source_version, timeline_path: timeline_version,
                               receipt_path: digest(receipt_path), features_path: record['features_sha256']})
        return record
    except BaseException as exc:
        receipt.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    args = parser.parse_args()
    result = capture(args.project)
    print(json.dumps({'status': result['status'], 'cues': len(result['cues']), 'estimated_bpm': result['estimated_bpm'],
                      'semantic_review': result['semantic_review']}, ensure_ascii=False))
