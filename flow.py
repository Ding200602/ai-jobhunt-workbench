# -*- coding: utf-8 -*-
"""
求职全流程定义与状态管理。
把"从无简历到约面"的整个求职链路固化成 7 个阶段，供可视化面板实时展示。
状态机由主控(Agent)推进，面板 SSE 接收状态变更实时刷新。
"""
import copy
import threading

STAGES = [
    {
        "id": "s1", "name": "① 起点·信息收集", "desc": "真实素材与目标确认",
        "steps": [
            {"id": "s1a", "name": "确认个人背景与真实素材", "status": "done"},
            {"id": "s1b", "name": "确认目标岗位 / 城市 / 薪资", "status": "done"},
        ],
    },
    {
        "id": "s2", "name": "② 简历撰写", "desc": "从无到有",
        "steps": [
            {"id": "s2a", "name": "生成简历初稿（算法向+真人向）", "status": "done"},
        ],
    },
    {
        "id": "s3", "name": "③ 清洗定稿", "desc": "去 AI 味 · 干净投递版",
        "steps": [
            {"id": "s3a", "name": "去 AI 味改写、内部标注清除", "status": "done"},
            {"id": "s3b", "name": "导出 docx / pdf 定稿", "status": "done"},
        ],
    },
    {
        "id": "s4", "name": "④ 导入平台", "desc": "BOSS 在线简历清洗",
        "steps": [
            {"id": "s4a", "name": "教育经历改回真实学校", "status": "done"},
            {"id": "s4b", "name": "清除虚构工作经历（测试脏数据）", "status": "done"},
            {"id": "s4c", "name": "补入 3 条真实项目经历", "status": "done"},
            {"id": "s4d", "name": "个人优势替换为定稿文案", "status": "done"},
            {"id": "s4e", "name": "求职意向切换为「实习」", "status": "doing", "note": "网页端受限，需 App 操作"},
            {"id": "s4f", "name": "清理他人错误附件", "status": "done"},
            {"id": "s4g", "name": "上传真人头像", "status": "todo", "note": "待用户提供照片"},
        ],
    },
    {
        "id": "s5", "name": "⑤ 搜岗过滤", "desc": "北京 / 长春 / 济南 · 多源全面搜索",
        "steps": [
            {"id": "s5a", "name": "北京 AI 产品实习 12 岗清单", "status": "done"},
            {"id": "s5b", "name": "长春 6 岗清单（供给有限）", "status": "done"},
            {"id": "s5c", "name": "济南 8 岗清单", "status": "done"},
            {"id": "s5d", "name": "全面搜索首轮完成：智联421岗 + 应届生33入口", "status": "done"},
            {"id": "s5e", "name": "多源岗位导出 Excel（job_searches_20260910_0254.xlsx，454行）", "status": "done"},
            {"id": "s5f", "name": "零散渠道采集器 scatter_channels.py 落地：高校公告400 + 零散岗位165 + 招聘会100 + 社区汇总29", "status": "done"},
        ],
    },
    {
        "id": "s6", "name": "⑥ 投递执行", "desc": "打招呼 · 发送简历",
        "steps": [
            {"id": "s6a", "name": "投递 17 个高匹配岗（已投 3 / 17）", "status": "doing", "note": "14 个被风控静默拦截，待恢复"},
            {"id": "s6b", "name": "逐个记录投递结果", "status": "todo"},
        ],
    },
    {
        "id": "s7", "name": "⑦ 跟进·约面", "desc": "HR 沟通 · 面试管理",
        "steps": [
            {"id": "s7a", "name": "处理 HR 回复（中关村学院待回）", "status": "doing", "note": "HR 索要简历，待用户确认发送"},
            {"id": "s7b", "name": "约面管理 / 面试记录", "status": "todo"},
        ],
    },
]

_lock = threading.Lock()


def get_flow():
    with _lock:
        return copy.deepcopy(STAGES)


def set_step(stage_id, step_id, status, note=None):
    """更新某个子步骤状态，返回更新后的全流程。"""
    with _lock:
        for st in STAGES:
            if st["id"] == stage_id:
                for sp in st["steps"]:
                    if sp["id"] == step_id:
                        sp["status"] = status
                        if note is not None:
                            sp["note"] = note
                        return copy.deepcopy(STAGES)
    return None
