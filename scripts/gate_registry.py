"""导出真实门禁入口与人工/外部环节，数量由实际报告计算。"""
import argparse
import json
from pathlib import Path

REGISTRY = [
    {"id": "production_costs", "phase": "delivery_package/ready", "entry": "production_costs.audit -> delivery_package cost_audit.json", "owner": "machine_and_recorded_evidence", "inputs": ["registered_jobs", "recorded_actual_fee_response", "settlement_events", "refund_evidence", "unspent_release"], "failure_exit": 1, "limits": "只核对记录一致性，不认证供应商账单签名；估价/预占不是实付，未登记历史费用不推算"},
    {"id": "backend_access_policy", "phase": "reserve/dispatch/mcp", "entry": "execution_policy.require_video_backend + mcp_host_bridge explicit opt-in", "owner": "machine_and_user_constraint", "inputs": ["current_project_execution_policy", "explicit_host_opt_in"], "failure_exit": 1, "limits": "默认关闭；本任务用户禁止使用后端。策略是本地约束记录，不是身份认证，也不拦截仓库外调用"},
    {"id": "mcp_broker", "phase": "external_handoff", "entry": "mcp_broker.py + mcp_host_bridge.js + lovart_adapter.py", "owner": "external_adapter", "inputs": ["live_original_process", "one_time_claim", "current_dispatch_basis", "exact_native_arguments", "raw_mcp_result", "request_archive"], "failure_exit": 1, "limits": "仅本地合成回执测试；历史只读schema实测不证明视频生成。本任务禁止实际后端调用"},
    {"id": "local_transform", "phase": "repair/admission", "entry": "local_transform.py --plan -> derived media_origin -> repair_state.close", "owner": "machine_and_review", "inputs": ["open_frozen_repair", "source_origin_chain", "explicit_frame_crop_scale_plan", "actual_process_receipt", "new_chapter_review"], "failure_exit": 1, "limits": "数码裁切/放大不创造真实机位或细节；不自动审片；无任意滤镜、变速、重复帧"},
    {"id": "delivery_package", "phase": "technical_verified/ready", "entry": "delivery_package.py + delivery_state.promote", "owner": "machine_and_review", "inputs": ["actual_master_video", "selected_embedded_audio", "authoritative_lrc", "actual_alignment_execution", "final_media_review", "verified_zip"], "failure_exit": 1, "limits": "口型复核声明不认证实际观看；缺包保持technical_verified，不自动接收"},
    {"id": "media_origin", "phase": "download/admission/delivery", "entry": "media_origin.py -> chapter_admission.require -> delivery_state.dependencies", "owner": "machine_and_review", "inputs": ["completed_provider_task", "actual_http_download", "media_sha256", "retained_user_authorization", "chapter_review"], "failure_exit": 1, "limits": "来源登记不自动批准动态质量；外部下载证据和授权文本不具身份签名"},
    {"id": "submission_jobs", "phase": "submission/budget", "entry": "submission_jobs.prepare/dispatch/poll/settle + mcp_broker", "owner": "external_adapter", "inputs": ["execution_policy", "current_contract", "immutable_request_archive", "native_request", "upload_receipts", "reservation", "actual_response", "actual_cost"], "failure_exit": 1, "limits": "未知不重派；本任务禁止实际后端，离线测试不冒充生成；不能全局拦截仓库外调用"},
    {"id": "workflow_execution", "phase": "timing/design/prompts/edit", "entry": "workflow_execution.py run/check --step gate1|gate5|crew_pipeline|crew_rehearsal|gate6", "owner": "machine", "inputs": ["actual_process", "current_inputs", "actual_report", "code_versions"], "failure_exit": 1, "limits": "五项实际命令回执，不认证观察，其余十二步执行链仍在建设"},
    {"id": "timing_source", "phase": "timing", "entry": "asr_capture.py -> timing_draft.py -> timing_evidence.py -> gate1", "owner": "machine_and_review", "inputs": ["original_audio", "official_lyrics", "actual_asr", "bound_per_line_review"], "failure_exit": 1, "limits": "识别和草稿均不自动批准句界；逐行声明不认证真实听辨"},
    {"id": "workflow_audit", "phase": "pre_generation/pre_batch/pre_delivery", "entry": "workflow_audit.py --phase", "owner": "machine_and_review", "inputs": ["current_project_state", "submission_contract", "actual_reviews", "delivery_record"], "failure_exit": 1, "limits": "只读当前检查；不证明历史顺序，不自动认证十二步完成或艺术通过"},
    {"id": "crew_rehearsal", "phase": "pre_prompts", "entry": "prompt_build.py -> crew_rehearsal.require_before_prompts", "owner": "machine_and_review", "inputs": ["development", "storyboard", "director_catalog"], "failure_exit": 1, "limits": "基础结构返工与制作桌，不是艺术批准"},
    {"id": "animatic", "phase": "pre_generation", "entry": "animatic_review.py init/check", "owner": "machine_and_review", "inputs": ["actual_animatic", "design", "timecoded_review"], "failure_exit": 1},
    {"id": "1", "phase": "timing", "entry": "gate_checks.py gate1", "owner": "machine", "inputs": ["timeline", "lyrics", "audio"], "failure_exit": 1},
    {"id": "2", "phase": "style", "entry": "SKILL.md 风格确认", "owner": "human", "inputs": ["style"], "failure_exit": None},
    {"id": "3", "phase": "identity", "entry": "submission_contract.py", "owner": "external_adapter", "inputs": ["character", "upload_receipt"], "failure_exit": 1},
    {"id": "4", "phase": "design", "entry": "visual_development.py / crew_pipeline.py", "owner": "machine_and_review", "inputs": ["development", "storyboard"], "failure_exit": 1},
    {"id": "5", "phase": "prompts", "entry": "gate_checks.py gate5", "owner": "machine", "inputs": ["prompts", "development", "style", "storyboard"], "failure_exit": 1},
    {"id": "6", "phase": "edit", "entry": "gate_checks.py gate6", "owner": "machine", "inputs": ["storyboard", "timeline"], "failure_exit": 1},
    {"id": "7", "phase": "submission", "entry": "submission_contract.py --project-dir", "owner": "external_adapter", "inputs": ["package", "references", "model_receipt", "budget"], "failure_exit": 1},
    {"id": "7.5", "phase": "prototype", "entry": "director_review_gate.py check", "owner": "machine_and_review", "inputs": ["review", "media", "dependencies"], "failure_exit": 1},
    {"id": "8", "phase": "batch", "entry": "external scheduler + ledger.py", "owner": "external_adapter", "inputs": ["review", "reservation", "clips"], "failure_exit": 1},
    {"id": "8.5", "phase": "repair", "entry": "repair_state.py open/close/assess/check + local_transform.py + visual_revision.py", "owner": "machine_and_review", "inputs": ["failed_media", "timecoded_failure_review", "frozen_repair_plan", "replacement_task_or_actual_local_transform", "new_chapter_review", "repair_assessment"], "failure_exit": 1, "limits": "绑定声明不认证真实听看；本地变换只支持显式裁切/缩放/按帧重剪"},
    {"id": "delivery", "phase": "final", "entry": "stitch_chapters.py + delivery_state.py check + align_verify.py + full_sequence review", "owner": "machine_and_review", "inputs": ["actual_media", "master", "chapter_reviews", "delivery_record", "verification_report"], "failure_exit": 1},
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps({"schema_version": 1, "gates": REGISTRY,
                       "check_count_policy": "读取实际gates报告checks，不使用20/20硬编码",
                       "only_gate_checks_subcommands": ["gate1", "gate5", "gate6"]}, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)

if __name__ == "__main__":
    main()
