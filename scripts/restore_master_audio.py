"""保留已有 MV 画面，把原始母带重贴到新文件，并验证音频载荷。"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

try:
    from .audio_master import resolve_master, master_encoding_args, probe_audio
except ImportError:
    from audio_master import resolve_master, master_encoding_args, probe_audio


def media_hash(path: Path, stream: str, copy: bool) -> str:
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", stream,
        "-c", "copy" if copy else "pcm_s24le", "-f", "hash", "-hash", "sha256", "-",
    ], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def restore(project: Path, video: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"拒绝覆盖已有文件: {output}")
    if output.suffix.lower() != ".mp4":
        raise ValueError("输出须为 .mp4")
    timeline = json.loads((project / "state" / "timeline.json").read_text(encoding="utf-8"))
    master = resolve_master(project, timeline)
    encoding = master_encoding_args(master)
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-n", "-v", "error", "-i", str(video), "-i", str(master),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *encoding,
        "-movflags", "+faststart", str(output),
    ], check=True, capture_output=True)
    copied = encoding == ["-c:a", "copy"]
    source_audio_hash = media_hash(master, "0:a:0", copied)
    output_audio_hash = media_hash(output, "0:a:0", copied)
    source_video_hash = media_hash(video, "0:v:0", True)
    output_video_hash = media_hash(output, "0:v:0", True)
    result = {
        "output": str(output), "master_audio": str(master),
        "master_file_sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
        "source_audio": probe_audio(master), "delivered_audio": probe_audio(output),
        "audio_encoding": encoding,
        "audio_hash_kind": "encoded_packets" if copied else "decoded_pcm_s24le",
        "source_audio_sha256": source_audio_hash, "delivered_audio_sha256": output_audio_hash,
        "audio_payload_identical": source_audio_hash == output_audio_hash,
        "video_payload_identical": source_video_hash == output_video_hash,
    }
    report = output.with_suffix(".audio_quality.json")
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not result["audio_payload_identical"] or not result["video_payload_identical"]:
        raise ValueError(f"载荷比对未通过，检查报告: {report}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir")
    parser.add_argument("existing_video")
    parser.add_argument("new_output")
    args = parser.parse_args()
    print(json.dumps(restore(Path(args.project_dir).resolve(),
                             Path(args.existing_video).resolve(),
                             Path(args.new_output).resolve()), ensure_ascii=False))
