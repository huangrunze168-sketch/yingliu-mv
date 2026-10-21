"""对实际媒体核验帧数、帧率、画布、音轨和母带载荷。"""
import hashlib
import json
import subprocess
import math
from pathlib import Path


def probe(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                             "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,nb_read_frames,duration,sample_rate,channels:format=duration",
                             "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def ratio(text):
    a, b = text.split("/")
    value = float(a)/float(b)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("媒体帧率不是正向有限数")
    return value


def is_cfr(path, fps):
    """检查解码帧时间戳，avg_frame_rate==r_frame_rate不充分证明CFR。"""
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                             '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time',
                             '-of', 'json', str(path)], capture_output=True, text=True, check=True)
    frames = json.loads(result.stdout).get('frames', [])
    timestamps = [float(frame['best_effort_timestamp_time']) for frame in frames
                  if 'best_effort_timestamp_time' in frame]
    return len(timestamps) == len(frames) and len(frames) > 1 and all(
        math.isfinite(b) and abs((b-a)-1/fps) <= max(2e-6, .001/fps)
        for a, b in zip(timestamps, timestamps[1:]))


def audio_hash(path, copy=False):
    if copy:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_packets",
            "-show_entries", "packet=data_hash", "-show_data_hash", "sha256",
            "-of", "json", str(path),
        ], capture_output=True, text=True, check=True)
        packet_hashes = [packet.get("data_hash") for packet in
                         json.loads(result.stdout).get("packets", [])]
        if not packet_hashes or any(not value for value in packet_hashes):
            raise ValueError("无法取得完整音频包哈希")
        aggregate = hashlib.sha256()
        for value in packet_hashes:
            aggregate.update(value.encode("ascii"))
            aggregate.update(b"\0")
        return "sha256:" + aggregate.hexdigest()
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                             "-c:a", "pcm_s24le", "-f", "hash", "-hash", "sha256", "-"],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def verify_delivery(path, master, duration, fps, width, height, *, project=None):
    data = probe(path)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    audio = next(s for s in data["streams"] if s["codec_type"] == "audio")
    original = next(s for s in probe(master)["streams"] if s["codec_type"] == "audio")
    errors = []
    expected = int(float(duration)*float(fps) + 1e-6)
    if int(video["nb_read_frames"]) != expected or abs(ratio(video["avg_frame_rate"])-fps)>1e-6:
        errors.append("成片实际帧数或帧率不符合项目")
    if not is_cfr(path, fps):
        errors.append("交付画面不是已核验CFR")
    if (video["width"], video["height"]) != (width, height):
        errors.append("成片画布不符合项目")
    if abs(float(video.get("duration", data["format"]["duration"]))-duration) > 1/fps+0.01:
        errors.append("成片视频时长超出帧容差")
    if audio["sample_rate"] != original["sample_rate"] or audio["channels"] != original["channels"]:
        errors.append("交付音轨改变原采样率或声道")
    copy = original["codec_name"] == audio["codec_name"]
    source_hash, output_hash = audio_hash(master, copy), audio_hash(path, copy)
    if source_hash != output_hash:
        errors.append("原始母带与交付音频载荷不一致")
    sound_report = None
    if project is not None:
        try:
            try:
                from .sound_mix import verify as verify_sound
            except ImportError:
                from sound_mix import verify as verify_sound
            sound_report = verify_sound(project, path, master)
        except Exception as exc:
            errors.append('声音转场交付核验失败: '+str(exc))
    return {"status": "blocked" if errors else "pass", "errors": errors, "streams": data["streams"],
            "sound_mix": sound_report,
            "master_audio_hash": source_hash, "delivery_audio_hash": output_hash,
            "hash_mode": "encoded_packets" if copy else "decoded_pcm_s24le"}
