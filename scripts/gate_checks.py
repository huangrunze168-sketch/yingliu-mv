#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate_checks.py — 关5/关6 硬门禁机检器（纯标准库）

用法:
  python gate_checks.py gate1 <project_dir>            # 校验 timeline.json（关1）
  python gate_checks.py gate5 <project_dir>            # 校验 prompts/*.json，检查数读实际报告
  python gate_checks.py gate6 <project_dir>            # 校验 storyboard.json 窗口与口型

退出码: 0 = 全绿放行; 1 = 存在失败项（硬门禁阻断）
输出:   JSON 报告到 stdout；同时返回逐项明细供 HTML 关页渲染
"""
import json
import re
import subprocess
import sys
import hashlib
import html
from datetime import datetime
from pathlib import Path

try:
    from .storyboard_build import music_anchors, REQUIRED_STORY_FIELDS
    from .visual_development import validate as validate_development, shot_fingerprint
except ImportError:
    from storyboard_build import music_anchors, REQUIRED_STORY_FIELDS
    from visual_development import validate as validate_development, shot_fingerprint

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

LIPSYNC_WHITELIST = {"大特写", "特写", "近景", "中景"}
SIX_SECTIONS = ["style", "scene", "subject", "performance", "camera", "audio_policy"]
SING_MARK_RE = re.compile(r"唱|开口唱|歌声")


def nonlip_singing_marker(text):
    """排除唱盘设备名和明确否定，不把DJ/闭口反应误判为演唱。"""
    text = re.sub(r'唱(?:盘|片|机|臂|针)', '', text)
    text = re.sub(r'(?:不得|不要|不能|禁止|不)(?:跟唱|演唱|唱歌|开口唱)', '', text)
    return SING_MARK_RE.search(text)
SPEECH_RE = re.compile(r"说.{0,4}[「\"'‘“]|说.{0,6}话")
SECONDS_RE = re.compile(r"\d+(\.\d+)?\s*秒")
LIP_NEG_RE = re.compile(r"lip|mouth|嘴|口型", re.I)
CLOSED_LIP_DECL = "嘴唇自然闭合"

# 扩展项 #12：具体动作词表（非穷举，覆盖常见叙事动作）
ACTION_WORDS = ["抬", "握", "拿", "放", "坐", "站", "走", "跑", "跪", "靠", "推",
                "拉", "翻", "合", "转", "低头", "抬头", "回", "垂", "抱", "递",
                "拨", "点", "写", "穿", "脱", "擦", "点", "迈", "俯", "仰"]
REF_DUTIES = {"composition", "color", "lighting", "material", "likeness", "wardrobe", "none"}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def write_gate_artifacts(proj: Path, report: dict):
    """把门禁 JSON 与零依赖 HTML 一并落盘，保证项目可断点复核。"""
    gate = report["gate"]
    gates = proj / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    json_path = gates / f"gate{gate}.json"
    html_path = gates / f"gate{gate}.html"
    if html_path.exists():
        (gates / f"gate{gate}_prev.html").write_bytes(html_path.read_bytes())
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rows = []
    items = list(report.get("checks", []))
    for shot in report.get("shots", []):
        for check in shot.get("checks", []):
            items.append({**check, "object": check.get("shot_id") or shot.get("file", "")})
    for check in items:
        ok = bool(check.get("pass"))
        rows.append(
            "<tr><td>{}</td><td>{}</td><td class='{}'>{}</td><td>{}</td></tr>".format(
                html.escape(str(check.get("object", "项目"))),
                html.escape(str(check.get("item", ""))),
                "ok" if ok else "no", "通过" if ok else "失败",
                html.escape(str(check.get("evidence", ""))),
            )
        )
    summary = report.get("summary", {})
    passed = bool(summary.get("pass"))
    template = (Path(__file__).parent.parent / "assets" / "gate_page_template.html").read_text(encoding="utf-8")
    values = {
        "GATE_TITLE": f"关 {gate} 机检报告",
        "STATUS_CLASS": "pass" if passed else "fail",
        "STATUS_TEXT": "PASS" if passed else "FAIL",
        "PASSED": summary.get("passed", 0),
        "TOTAL": summary.get("total", 0),
        "PROJECT": proj.name,
        "TIMESTAMP": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ROWS": "\n".join(rows),
        "FAILED_NOTES": "" if passed else "<p class='fix'>存在失败项，修复后必须重新运行本门禁。</p>",
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", str(value))
    html_path.write_text(template, encoding="utf-8")


def persist_prompt_fingerprints(proj: Path, report: dict):
    storyboard_path = proj / "state" / "storyboard.json"
    if not storyboard_path.exists():
        return
    storyboard = load(storyboard_path)
    fingerprints = {Path(item["file"]).stem: item["fingerprint"]
                    for item in report.get("shots", [])}
    for shot in storyboard.get("shots", []):
        if shot.get("id") in fingerprints:
            shot["prompt_fingerprint"] = fingerprints[shot["id"]]
    storyboard_path.write_text(json.dumps(storyboard, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")


# ---------------- 关1 · 时间轴机检 ----------------

def check_timeline(tl: dict, lyrics_text: str, audio_duration: float = None) -> list:
    checks = []

    def add(name, ok, evidence=""):
        checks.append({"item": name, "pass": bool(ok), "evidence": str(evidence)[:200]})

    vocal = [ln for ln in tl.get("lines", []) if ln["type"] != "interlude"]
    interludes = [ln for ln in tl.get("lines", []) if ln["type"] == "interlude"]

    # ① 行数与官方歌词一致（非空行）
    try:
        try:
            from .lyrics_canonical import parse
        except ImportError:
            from lyrics_canonical import parse
        official_rows = parse(lyrics_text) if lyrics_text.strip() else []
        official_lines = [row['text'] for row in official_rows]
        lyrics_parse_error = False
    except ValueError:
        official_lines, official_rows, lyrics_parse_error = [], [], True
    if lyrics_parse_error:
        lyrics_ok = False
    elif official_lines:
        import unicodedata
        normalize = lambda value: "".join(unicodedata.normalize("NFKC", str(value)).split())
        lyrics_ok = [normalize(line.get("text", "")) for line in vocal] == [normalize(line) for line in official_lines]
    else:
        lyrics_ok = tl.get("lyrics_source") == "asr_only" and bool(vocal)
    add("歌词一行不能少", lyrics_ok,
        f"timeline={len(vocal)} official={len(official_lines)} source={tl.get('lyrics_source')}")
    if any(row.get('speaker') for row in official_rows):
        add('演唱者与官方角色标签一致', len(vocal) == len(official_rows) and all(
            not row.get('speaker') or row['speaker'] == line.get('speaker')
            for row, line in zip(official_rows, vocal)), '角色标签仅作元数据，不能改变谁在演唱')
    import math
    ids = [line.get("index") for line in tl.get("lines", [])]
    valid = all(type(i) is int and i > 0 for i in ids) and len(ids) == len(set(ids))
    for line in tl.get("lines", []):
        try:
            if isinstance(line["start"], bool) or isinstance(line["end"], bool):
                raise ValueError
            start, end = float(line["start"]), float(line["end"])
            valid &= math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= float(tl["duration_sec"])
        except (ValueError, TypeError, KeyError):
            valid = False
    add("歌词索引唯一且时间正向有限", valid)

    # ② 时间单调递增无重叠
    seq = tl.get("lines", [])
    bad = [(a["index"], b["index"]) for a, b in zip(seq, seq[1:])
           if float(b["start"]) < float(a["end"]) - 0.001]
    add("时间单调无重叠", not bad, str(bad[:3]))

    # ③ 全曲时长必须与音频实测一致，不能只相信 JSON 自报值
    declared_duration = float(tl.get("duration_sec", -1))
    duration_ok = (declared_duration > 0 and audio_duration is not None and
                   abs(declared_duration - audio_duration) < 0.1)
    add("全曲时长与音频实测一致", duration_ok,
        f"timeline={declared_duration} audio={audio_duration}")

    # ④ 间奏显式标注（至少一个 interlude，或显式声明无间奏）
    add("间奏显式标注", len(interludes) >= 1 or tl.get("no_interlude") is True,
        f"interludes={len(interludes)}")

    # ⑤ 待核清单：confidence < 0.85 的行必须全部登记 pending_review
    low = [ln["index"] for ln in vocal if float(ln.get("confidence", 1)) < 0.85]
    pend = tl.get("pending_review", [])
    add("待核行已登记", set(low) <= set(pend),
        f"low_conf={low[:5]} pending={pend[:5]}")

    # ⑥ 对齐引擎必须显式声明（uniform_fallback 不可冒充权威对齐）
    eng = tl.get("asr_engine", "")
    add("对齐引擎已声明", bool(eng), f"engine={eng}")
    return checks


# ---------------- 关5 · 提示词与项目依赖机检 ----------------

def check_prompt(pr: dict, timeline: dict, style: dict, has_protagonist: bool,
                 shot: dict = None) -> list:
    meta = pr.get("meta", {})
    checks = []

    def add(idx, name, ok, evidence=""):
        checks.append({"shot_id": meta.get("shot_id"), "idx": idx, "item": name,
                       "pass": bool(ok), "evidence": str(evidence)[:120]})

    song_dur = timeline.get("duration_sec", 0)
    line_map = {ln["index"]: ln for ln in timeline.get("lines", [])}
    perf = pr.get("performance", "")
    audio = pr.get("audio_policy", "")
    neg = pr.get("negative_prompt", "")
    lip = bool(meta.get("lip_sync"))
    w = meta.get("window", {})
    wlen = float(w.get("end", 0)) - float(w.get("start", 0))

    # 1 六段齐全
    missing = [k for k in SIX_SECTIONS if not str(pr.get(k, "")).strip()]
    add(1, "六段齐全", not missing, "missing=" + ",".join(missing))

    # 2 语言分层：歌词文本与 timeline 对应行逐字一致
    # BUG-09 修法：间奏镜(lyric_line=None或对应interlude行)不得携带歌词文本；
    # 空字典.get("text")返回None而非""，直接比较有假阳性
    lyric_ids = meta.get("lyric_lines", [meta.get("lyric_line")])
    lyrics = [line_map[index] for index in lyric_ids if index in line_map
              and line_map[index].get("type") != "interlude"]
    expected_lyric = "\n".join(line.get("text", "") for line in lyrics)
    if not lyrics:
        lyric_ok = not (meta.get("lyric_text") or "").strip()
    else:
        lyric_ok = expected_lyric == (meta.get("lyric_text") or "")
    if shot is not None:
        lyric_ok = lyric_ok and meta.get("lyric_lines", []) == shot.get("lyric_lines", [])
    add(2, "歌词与时间轴逐字一致", lyric_ok,
        f"timeline='{expected_lyric}' vs meta='{meta.get('lyric_text','')}'")

    # 3 唱歌格式：口型段 performance 含歌词独立行
    add(3, "口型段歌词独立成行", (not lip) or (
        bool(lyrics) and all(line["text"] in perf.splitlines() for line in lyrics)),
        "lyric_in_performance_lines")

    # 4 念白禁令
    full_text = " ".join(str(pr.get(k, "")) for k in SIX_SECTIONS)
    add(4, "无念白句式", not SPEECH_RE.search(full_text), SPEECH_RE.search(full_text))

    # 5 非口型闭口声明
    ok5 = True
    if not lip:
        ok5 = (CLOSED_LIP_DECL in audio) and bool(LIP_NEG_RE.search(neg))
    add(5, "非口型段闭口声明+负面压口型", ok5,
        f"decl={CLOSED_LIP_DECL in audio} neg_has_lip={bool(LIP_NEG_RE.search(neg))}")

    # 6 非口型段无唱歌标记
    add(6, "非口型段无唱歌标记", lip or not nonlip_singing_marker(perf + audio),
        nonlip_singing_marker(perf + audio))

    # 7 景别白名单 + 模型中立生成职责
    expected_role = "lip_sync" if lip else "narrative"
    shot_match = (shot is None or
                  (meta.get("shot_type") == shot.get("shot_type") and
                   lip == bool(shot.get("lip_sync")) and
                   shot.get("generation_role") == expected_role))
    role_match = meta.get("generation_role") == expected_role
    add(7, "口型段景别白名单且与分镜一致",
        ((not lip) or meta.get("shot_type") in LIPSYNC_WHITELIST) and
        shot_match and role_match,
        f"shot_type={meta.get('shot_type')} generation_role={meta.get('generation_role')}")

    # 8 禁手写秒数
    add(8, "正文无手写秒数", not SECONDS_RE.search(full_text), SECONDS_RE.search(full_text))

    # 9 时长一致
    dur = float(meta.get("duration_sec", -1))
    shot_window = (shot or {}).get("window", {})
    window_match = (shot is None or
                    (abs(float(w.get("start", -1)) - float(shot_window.get("start", -2))) < 0.001 and
                     abs(float(w.get("end", -1)) - float(shot_window.get("end", -2))) < 0.001))
    add(9, "duration_sec与窗口一致且匹配分镜", abs(dur - wlen) < 0.05 and window_match,
        f"meta={dur} window={wlen:.3f} storyboard_match={window_match}")

    # 10 窗口合法
    ok10 = 0 <= float(w.get("start", -1)) < float(w.get("end", -1)) <= song_dur + 0.05
    add(10, "窗口合法", ok10, f"{w} song={song_dur}")

    # 11 指纹一致
    expected_style_fp = style.get("fingerprint")
    fp_ok = bool(expected_style_fp) and meta.get("style_fingerprint") == expected_style_fp
    char_ok = (meta.get("character_fingerprint") not in (None, "", "REF")) if has_protagonist else True
    add(11, "风格/人物指纹一致", fp_ok and char_ok,
        f"style_ok={fp_ok} char_ok={char_ok}")

    # ---- 扩展项 #12-#14（具体瞬间/场景母版/参考职责）----

    # 12 检查已设计动作是否完整传递；动词词表不能证明语义质量，也不能
    # 因“压下把手/攥紧钥匙”等合法动作不在词表而误拒真实制作包。
    hit = [w for w in ACTION_WORDS if w in perf]
    if shot is not None:
        master = (pr.get("direction") or {}).get("scene_master") or {}
        action = shot.get("current_action")
        moment = shot.get("keyframe_moment")
        action_ok = (isinstance(action, str) and bool(action.strip()) and action in perf
                     and master.get("current_action") == action
                     and isinstance(moment, str) and bool(moment.strip())
                     and master.get("keyframe_moment") == moment)
        add(12, "动作与关键帧事实完整传递", action_ok,
            "核对设计传递，不认证动作自然或艺术质量；词表仅辅助=" + str(hit[:3]))
    else:
        add(12, "旧格式具体瞬间启发式", len(perf.strip()) >= 10 and bool(hit),
            f"legacy_heuristic_only actions={hit[:3]} len={len(perf.strip())}")

    # 13 场景母版一致：scene 命中锁定关键词
    sm = style.get("scene_master", {})
    if shot is not None and shot.get("scene_id"):
        scene_ok = meta.get("scene_id") == shot["scene_id"] and all(
            str(shot.get(key, "")).strip() and str(shot[key]) in pr.get("scene", "")
            for key in ("space", "blocking", "props", "light", "material_response", "visual_center"))
        add(13, "场景事实完整编译且场景引用一致", scene_ok, shot["scene_id"])
    elif sm.get("locked"):
        locked_words = sum((sm.get(k, []) for k in ("space", "light_sources", "props")), [])
        hit13 = [w for w in locked_words if w in pr.get("scene", "")]
        add(13, "场景母版一致", bool(hit13), f"hit={hit13[:2]} locked={locked_words[:4]}")
    else:
        add(13, "场景母版一致", True, "scene_master未锁定，跳过")

    # 14 参考职责声明
    duties = meta.get("reference_duties", [])
    enum_ok = isinstance(duties, list) and bool(duties) and all(d in REF_DUTIES for d in duties)
    identity_ok = (not has_protagonist) or ({"likeness", "wardrobe"} <= set(duties))
    add(14, "参考职责声明", enum_ok and identity_ok,
        f"duties={duties} enum_ok={enum_ok} identity_ok={identity_ok}")

    # 15-16：必须来自 ZY Story Visual Development，而非歌词模板拼接。
    story = pr.get("story", {})
    story_fields = ("beat", "before", "next", "shot_delta")
    add(15, "ZY故事结构完整", all(str(story.get(k, "")).strip() for k in story_fields),
        f"fields={[k for k in story_fields if story.get(k)]}")
    add(16, "故事方案指纹已锁定", bool(meta.get("story_plan_fingerprint")),
        meta.get("story_plan_fingerprint"))

    # 17-20：导演方法必须可执行，不能再靠姓名与空泛“电影感”。
    direction = pr.get("direction", {})
    method = direction.get("method", {})
    required_axes = ("light_contrast", "color_exposure", "lens_camera", "composition_space")
    add(17, "导演四轴已展开", all(str(method.get(k, "")).strip() for k in required_axes),
        f"missing={[k for k in required_axes if not method.get(k)]}")
    director_name = style.get("director_ref", {}).get("name", "")
    visible_prompt = json.dumps({key: value for key, value in pr.items() if key != "meta"},
                                ensure_ascii=False)
    add(18, "最终提示词不以导演姓名代替方法",
        not director_name or director_name not in visible_prompt,
        f"director={director_name}")
    anti_ai = direction.get("anti_ai", {})
    scene_master = direction.get("scene_master", {})
    required_scene_master = (
        "story_intent", "obstacle", "audience_information",
        "shooting_station", "frame_boundary", "visual_acceptance",
    )
    scene_master_ok = all(str(scene_master.get(key, "")).strip()
                          for key in required_scene_master)
    if shot is not None:
        scene_master_ok = scene_master_ok and all(
            scene_master.get(key) == shot.get(key) for key in required_scene_master
        )
    add(19, "导演级Scene Master与去AI化约束完整",
        scene_master_ok and
        bool(str(anti_ai.get("clarity_budget", "")).strip()) and
        bool(str(anti_ai.get("motion_rule", "")).strip()),
        f"scene_master={sorted(scene_master)} anti_ai={sorted(anti_ai)}")
    add(20, "切镜有因果触发", bool(str(direction.get("edit_trigger", "")).strip()),
        direction.get("edit_trigger", ""))

    return checks


# ---------------- 关6 · 窗口与口型 ----------------

def check_storyboard(sb: dict, timeline: dict) -> list:
    try:
        from .pipeline_contracts import unique_ids
    except ImportError:
        from pipeline_contracts import unique_ids
    if not unique_ids(sb.get("shots", [])):
        return [{"item": "分镜ID唯一非空", "pass": False, "evidence": "先修正schema与唯一性"}]
    shots = sorted(sb.get("shots", []), key=lambda s: s["window"]["start"])
    song_dur = float(timeline.get("duration_sec", 0))
    checks = []

    def add(name, ok, evidence=""):
        checks.append({"item": name, "pass": bool(ok), "evidence": str(evidence)[:200]})

    # ① 首尾相接，且真正从 0 覆盖到全曲末尾（仅检查相邻窗口会漏掉整体平移）
    gaps = []
    for a, b in zip(shots, shots[1:]):
        d = float(b["window"]["start"]) - float(a["window"]["end"])
        if abs(d) > 0.001:
            gaps.append(f"{a['id']}->{b['id']}:{d:+.3f}")
    endpoints_ok = bool(shots)
    if shots:
        endpoints_ok = (abs(float(shots[0]["window"]["start"])) <= 0.001 and
                        abs(float(shots[-1]["window"]["end"]) - song_dur) <= 0.05)
    add("窗口首尾相接且覆盖全曲", not gaps and endpoints_ok,
        f"gaps={';'.join(gaps)} start={shots[0]['window']['start'] if shots else None} "
        f"end={shots[-1]['window']['end'] if shots else None} song={song_dur}")

    # ② 合计=全曲长
    total = sum(float(s["window"]["end"]) - float(s["window"]["start"]) for s in shots)
    add("合计等于全曲长", abs(total - song_dur) < 0.05, f"sum={total:.3f} song={song_dur}")

    # ③ 口型景别白名单 + 生成职责一致
    bad = [s["id"] for s in shots if s.get("lip_sync") and s.get("shot_type") not in LIPSYNC_WHITELIST]
    bad_roles = [s["id"] for s in shots
                 if s.get("generation_role") !=
                 ("lip_sync" if s.get("lip_sync") else "narrative")]
    add("口型景别白名单且生成职责一致", not bad and not bad_roles,
        f"bad_shot_type={bad} bad_generation_role={bad_roles}")

    # ④ 项目演唱策略可配置；未设置比例时不强加审美配额。
    lip_n = sum(1 for s in shots if s.get("lip_sync"))
    ratio = lip_n / len(shots) if shots else 0
    policy = sb.get("performance_policy", {})
    bounds = policy.get("lip_ratio_range")
    ratio_ok = (bounds is None or (isinstance(bounds, list) and len(bounds) == 2
                and 0 <= float(bounds[0]) <= ratio <= float(bounds[1]) <= 1))
    add("口型占比服从项目演唱策略", ratio_ok, f"ratio={ratio:.2f} policy={bounds}")

    # ⑤ 项目配置的连续演唱约束
    run = mx = 0
    for s in shots:
        run = run + 1 if s.get("lip_sync") else 0
        mx = max(mx, run)
    limit = policy.get("consecutive_lip_limit")
    add("连续口型服从项目演唱策略", limit is None or mx <= int(limit), f"max_run={mx} policy={limit}")

    # ⑥ 无手写秒数（分镜自由文本字段）
    sec_hits = [s["id"] for s in shots
                if SECONDS_RE.search(str(s.get("visual_concept", "")) + str(s.get("camera", "")))]
    add("分镜无手写秒数", not sec_hits, ",".join(sec_hits))

    # ⑦ 视觉切点引用音乐依据，旧结构只用于迁移诊断。
    boundaries = sorted({round(float(ln[k]), 2) for ln in timeline.get("lines", [])
                         for k in ("start", "end")} | {0.0, round(song_dur, 2)})
    interludes = [(float(ln["start"]), float(ln["end"]))
                  for ln in timeline.get("lines", []) if ln["type"] == "interlude"]
    # 时间轴未覆盖的尾部（如尾奏）视同间奏
    last_end = max((float(ln["end"]) for ln in timeline.get("lines", [])), default=0.0)
    if last_end < song_dur:
        interludes.append((last_end, song_dur))
    bad_cuts = []
    for s in shots[:-1]:
        e = float(s["window"]["end"])
        near_boundary = any(abs(round(e, 2) - b) < 0.2 for b in boundaries)
        in_interlude = any(st + 0.2 < e < en - 0.2 for st, en in interludes)
        if not (near_boundary or in_interlude):
            bad_cuts.append(f"{s['id']}@{round(e, 2)}")
    if sb.get("timing_mode") == "music_referenced_visual_edit":
        anchor_map = sb.get("music_anchors", {})
        bad_refs = [s["id"] for s in shots if any(
            s.get("timing_refs", {}).get(key) not in anchor_map
            or abs(float(s["window"][key]) - float(anchor_map.get(
                s.get("timing_refs", {}).get(key), -1))) > 0.001 for key in ("start", "end"))
            or not s.get("cut_reason")]
        add("切点引用音乐依据且有剪辑理由", not bad_refs, str(bad_refs))
    else:
        add("旧分镜需迁移为音乐引用编辑方案", False, ";".join(bad_cuts))

    # ⑧ 歌词行全覆盖（几何判定：行中点落入某窗口即算覆盖——兼容一窗多行，BUG-05 修法）
    vocal_lines = [ln for ln in timeline.get("lines", []) if ln["type"] != "interlude"]
    missing = []
    for ln in vocal_lines:
        mid = (float(ln["start"]) + float(ln["end"])) / 2
        if not any(float(s["window"]["start"]) <= mid <= float(s["window"]["end"])
                   for s in shots):
            missing.append(ln["index"])
    add("歌词行全覆盖(几何)", not missing, str(missing))

    # ⑨ 故事分镜必须由 ZY Story Visual Development 产出，且每镜具备因果链与 Delta。
    required_story = REQUIRED_STORY_FIELDS
    bad_story = [s.get("id") for s in shots
                 if any(not str(s.get(k, "")).strip() for k in required_story)]
    add("ZY故事分镜完整", sb.get("story_mode") == "zy_story_visual_development" and not bad_story,
        f"mode={sb.get('story_mode')} bad={bad_story[:5]}")

    return checks


def probe_audio_duration(path: Path):
    if not path.is_file():
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def resolve_source_audio(proj: Path, timeline: dict):
    declared = timeline.get("source_audio")
    if declared:
        path = Path(declared)
        candidate = path if path.is_absolute() else proj / path
        if candidate.is_file():
            return candidate
    audio_extensions = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}
    candidates = sorted(p for p in (proj / "input").glob("*")
                        if p.is_file() and p.suffix.lower() in audio_extensions
                        and "16k" not in p.stem.lower())
    return candidates[0] if len(candidates) == 1 else None


def audit_prompts(proj: Path, timeline: dict, style: dict) -> tuple[dict, bool]:
    """复用关5实时检查；不写指纹或批准，供CLI和项目审计共同调用。"""
    has_protagonist = any((proj / "input").glob("protagonist.*")) or (proj/'state/reference_roles.json').is_file()
    report = {"gate": 5, "checks": [], "shots": []}
    all_ok = True
    roles = None
    if (proj/'state/reference_roles.json').is_file():
        try:
            try:
                from .reference_roles import require as require_roles
            except ImportError:
                from reference_roles import require as require_roles
            roles, _ = require_roles(proj)
            role_errors = []
        except (OSError, ValueError, KeyError, TypeError) as exc:
            role_errors = [str(exc)]
        report['checks'].append({'item': '多人物原图与托管职责当前有效', 'pass': not role_errors,
                                 'evidence': '; '.join(role_errors)})
        all_ok &= not role_errors
    storyboard_path = proj / "state" / "storyboard.json"
    storyboard = load(storyboard_path) if storyboard_path.exists() else {"shots": []}
    try:
        from .pipeline_contracts import unique_ids
    except ImportError:
        from pipeline_contracts import unique_ids
    unique = unique_ids(storyboard.get("shots", []))
    report["checks"].append({"item": "分镜ID唯一非空", "pass": unique})
    all_ok &= unique
    shot_map = {s.get("id"): s for s in storyboard.get("shots", [])}
    prompt_ids = []
    file_ids = []
    for pf in sorted((proj / "prompts").glob("SH*.json")):
        prompt = load(pf)
        prompt_id = prompt.get("meta", {}).get("shot_id")
        prompt_ids.append(prompt_id)
        expected_id = pf.stem
        file_ids.append(expected_id)
        shot = shot_map.get(prompt_id)
        checks = check_prompt(prompt, timeline, style, has_protagonist, shot)
        if roles:
            role_ok = (prompt.get('meta', {}).get('character_bindings') == roles['characters']
                       and prompt.get('meta', {}).get('character_fingerprint') == 'sha256:'+sha256_file(proj/'state/reference_roles.json'))
            checks.append({'item': '多人物身份与服装逐镜绑定一致', 'pass': role_ok,
                           'evidence': '命名人物原图与职责登记逐项比较，不回退单一protagonist'})
        if storyboard.get('story_mode') == 'zy_story_visual_development':
            try:
                from .crew_execution import validate_motion_package
                from .pipeline_contracts import digest
            except ImportError:
                from crew_execution import validate_motion_package
                from pipeline_contracts import digest
            try:
                motion_errors = validate_motion_package(prompt, load(proj/'prompts/motion'/pf.name), digest(pf))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                motion_errors = [f'运动包无法核验: {exc}']
            checks.append({'item': '视频运动包完整保留剧组和导演事实', 'pass': not motion_errors,
                           'evidence': '; '.join(motion_errors)})
        if shot is not None:
            checks.append({"shot_id": prompt_id, "idx": 21, "item": "当前镜头设计指纹一致",
                           "pass": prompt.get("meta", {}).get("shot_design_fingerprint") == shot_fingerprint(shot),
                           "evidence": "镜头设计变更后必须重新编译"})
        report["shots"].append({"file": pf.name, "checks": checks,
                                "fingerprint": sha256_file(pf)})
        all_ok &= all(c["pass"] for c in checks)
    coverage_ok = (bool(shot_map) and len(prompt_ids) == len(set(prompt_ids)) and
                   set(prompt_ids) == set(shot_map) and prompt_ids == file_ids)
    report["checks"].append({"item": "提示词完整覆盖分镜且文件名一致", "pass": coverage_ok,
                             "evidence": f"prompts={len(prompt_ids)} shots={len(shot_map)} "
                                         f"filenames_match={prompt_ids == file_ids}"})
    all_ok &= coverage_ok
    try:
        development = load(proj / "state" / "development.json")
        dev_errors = validate_development(development, storyboard, style, proj)
    except (OSError, ValueError) as exc:
        dev_errors = [str(exc)]
    report["checks"].append({"item": "ZY视觉开发来源与连续性契约", "pass": not dev_errors,
                              "evidence": "; ".join(dev_errors)})
    all_ok &= not dev_errors
    dependencies = {
        "story_plan_fingerprint": proj / "state" / "story_plan.json",
        "timeline_fingerprint": proj / "state" / "timeline.json",
        "visual_edit_fingerprint": proj / "state" / "visual_edit_plan.json",
    }
    dependency_ok = all(path.is_file() and storyboard.get(key) == "sha256:" + sha256_file(path)
                        for key, path in dependencies.items())
    if (proj / "state" / "music_structure.json").is_file():
        dependency_ok &= storyboard.get("music_structure_fingerprint") == "sha256:" + sha256_file(proj / "state" / "music_structure.json")
    dev_path = proj / "state" / "development.json"
    if dev_path.is_file():
        expected_dev = "sha256:" + sha256_file(dev_path)
        dependency_ok &= all(load(proj / "prompts" / s["file"]).get("meta", {}).get(
            "development_fingerprint") == expected_dev for s in report["shots"])
    report["checks"].append({"item": "制作包依赖指纹仍有效", "pass": bool(dependency_ok),
                              "evidence": "修改上游后必须重新编译"})
    all_ok &= dependency_ok
    return report, bool(all_ok)


def main():
    diagnostic = len(sys.argv) == 4 and sys.argv[1] == 'gate1' and sys.argv[3] == '--diagnostic'
    if (len(sys.argv) != 3 and not diagnostic) or sys.argv[1] not in ("gate1", "gate5", "gate6"):
        print(__doc__)
        sys.exit(2)
    proj = Path(sys.argv[2])
    try:
        from .pipeline_contracts import require_no_transaction
    except ImportError:
        from pipeline_contracts import require_no_transaction
    require_no_transaction(proj)
    timeline = load(proj / "state" / "timeline.json")
    # BUG-06 修法：style.json 在关2才产出，关1不得依赖它
    style = load(proj / "state" / "style.json") if (proj / "state" / "style.json").exists() else {}

    if sys.argv[1] == "gate1":
        lyrics = (proj / "input" / "lyrics.txt").read_text(encoding="utf-8") \
            if (proj / "input" / "lyrics.txt").exists() else ""
        audio_path = resolve_source_audio(proj, timeline)
        checks = check_timeline(timeline, lyrics,
                                probe_audio_duration(audio_path) if audio_path else None)
        report = {"gate": 1, "checks": checks}
        try:
            from .pipeline_contracts import timing_authority
        except ImportError:
            from pipeline_contracts import timing_authority
        report["timing_authority"] = timing_authority(timeline)
        report["structural_pass"] = all(c["pass"] for c in checks)
        try:
            from .timing_evidence import errors as timing_source_errors
        except ImportError:
            from timing_evidence import errors as timing_source_errors
        report['timing_source_errors'] = timing_source_errors(proj, timeline)
        report["production_allowed"] = (report["structural_pass"] and report["timing_authority"] == "production"
                                        and not report['timing_source_errors'])
        report["stage"] = "production" if report["production_allowed"] else "provisional" if report["structural_pass"] else "structural_failed"
        report['diagnostic_only'] = diagnostic
        if not diagnostic:
            checks.extend([
                {'item': '正式时间权威已批准', 'pass': report['timing_authority'] == 'production', 'evidence': report['timing_authority']},
                {'item': '实际ASR来源及逐行复核有效', 'pass': not report['timing_source_errors'],
                 'evidence': '; '.join(report['timing_source_errors'])}])
        all_ok = report['structural_pass'] if diagnostic else report['production_allowed']
        n_pass, n_total = sum(1 for c in checks if c["pass"]), len(checks)
        report["summary"] = {"pass": all_ok, "passed": n_pass, "total": n_total}
        write_gate_artifacts(proj, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(0 if all_ok else 1)

    if sys.argv[1] == "gate5":
        report, all_ok = audit_prompts(proj, timeline, style)
    else:
        sb = load(proj / "state" / "storyboard.json")
        checks = check_storyboard(sb, timeline)
        report = {"gate": 6, "checks": checks}
        all_ok = all(c["pass"] for c in checks)

    if sys.argv[1] == "gate6":
        n_pass = sum(1 for c in report["checks"] if c["pass"])
        n_total = len(report["checks"])
    else:
        top_checks = report.get("checks", [])
        n_pass = (sum(1 for s in report["shots"] for c in s["checks"] if c["pass"]) +
                  sum(1 for c in top_checks if c["pass"]))
        n_total = sum(len(s["checks"]) for s in report["shots"]) + len(top_checks)
    report["summary"] = {"pass": all_ok, "passed": n_pass, "total": n_total}
    if sys.argv[1] == "gate5" and all_ok:
        persist_prompt_fingerprints(proj, report)
    write_gate_artifacts(proj, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
