"""共享工作流事实、严格类型、时间窗口与原子文件提交。"""
import hashlib
import json
import math
import os
import tempfile
import threading
from functools import wraps
from pathlib import Path

REFERENCE_INTENTS = {
    "character_identity", "wardrobe", "location", "composition", "storyboard",
    "blocking_start_state", "camera_motion", "action_timing", "performance_timing",
    "voice", "sound", "material", "lighting", "style",
}
SHOT_SIZES = {"大特写": "extreme_close_up", "特写": "close_up", "近景": "medium_close_up",
              "中景": "medium", "中远景": "medium_wide", "全景": "full", "远景": "wide",
              "细节": "insert", "空镜": "empty", "背影": "back", "群像": "group",
              "closeup": "close_up", "close": "close_up", "locked": "static"}
CAMERA_MOVEMENTS = {"固定": "static", "静止": "static", "locked": "static",
                    "推轨": "dolly", "拉轨": "dolly", "横摇": "pan", "俯仰摇": "tilt",
                    "手持": "handheld", "跟拍": "tracking", "升降": "crane"}


class RevisionConflict(ValueError):
    """读取后的版本已经被其他任务替换，调用者不能回写。"""


def number(value):
    if isinstance(value, bool):
        raise ValueError("布尔值不是数值")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("必须是有限数")
    return result


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def timeline_errors(shots, duration, id_field="id"):
    errors, seen, previous = [], set(), 0.0
    try:
        duration = number(duration)
        if duration <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return ["包时长必须是正向有限数"]
    if not isinstance(shots, list) or not shots:
        return ["镜头列表不能为空"]
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            errors.append(f"镜头{index}不是对象")
            continue
        sid = shot.get(id_field)
        if not isinstance(sid, str) or not sid.strip() or sid in seen:
            errors.append(f"镜头{index} ID为空或重复")
        seen.add(str(sid))
        try:
            window = shot.get("window") or {}
            start = number(shot.get("start_sec", window.get("start")))
            end = number(shot.get("end_sec", window.get("end")))
            if start < 0 or end <= start or end > duration + 1e-6:
                raise ValueError
            if abs(start - previous) > 1e-6:
                errors.append(f"{sid}时间窗不连续、重叠或首镜不为0")
            previous = end
        except (ValueError, TypeError, AttributeError):
            errors.append(f"{sid}时间窗必须正向有限并位于包内")
    if abs(previous - duration) > 1e-6:
        errors.append("末镜没有完整覆盖包时长")
    return errors


def unique_ids(shots):
    ids = [shot.get("id") for shot in shots if isinstance(shot, dict)]
    return (bool(ids) and all(isinstance(sid, str) and sid.strip() for sid in ids)
            and len(ids) == len(shots) == len(set(ids)))


def timing_authority(timeline):
    authority = str(timeline.get("authority", "")) + str(timeline.get("asr_engine", ""))
    pending = timeline.get("pending_review", [])
    invalid = any(word in authority.lower() for word in ("fallback", "provisional", "pending", "diagnostic"))
    production = (timeline.get("production_timing_verified") is True and not pending
                  and timeline.get("simulation_only") is not True and not invalid)
    return "production" if production else "provisional"


def supported_transitions(shots):
    errors = []
    for index, shot in enumerate(shots):
        editing = (shot.get("crew") or {}).get("editing") or {}
        kind = editing.get('transition_type', 'hard_cut')
        if kind == 'dissolve':
            duration = editing.get('transition_duration_frames')
            if type(duration) is not int or duration <= 0 or index == len(shots)-1:
                errors.append(f"{shot.get('id')}叠化须指定正整数transition_duration_frames且有下一镜")
        elif kind not in {"hard_cut", "match_cut"}:
            errors.append(f"{shot.get('id')}本地渲染尚未实现 {editing.get('transition_type')}，不能宣称完成")
        audio_kind = editing.get("audio_transition", "straight")
        if audio_kind in {'J_cut', 'L_cut'}:
            frames = editing.get('audio_transition_duration_frames')
            if type(frames) is not int or frames <= 0 or index == len(shots)-1:
                errors.append(f"{shot.get('id')}J/L声音转场须有下一镜及正整数audio_transition_duration_frames")
        elif audio_kind not in {"straight", "music_hit"}:
            errors.append(f"{shot.get('id')}声画偏移尚未实现 {editing.get('audio_transition')}，需外部音效轨渲染证据")
    return errors


_thread_write_lock = threading.RLock()


def _serialize_threads(operation):
    @wraps(operation)
    def wrapped(*args, **kwargs):
        with _thread_write_lock:
            return operation(*args, **kwargs)
    return wrapped


@_serialize_threads
def atomic_batch(updates, *, expected=None, before_commit=None, deletions=()):
    """失败时恢复原文件；崩溃遗留事务会被后续调用阻断并自动恢复。"""
    paths = {Path(path).resolve(): payload for path, payload in updates.items()}
    removed = {Path(path).resolve() for path in deletions}
    expected_paths = {Path(path).resolve(): value for path, value in (expected or {}).items()}
    if removed & set(paths):
        raise ValueError('事务写入与删除目标重叠')
    if any(path not in expected_paths or expected_paths[path] is None or not path.is_file() for path in removed):
        raise ValueError('事务删除必须绑定现存文件的明确版本')
    paths.update({path: b'' for path in removed})
    if not paths:
        return
    common = Path(os.path.commonpath([str(path.parent) for path in paths]))
    for ancestor in (common, *common.parents):
        if (ancestor / "state").is_dir():
            common = ancestor
            break
    common.mkdir(parents=True, exist_ok=True)
    journal = common / ".pipeline-transaction.json"
    lock = common / ".pipeline-write.lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("存在在途写入或中断事务，请先核查并恢复，禁止覆盖") from exc
    originals, stages = {}, {}
    try:
        if journal.exists():
            recover_transaction(journal)
        for name, required in (expected or {}).items():
            target = Path(name).resolve()
            # expected只读取指纹；用户明确指定的外部源媒体也可参与一致性检查。
            actual = digest(target) if target.is_file() else None
            if actual != required:
                raise RevisionConflict('版本已改变，拒绝旧任务回写')
        for path, payload in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            originals[str(path)] = path.read_bytes().hex() if path.exists() else None
            fd, name = tempfile.mkstemp(prefix=".pipeline-", dir=path.parent)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload if isinstance(payload, bytes) else str(payload).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            stages[str(path)] = name
        with journal.open("w", encoding="utf-8") as handle:
            json.dump({"originals": originals, "stages": stages}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        if before_commit is not None:
            before_commit()
        for path, name in stages.items():
            if Path(path) in removed:
                Path(path).unlink()
            else:
                os.replace(name, path)
        journal.unlink()
    except BaseException:
        if journal.exists():
            recover_transaction(journal)
        raise
    finally:
        for name in stages.values():
            Path(name).unlink(missing_ok=True)
        os.close(lock_fd)
        lock.unlink(missing_ok=True)


def require_no_transaction(project):
    for root in (Path(project), Path(project)/"prompts", Path(project)/"state"):
        if (root/".pipeline-write.lock").exists() or (root/".pipeline-transaction.json").exists():
            raise ValueError(f"{root}存在未完成事务，先运行事务恢复，不得沿用部分新文件")


def recover_transaction(journal):
    journal = Path(journal).resolve()
    record = json.loads(journal.read_text(encoding="utf-8"))
    root = journal.parent
    # 先完整验证日志，再恢复；坏日志不能造成部分恢复或删除正常媒体。
    if not isinstance(record.get('originals'), dict) or not isinstance(record.get('stages'), dict):
        raise ValueError('事务日志结构无效')
    originals, stages = {}, []
    for path, payload in record["originals"].items():
        target = Path(path).resolve()
        target.relative_to(root)
        if target == root or target == journal or target.is_dir() or target in originals:
            raise ValueError('事务恢复目标无效')
        originals[target] = None if payload is None else bytes.fromhex(payload)
    if record['stages'] and set(record['stages']) != set(record['originals']):
        raise ValueError('事务临时文件与恢复目标不匹配')
    for path, name in record['stages'].items():
        temporary = Path(name).resolve()
        temporary.relative_to(root)
        if (temporary.parent != Path(path).resolve().parent
                or not temporary.name.startswith('.pipeline-') or temporary.is_dir()
                or temporary in originals or temporary == journal
                or temporary.name == '.pipeline-write.lock' or temporary in stages):
            raise ValueError('事务临时文件不属于本次写入')
        stages.append(temporary)
    for target, payload in originals.items():
        if payload is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(payload)
    for temporary in stages:
        temporary.unlink(missing_ok=True)
    journal.unlink()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="恢复中断事务；运行前确认没有在途写入进程")
    parser.add_argument("journal", type=Path)
    parser.add_argument("--confirmed-no-writer", action="store_true", required=True)
    args = parser.parse_args()
    recover_transaction(args.journal.resolve())
    (args.journal.resolve().parent / ".pipeline-write.lock").unlink(missing_ok=True)
