"""交付母带与分析音频分离；禁止把低采样率对齐副本当成母带。"""
import json
import subprocess
from pathlib import Path
try:
    from .pipeline_contracts import digest, atomic_batch
except ImportError:
    from pipeline_contracts import digest, atomic_batch


def probe_audio(path: Path) -> dict:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels,bits_per_raw_sample,sample_fmt",
        "-of", "json", str(path),
    ], capture_output=True, text=True, encoding="utf-8", check=True)
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        raise ValueError(f"没有音频轨: {path}")
    return streams[0]


def resolve_master(project: Path, timeline: dict) -> Path:
    declared = timeline.get("master_audio") or timeline.get("source_audio")
    if not declared:
        raise ValueError("timeline 缺少 master_audio 原始母带路径")
    path = Path(declared)
    path = path if path.is_absolute() else project / path
    if not path.is_file():
        raise FileNotFoundError(f"原始母带不存在: {path}")
    info = probe_audio(path)
    if not timeline.get("master_audio") and int(info["sample_rate"]) <= 24000:
        raise ValueError(
            "source_audio 是低采样率分析音频；请在 timeline.master_audio 指定用户原始母带。"
            "若用户原文件本身为低采样率，也必须显式声明 master_audio。")
    actual = digest(path)
    declared_hash = timeline.get("master_audio_sha256")
    if declared_hash and declared_hash != actual:
        raise ValueError("原始母带SHA-256与输入声明不一致")
    lock_path = project / "state/master_audio_lock.json"
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if lock.get("sha256") != actual:
            raise ValueError("原始母带已改变，需显式重新登记并重审所有音频依赖")
    else:
        atomic_batch({lock_path: json.dumps({"sha256": actual, "path": str(path.resolve()),
                                           "origin": "declared_input" if declared_hash else "first_observed_input"})})
    return path


def master_encoding_args(path: Path) -> list[str]:
    """MP4 可承载的原音轨直接复制；PCM/FLAC 等使用 ALAC 无损编码。"""
    info = probe_audio(path)
    if info["codec_name"] in {"mp3", "aac", "alac", "ac3", "eac3"}:
        return ["-c:a", "copy"]
    bits = int(info.get("bits_per_raw_sample") or 0)
    if (bits > 24 or info["codec_name"] in {"pcm_s32le", "pcm_s32be"}
            or info.get("sample_fmt", "").startswith(("flt", "dbl"))):
        raise ValueError("此母带位深不适合默认 ALAC 交付，请选择能原样承载音轨的容器")
    return ["-c:a", "alac"]
