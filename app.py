"""
小红书冷启动成功率分析报告 — Streamlit 应用
通过本地 FastAPI 代理调用火山引擎方舟（密钥仅在后端环境变量）+ 可选 Supabase + 可选 Stripe
"""

from __future__ import annotations

import json
import os
import secrets as py_secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import quote
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from llm_client import LlmUserError, call_llm_backend

# 价格带（可在此列表扩展）
PRICE_BAND_OPTIONS: List[str] = [
    "0–20元",
    "20–50元",
    "50–99元",
    "100–199元",
    "200–399元",
    "400–699元",
    "700元以上",
]

MONTHLY_BUDGET_OPTIONS = [
    "1万以内",
    "1-3万",
    "3-5万",
    "5-10万",
    "10万以上",
]

XHS_BASE_OPTIONS = [
    "无账号从零开始",
    "有账号但无内容",
    "已有少量笔记",
    "已有一定内容积累",
]

# ---------------------------------------------------------------------------
# System prompt：专业分析师 + 固定 6 大模块 + 输出格式（含摘要 JSON 便于前端展示）
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_ANALYST = """你是一位资深「小红书品牌营销分析师」，只服务【从未在小红书投放过、0站内数据、新入场】的商家。文风：专业、判断清晰、可执行；为显著缩短生成时间，必须遵守下文篇幅与结构，禁止灌水、禁止长论证。

请从用户消息读取：品类、月预算、产品核心卖点；以及可能提供的品牌名、价格带、目标人群、账号基础等辅助约束。只基于这些信息输出。

【性能与篇幅（必须严格遵守，用于显著缩短生成时间）】
1) 全文总字数严格控制在 100–1500 字（中文，含标点与 Markdown 符号）。
2) 六大模块（见后文）每一模块只允许 150–250 字：先给 1 句结论，再给 3–6 条要点（短句，用无序列表 `-`），禁止长段落铺陈与重复论证。
3) 禁止输出与业务无关的铺垫、免责声明长段、重复总结；禁止复述提示词/系统设定；禁止输出「模型思考过程」或中间推理。
4) 为降低耗时：优先输出「结论 → 关键依据（可极短） → 可执行动作」，不要展开案例细节。

【输出顺序（必须严格遵守，不得颠倒）】
A) 先输出 <<<SUMMARY_JSON>>>…<<<END_SUMMARY_JSON>>>（字段必须齐全，且必须包含 dimension_scores；两个标记字符串必须原样出现；JSON 内不得再包含上述标记子串）。
B) JSON 之后，直接输出 Markdown 完整报告正文：六大模块（见后文），遵守标题规范。

【Markdown 标题规范（必须严格遵守：解决「小标题太大」）】
1) 六大模块大标题统一使用二级标题：`## 一、…` 至 `## 六、…`（禁止使用一级标题 `#`）。
2) 模块内小标题统一使用四级标题，且必须加粗：写成 `#### **1) 标题**` 形式，按需递增编号（禁止使用 `###` 作为模块内小标题）。
3) 除上述小标题自带加粗外，每个模块正文内额外加粗（`**…**`）不超过 2 处。

【六大模块（必须全部出现、顺序固定、不可省略）】
## 一、市场赛道机会分析
## 二、目标人群匹配度深度分析
## 三、产品适配性与内容可行性分析
## 四、小红书30天冷启动营销策略
## 五、冷启动成功率综合判定
## 六、冷启动执行标准与止损红线

每个模块须用短句覆盖关键维度（赛道/人群/内容/30天节奏与预算/成功-风险-目标/指标与止损），但禁止展开成长段落与案例细节。

【SUMMARY_JSON 字段（必须齐全）】
JSON 必须包含：score、rating_label、highlights、pitfalls、strategy_brief、execution、dimension_scores。
建议长度上限（中文）：rating_label 12 字内；highlights / pitfalls / execution 各 80 字内；strategy_brief 100 字内（为总篇幅服务，越短越好）。

【分维度打分（必须严格遵守：百分制）】
1) dimension_scores 结构固定为（键名必须一字不差）：
"dimension_scores": {
  "市场定位匹配度": {"score": 0-25 的整数, "comment": "一句话点评（<=20 字）"},
  "卖点竞争力": {"score": 0-25 的整数, "comment": "一句话点评（<=20 字）"},
  "价格带合理性": {"score": 0-25 的整数, "comment": "一句话点评（<=20 字）"},
  "冷启动可操作性": {"score": 0-25 的整数, "comment": "一句话点评（<=20 字）"}
}
2) SUMMARY_JSON.score 必须为 0–100 的整数，且严格等于上述四个 score 之和（四维度各 0–25，合计 0–100）；不得出现与维度之和不一致的数值。

【禁止项】
1) 禁止输出任何 HTML、CSS、JS、代码围栏（禁止使用 ```）；除 SUMMARY_JSON 标记块外，禁止任何代码块。
2) 禁止输出任何历史参考文案/品牌/模板内容（包括但不限于「帅康」等）；禁止输出与当前用户输入无关的内容。
3) 若需引用「数据」，请用行业公开口径/平台趋势推演/合理区间估计等方式表述，避免编造不可验证的具体站内明细数据。
4) 不要复述你被提供过的参考资料/原文/示例；不要引用既有方案原句。

只输出：A 的标记 JSON 块 + B 的 Markdown 正文；不要输出任何前后缀说明文字。
"""


def _sanitize_no_legacy_reference(text: str) -> str:
    """
    轻量过滤：移除包含禁用词的段落，防止旧参考文案/品牌名意外泄露到最终结果。
    只做纯文本处理，不引入额外 API 调用。
    """
    if not text:
        return ""
    forbidden = ["帅康"]
    t = str(text)
    for w in forbidden:
        t = t.replace(w, "")
    blocks = [b.strip() for b in t.split("\n\n")]
    kept = []
    for b in blocks:
        if not b:
            continue
        low = b
        hit = any(w in low for w in forbidden)
        if hit:
            continue
        kept.append(b)
    return "\n\n".join(kept).strip()


def set_styles() -> None:
    st.set_page_config(
        page_title="小红书冷启动成功率分析",
        page_icon="✦",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    css = """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;600&family=Noto+Serif+SC:wght@500;600;700&display=swap');
      header {visibility: hidden;}
      footer {visibility: hidden;}
      #MainMenu {visibility: hidden;}
      :root{
        --bg: #F8F3EA;
        --card: #F1EADF;
        --text: #1A1A1A;
        --muted: #8A8276;
        --border: rgba(0,0,0,0.18);
        --shadow: 0 10px 30px rgba(0,0,0,0.04);
        --accent: #000000;
        --radius: 12px;
      }
      html, body, [class*="css"] {
        font-family: 'Noto Sans SC', system-ui, -apple-system, Segoe UI, Roboto, Arial, 'PingFang SC','Microsoft YaHei', sans-serif;
        color: var(--text);
      }
      html, body { max-width: 100%; overflow-x: hidden; }
      * { box-sizing: border-box; }
      .stApp { background: var(--bg); }
      section.main > div { max-width: 860px; padding-top: 42px; padding-bottom: 42px; }

      /* subtle fade-in */
      section.main > div > div { animation: fadeIn .28s ease-out; }
      @keyframes fadeIn { from{ opacity:0; transform: translateY(6px);} to{ opacity:1; transform: translateY(0);} }

      .herti-eyebrow{
        font-family: 'Noto Serif SC', 'Times New Roman', serif;
        font-size: 12px;
        letter-spacing: .28em;
        text-transform: uppercase;
        color: var(--muted);
        text-align:center;
        font-style: italic;
        margin: 0 0 10px 0;
      }
      .herti-title{
        font-family: 'Noto Serif SC', 'Times New Roman', serif;
        font-weight: 700;
        letter-spacing: .18em;
        text-transform: uppercase;
        text-align:center;
        font-size: 42px;
        line-height: 1.12;
        margin: 0 0 10px 0;
      }
      .herti-subtitle{
        text-align:center;
        color: var(--text);
        font-size: 15px;
        letter-spacing: .12em;
        margin: 0 0 18px 0;
      }
      .herti-lead{
        text-align:center;
        color: var(--muted);
        line-height: 1.9;
        font-size: 14px;
        margin: 0 0 18px 0;
      }
      .herti-meta{
        text-align:center;
        color: var(--muted);
        font-size: 12px;
        letter-spacing: .06em;
        margin: 0 0 26px 0;
      }
      .card{
        background: var(--card);
        border: 1px solid rgba(0,0,0,0.10);
        border-radius: var(--radius);
        box-shadow: var(--shadow);
        padding: 18px 18px;
        margin: 14px 0;
      }
      .card-title{
        font-family: 'Noto Serif SC', 'Times New Roman', serif;
        letter-spacing: .10em;
        text-transform: uppercase;
        font-size: 12px;
        color: var(--muted);
        margin: 0 0 10px 0;
      }
      .card-body{ color: var(--text); }

      /* inputs */
      .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] > div {
        border-radius: var(--radius) !important;
        border: 1px solid rgba(0,0,0,0.18) !important;
        background: rgba(255,255,255,0.55) !important;
      }
      .stTextInput input:focus, .stTextArea textarea:focus,
      div[data-baseweb="select"] > div:focus-within {
        border-color: #000 !important;
        box-shadow: 0 0 0 2px rgba(0,0,0,0.08) !important;
      }

      /* buttons — primary black */
      div.stButton > button, button[kind="primary"]{
        border-radius: var(--radius) !important;
        width: 100% !important;
        height: 44px !important;
        font-weight: 600 !important;
        border: 1px solid #000 !important;
        transition: transform .06s ease, background .12s ease, color .12s ease, opacity .12s ease;
      }
      div.stButton > button:active{ transform: scale(0.985); }
      div.stButton > button[kind="primary"], button[kind="primary"]{
        background: #000 !important;
        color: #fff !important;
      }
      div.stButton > button[kind="primary"]:hover{ opacity: 0.92 !important; }
      div.stButton > button:not([kind="primary"]){
        background: transparent !important;
        color: #000 !important;
      }
      div.stButton > button:not([kind="primary"]):hover{
        background: rgba(255,255,255,0.40) !important;
      }

      /* tiny clear button */
      .small-clear button{
        background: transparent !important;
        color: #000 !important;
        border: 1px solid rgba(0,0,0,0.25) !important;
        border-radius: var(--radius) !important;
        min-width: 2.3rem !important;
        width: 2.3rem !important;
        height: 2.3rem !important;
        padding: 0 !important;
        font-size: 16px !important;
        line-height: 1 !important;
        opacity: .9;
      }

      .text-link button{
        background: transparent !important;
        border: none !important;
        width: auto !important;
        height: auto !important;
        padding: 0 !important;
        color: var(--muted) !important;
        font-weight: 500 !important;
      }
      .text-link button:hover{ color: #000 !important; text-decoration: underline !important; }

      .footer-note{ text-align:center; color: var(--muted); font-size: 12px; margin-top: 14px; }
      .hr-soft{ height: 1px; background: rgba(0,0,0,0.10); margin: 18px 0; }
      .progress-line{ text-align:center; color: var(--muted); font-size: 12px; letter-spacing: .08em; margin: 0 0 8px 0; }

      /* segmented radios => HERTI buttons */
      div[role="radiogroup"]{
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        padding: 2px 0;
      }
      div[role="radiogroup"] > label{
        margin: 0 !important;
      }
      div[role="radiogroup"] > label > div{
        border: 1px solid #000 !important;
        border-radius: var(--radius) !important;
        padding: 10px 14px !important;
        background: rgba(255,255,255,0.55) !important;
        color: #000 !important;
        transition: transform .06s ease, background .12s ease, color .12s ease, opacity .12s ease;
      }
      div[role="radiogroup"] > label > div:active{ transform: scale(0.985); }
      div[role="radiogroup"] > label[data-checked="true"] > div{
        background: #000 !important;
        color: #fff !important;
      }
      div[role="radiogroup"] svg{ display:none !important; } /* hide default radio icon */
      div[role="radiogroup"] p{ margin: 0 !important; font-weight: 600 !important; }

      @media (max-width: 768px) {
        section.main > div { padding-top: 26px; padding-bottom: 30px; padding-left: 12px; padding-right: 12px; }
        .herti-title{ font-size: 32px; letter-spacing: .14em; }
        .card{ padding: 16px 14px; }
      }

      @media (max-width: 480px) {
        section.main > div { max-width: 100%; padding-left: 10px; padding-right: 10px; }
        .herti-title{ font-size: 28px; letter-spacing: .12em; }
        .herti-lead{ font-size: 13px; line-height: 1.85; }
        .herti-meta{ margin-bottom: 18px; }
        .card{ margin: 12px 0; }

        /* bigger tap targets on phone */
        div.stButton > button, button[kind="primary"]{ height: 48px !important; }
        .small-clear button{ min-width: 2.6rem !important; width: 2.6rem !important; height: 2.6rem !important; }

        /* iOS: prevent input zoom (16px+) */
        .stTextInput input, .stTextArea textarea { font-size: 16px !important; }
        .stTextArea textarea { line-height: 1.7 !important; }

        /* Streamlit columns sometimes squeeze; force wrap */
        div[data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: 10px !important; }
        div[data-testid="column"] { width: 100% !important; flex: 1 1 100% !important; }
      }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Secrets / env
# ---------------------------------------------------------------------------
def _secret(key: str, default: str = "") -> str:
    try:
        v = st.secrets.get(key)
        return str(v).strip() if v else default
    except Exception:
        return default


def get_llm_backend_url() -> str:
    """报告生成服务根地址，例如 http://127.0.0.1:8000（不含尾斜杠）。"""
    v = _secret("LLM_BACKEND_URL") or _secret("BACKEND_URL")
    if v:
        return str(v).strip()
    for k in ("LLM_BACKEND_URL", "BACKEND_URL"):
        ev = os.getenv(k)
        if ev and str(ev).strip():
            return str(ev).strip()
    return ""


def get_public_app_url() -> str:
    u = _secret("PUBLIC_APP_URL") or os.getenv("PUBLIC_APP_URL", "").strip()
    if u:
        return u.rstrip("/")
    try:
        ctx = st.context
        if hasattr(ctx, "headers"):
            h = ctx.headers
            origin = (h.get("Origin") or h.get("Referer") or "").strip()
            if origin:
                return origin.rstrip("/")
    except Exception:
        pass
    return "http://localhost:8501"


def supabase_enabled() -> bool:
    url = _secret("SUPABASE_URL") or os.getenv("SUPABASE_URL", "").strip()
    key = _secret("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY", "").strip()
    return bool(url and key)


def stripe_enabled() -> bool:
    return bool(_secret("STRIPE_SECRET_KEY") and _secret("STRIPE_PRICE_ID"))


def stripe_subscription_enabled() -> bool:
    return bool(_secret("STRIPE_SECRET_KEY") and _secret("STRIPE_SUBSCRIPTION_PRICE_ID"))


def get_supabase_client():
    from supabase import create_client

    url = _secret("SUPABASE_URL") or os.getenv("SUPABASE_URL", "").strip()
    key = _secret("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY", "").strip()
    return create_client(url, key)


def sb_set_session(client: Any, access_token: str, refresh_token: str) -> None:
    try:
        client.auth.set_session(access_token, refresh_token)
    except Exception:
        pass


def parse_model_output(raw: str) -> Tuple[Optional[Dict[str, Any]], str, Optional[str]]:
    """
    返回 (summary_dict_or_none, report_markdown, error_message)
    """
    summary: Optional[Dict[str, Any]] = None
    rest = raw
    start_tag = "<<<SUMMARY_JSON>>>"
    end_tag = "<<<END_SUMMARY_JSON>>>"
    s0 = raw.find(start_tag)
    s1 = raw.find(end_tag)
    if s0 != -1 and s1 != -1 and s1 > s0:
        js = raw[s0 + len(start_tag) : s1].strip()
        rest = (raw[:s0] + raw[s1 + len(end_tag) :]).strip()
        try:
            summary = json.loads(js)
        except json.JSONDecodeError:
            return None, rest.strip(), "摘要 JSON 解析失败，请重试。"
        if not isinstance(summary, dict):
            return None, rest.strip(), "摘要 JSON 格式不正确，请重试。"

    # 报告正文：去掉可能残留的空行
    report_md = _sanitize_no_legacy_reference(rest.strip())
    if summary is None:
        return None, report_md, "模型未按约定输出摘要块（<<<SUMMARY_JSON>>>…<<<END_SUMMARY_JSON>>>），请重试。"
    if len(report_md) < 80:
        return summary, report_md, "报告正文过短或未按约定输出，请重试。"
    if "```" in report_md:
        return summary, report_md, "报告正文不应包含代码围栏（```），请重试。"

    # 摘要字段同样做轻量过滤，防止禁用词出现在卡片里
    try:
        for k, v in list((summary or {}).items()):
            if isinstance(v, str):
                summary[k] = _sanitize_no_legacy_reference(v)
    except Exception:
        pass

    return summary, report_md, None


def clamp_int_score(v: Any) -> int:
    try:
        n = int(round(float(v)))
    except Exception:
        return 0
    return max(0, min(100, n))


def _ensure_local_history() -> List[Dict[str, Any]]:
    """
    本地历史（仅 session 内）：当未配置 Supabase 时也能查看/下载。
    """
    if "local_history" not in st.session_state or not isinstance(st.session_state.get("local_history"), list):
        st.session_state.local_history = []
    return st.session_state.local_history


def _push_local_history(snapshot: Dict[str, Any], *, summary: Optional[Dict[str, Any]], report_md: str) -> None:
    hist = _ensure_local_history()
    item = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": snapshot,
        "summary": summary or {},
        "report_md": report_md or "",
    }
    hist.insert(0, item)
    # 仅保留最近 30 条，避免 session 过大
    del hist[30:]


def _make_printable_html(snapshot: Dict[str, Any], summary: Dict[str, Any], report_md: str) -> str:
    """
    生成“可打印 HTML”：
    - 用户下载后用浏览器打印：可保存为 PDF；也可系统导出/截图为图片
    - 不依赖额外 PDF 渲染依赖，保证 Streamlit Cloud 可用
    """
    title = str(snapshot.get("brand_name") or snapshot.get("category") or "小红书冷启动报告").strip()
    created = datetime.now().strftime("%Y-%m-%d %H:%M")
    score = summary.get("score", "")
    rating = summary.get("rating_label", "")
    safe_md = (report_md or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    css = """
<style>
  @page { size: A4; margin: 14mm; }
  body { font-family: KaiTi, "KaiTi_GB2312", "STKaiti", "Songti SC", "Noto Serif SC", serif; color:#111; }
  .meta { color:#555; font-size:12px; margin-top:4px; }
  h1 { font-size:20px; margin:0; color:#7a6328; }
  .badge { margin-top:10px; padding:10px 12px; border:1px solid #ddd; border-radius:10px; }
  .badge b { color:#7a6328; }
  pre { white-space: pre-wrap; word-break: break-word; font-size:13px; line-height:1.75; margin-top:14px; }
  .hr { height:1px; background:#eee; margin:14px 0; }
</style>
"""
    head = f"<h1>{_esc_html(title)}</h1><div class='meta'>生成时间：{_esc_html(created)}</div>"
    badge = (
        "<div class='badge'>"
        f"<b>总分</b>：{_esc_html(str(score))}/100"
        + (f"｜{_esc_html(str(rating))}" if rating else "")
        + "</div>"
    )
    return f"<!doctype html><html><head><meta charset='utf-8'>{css}</head><body>{head}{badge}<div class='hr'></div><pre>{safe_md}</pre></body></html>"


def _normalize_dimension_scores(summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    返回四维度打分字典：
    - 优先使用模型在 SUMMARY_JSON 中返回的 dimension_scores
    - 若缺失/格式不合法，则基于总分自动补齐
    """
    total = clamp_int_score(summary.get("score"))
    dims = summary.get("dimension_scores")
    names = ["市场定位匹配度", "卖点竞争力", "价格带合理性", "冷启动可操作性"]
    if isinstance(dims, dict) and all(k in dims for k in names):
        out: Dict[str, Dict[str, Any]] = {}
        ssum = 0
        for k in names:
            item = dims.get(k) or {}
            sc = clamp_int_score((item.get("score") if isinstance(item, dict) else None))
            sc = max(0, min(25, int(sc)))
            cm = ""
            if isinstance(item, dict) and isinstance(item.get("comment"), str):
                cm = _sanitize_no_legacy_reference(item.get("comment") or "").strip()
            out[k] = {"score": sc, "comment": cm[:20]}
            ssum += sc
        # 确保总分一致：以维度之和为准（0-100），并同步回 summary
        total100 = max(0, min(100, ssum))
        summary["score"] = total100
        return out

    # 自动补齐（保持 0-25，四项之和 <= 100）
    base = max(0, min(100, total if total else 75))
    q, r = divmod(base, 4)
    scores = [q] * 4
    for i in range(r):
        scores[i] += 1
    comments = [
        "与人群/场景匹配度",
        "差异化与可传播性",
        "客单/毛利与转化阻力",
        "动作清晰度与执行成本",
    ]
    out = {}
    for k, sc, cm in zip(names, scores, comments):
        out[k] = {"score": max(0, min(25, int(sc))), "comment": cm}
    summary["score"] = max(0, min(100, sum(v["score"] for v in out.values())))
    summary["dimension_scores"] = out
    return out


# ---------------------------------------------------------------------------
# Session state：表单持久化
# ---------------------------------------------------------------------------
FORM_DEFAULTS: Dict[str, Any] = {
    "form_brand": "",
    "form_category": "",
    "form_price_band": PRICE_BAND_OPTIONS[2],
    "form_selling": "",
    "form_audience": "",
    "form_xhs_base": XHS_BASE_OPTIONS[0],
    "form_monthly_budget": MONTHLY_BUDGET_OPTIONS[1],
}


def init_session() -> None:
    for k, v in FORM_DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if "page" not in st.session_state:
        st.session_state.page = "home"
    if "report_data" not in st.session_state:
        st.session_state.report_data = None
    if "report_markdown" not in st.session_state:
        st.session_state.report_markdown = ""
    if "input_snapshot" not in st.session_state:
        st.session_state.input_snapshot = {}
    if "report_unlocked" not in st.session_state:
        st.session_state.report_unlocked = False
    if "pending_pay_token" not in st.session_state:
        st.session_state.pending_pay_token = ""
    if "last_analysis_id" not in st.session_state:
        st.session_state.last_analysis_id = ""
    if "llm_busy" not in st.session_state:
        st.session_state.llm_busy = False


def field_row_clear(label: str, widget_key: str, clear_key: str, *, multiline: bool = False) -> Any:
    c1, c2 = st.columns([1, 0.14], gap="small")
    with c1:
        if multiline:
            val = st.text_area(label, key=widget_key, height=120)
        else:
            val = st.text_input(label, key=widget_key)
    with c2:
        st.markdown('<div class="small-clear">', unsafe_allow_html=True)
        if st.button("×", key=clear_key, help="清空"):
            st.session_state[widget_key] = ""
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    return val


def select_row_clear(label: str, options: List[str], widget_key: str, clear_key: str, *, default_idx: int = 0) -> str:
    c1, c2 = st.columns([1, 0.14], gap="small")
    with c1:
        choice = st.selectbox(label, options, key=widget_key)
    with c2:
        st.markdown('<div class="small-clear">', unsafe_allow_html=True)
        if st.button("×", key=clear_key, help="恢复默认"):
            st.session_state[widget_key] = options[default_idx]
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    return str(choice)


# ---------------------------------------------------------------------------
# Supabase：云端历史存档（无登录版）
# ---------------------------------------------------------------------------
def save_analysis_cloud(sb: Any, snapshot: Dict[str, Any], report_md: str) -> None:
    aid = str(uuid.uuid4())
    row = {
        "id": aid,
        "user_id": None,
        "brand_name": snapshot.get("brand_name") or "",
        "category": snapshot.get("category") or "",
        "price_range": snapshot.get("price_band") or "",
        "analysis_result": report_md,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        sb.table("merchant_analyses").insert(row).execute()
        st.session_state.last_analysis_id = aid
    except Exception as ex:
        st.warning(f"云端保存失败（请检查 Supabase 表与表结构）：{ex}")


def load_history(sb: Any) -> List[Dict[str, Any]]:
    try:
        res = (
            sb.table("merchant_analyses")
            .select("id, created_at, brand_name, category, price_range")
            .order("created_at", desc=True)
            .limit(30)
            .execute()
        )
        return list(res.data or [])
    except Exception:
        return []


def fetch_analysis_by_id(sb: Any, aid: str) -> Optional[Dict[str, Any]]:
    try:
        res = (
            sb.table("merchant_analyses")
            .select("*")
            .eq("id", aid)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Stripe：Checkout 返回后校验 session
# ---------------------------------------------------------------------------
def _qp_first(qp: Any, key: str) -> str:
    v = qp.get(key)
    if v is None:
        return ""
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v)


def try_unlock_from_stripe_query(sb: Optional[Any]) -> None:
    if not stripe_enabled() and not stripe_subscription_enabled():
        return
    qp = st.query_params
    sid = _qp_first(qp, "session_id")
    token = _qp_first(qp, "unlock")
    if not sid or not token:
        return
    if token != st.session_state.get("pending_pay_token"):
        return
    import stripe

    stripe.api_key = _secret("STRIPE_SECRET_KEY")
    try:
        sess = stripe.checkout.Session.retrieve(sid, expand=["subscription"])
        paid = False
        if getattr(sess, "mode", None) == "subscription":
            paid = getattr(sess, "status", None) == "complete"
        else:
            paid = getattr(sess, "payment_status", None) == "paid"

        if paid:
            st.session_state.report_unlocked = True
            st.session_state.pending_pay_token = ""
            st.query_params.clear()
            st.success("支付成功，已解锁完整报告。")
            if st.session_state.get("report_markdown") or st.session_state.get("report_data"):
                st.session_state.page = "result"
            # 可选：回写云端解锁状态
            try:
                aid = st.session_state.get("last_analysis_id")
                if sb is not None and aid and st.session_state.get("sb_access"):
                    sb_set_session(sb, st.session_state.sb_access, st.session_state.sb_refresh or "")
                    sb.table("analyses").update({"is_unlocked": True}).eq("id", aid).execute()
            except Exception:
                pass
            st.rerun()
    except Exception as ex:
        st.error(f"支付校验失败：{ex}")


def create_checkout_session(unlock_token: str, *, mode: str = "payment") -> str:
    import stripe

    stripe.api_key = _secret("STRIPE_SECRET_KEY")
    if mode == "subscription":
        price_id = _secret("STRIPE_SUBSCRIPTION_PRICE_ID")
        checkout_mode = "subscription"
    else:
        price_id = _secret("STRIPE_PRICE_ID")
        checkout_mode = "payment"
    base = get_public_app_url()
    success = f"{base}/?session_id={{CHECKOUT_SESSION_ID}}&unlock={quote(unlock_token, safe='')}"
    cancel = f"{base}/"
    session = stripe.checkout.Session.create(
        mode=checkout_mode,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success,
        cancel_url=cancel,
        metadata={"unlock_token": unlock_token, "checkout_mode": checkout_mode},
    )
    return str(session.url or "")


# ---------------------------------------------------------------------------
# UI：表单页
# ---------------------------------------------------------------------------
def build_generation_prompt(snapshot: Dict[str, str]) -> str:
    return f"""请基于以下信息生成报告（核心约束：商家从未在小红书投放、0站内数据、新入场）。

【必填】
1. 品类：{snapshot["category"]}
2. 月预算：{snapshot["monthly_budget"]}
3. 产品核心卖点：{snapshot["selling_points"]}

【选填 / 辅助】
- 品牌名称：{snapshot.get("brand_name") or "（未提供）"}
- 单品价格带：{snapshot.get("price_band") or "（未提供）"}
- 目标人群：{snapshot.get("target_audience") or "（未提供）"}
- 当前小红书基础：{snapshot.get("xhs_base") or "（未提供）"}

请严格按 system 指令输出：先 SUMMARY_JSON 标记块；再输出 Markdown 完整报告正文（六大模块）。"""


def _load_history_into_state(sb: Any, row_id: str) -> None:
    full = fetch_analysis_by_id(sb, row_id)
    if not full:
        return
    st.session_state.last_analysis_id = str(full.get("id") or "")
    snap = {
        "brand_name": full.get("brand_name") or "",
        "category": full.get("category") or "",
        "price_band": full.get("price_range") or "",
    }
    st.session_state.input_snapshot.update(snap)
    st.session_state.form_brand = snap["brand_name"]
    st.session_state.form_category = snap["category"]
    st.session_state.form_price_band = snap["price_band"] or FORM_DEFAULTS["form_price_band"]
    st.session_state.report_data = None
    st.session_state.report_markdown = str(full.get("analysis_result") or "")
    st.session_state.report_unlocked = True
    st.session_state.page = "result"
    st.rerun()


def _load_local_history_into_state(item: Dict[str, Any]) -> None:
    snap = dict(item.get("snapshot") or {})
    st.session_state.input_snapshot = snap
    st.session_state.form_brand = str(snap.get("brand_name") or "")
    st.session_state.form_category = str(snap.get("category") or "")
    st.session_state.form_price_band = str(snap.get("price_band") or FORM_DEFAULTS["form_price_band"])
    st.session_state.form_selling = str(snap.get("selling_points") or "")
    st.session_state.form_audience = str(snap.get("target_audience") or "")
    st.session_state.form_xhs_base = str(snap.get("xhs_base") or FORM_DEFAULTS["form_xhs_base"])
    st.session_state.form_monthly_budget = str(snap.get("monthly_budget") or FORM_DEFAULTS["form_monthly_budget"])
    st.session_state.report_data = item.get("summary") or {}
    st.session_state.report_markdown = str(item.get("report_md") or "")
    st.session_state.report_unlocked = True
    st.session_state.page = "result"
    st.rerun()


def render_home_page(sb: Optional[Any]) -> None:
    st.markdown('<div class="herti-eyebrow">MERCHANT LAUNCH<br/>— a growth strategy map —</div>', unsafe_allow_html=True)
    st.markdown('<div class="herti-title">商船下水</div>', unsafe_allow_html=True)
    st.markdown('<div class="herti-subtitle">商家冷启动分析器</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="herti-lead">流量藏在细节里，<br/>但你的产品里，有一套专属的增长逻辑，<br/>正等待被精准激活。</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="herti-meta">20+分析维度 · 1次智能生成 · 约1–2分钟</div>', unsafe_allow_html=True)

    # 只保留一个简洁输入框 + 两个核心按钮
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">SUMMARY</div>', unsafe_allow_html=True)
    st.text_input("一句话概括你的产品（选填）", key="form_brand")
    st.markdown("</div>", unsafe_allow_html=True)

    gen_busy = bool(st.session_state.get("llm_busy"))
    if st.button("开始分析", type="primary", disabled=gen_busy, key="btn_start_home"):
        st.session_state.page = "form"
        st.rerun()

    st.markdown('<div class="footer-note">请输入你的产品信息，开始分析</div>', unsafe_allow_html=True)


def render_history_page(sb: Optional[Any]) -> None:
    st.markdown('<div class="progress-line">HISTORY</div>', unsafe_allow_html=True)
    st.markdown('<div class="herti-title" style="font-size:28px;letter-spacing:.12em;">HISTORY</div>', unsafe_allow_html=True)
    st.markdown('<div class="herti-subtitle">历史分析</div>', unsafe_allow_html=True)

    if st.button("返回首页", key="back_home_from_history"):
        st.session_state.page = "home"
        st.rerun()

    if sb is None or not supabase_enabled():
        st.info("未配置 Supabase，将展示本地历史（仅本次会话内保存）。")
        hist_local = _ensure_local_history()
        if not hist_local:
            st.info("暂无历史记录。")
            return
        for row in hist_local:
            snap = row.get("snapshot") or {}
            label = f"{str(row.get('created_at',''))[:19]} · {snap.get('category','')} · {snap.get('price_band','')}"
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown(f'<div class="card-title">{_esc_html(label)}</div>', unsafe_allow_html=True)
            selling = str(snap.get("selling_points", "") or "")
            st.markdown(
                f'<div class="card-body" style="color:var(--muted);font-size:13px;line-height:1.8;">'
                f'卖点：{_esc_html(selling[:120])}{"…" if len(selling)>120 else ""}</div>',
                unsafe_allow_html=True,
            )
            if st.button("打开这份报告", key=f"open_local_hist_{row.get('id','')}"):
                _load_local_history_into_state(row)
            st.markdown("</div>", unsafe_allow_html=True)
        return
    hist = load_history(sb)
    if not hist:
        st.info("暂无历史记录。")
        return

    for row in hist:
        label = f"{row.get('created_at', '')[:19]} · {row.get('category', '')} · {row.get('price_range', '')}"
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown(f'<div class="card-title">{_esc_html(label)}</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="card-body" style="color:var(--muted);font-size:13px;line-height:1.8;">'
            f'品牌：{_esc_html(str(row.get("brand_name",""))[:40])}</div>',
            unsafe_allow_html=True,
        )
        if st.button("打开这份报告", key=f"open_hist_{row['id']}"):
            _load_history_into_state(sb, row["id"])
        st.markdown("</div>", unsafe_allow_html=True)


def render_form_page(sb: Optional[Any]) -> None:
    st.markdown('<div class="progress-line">产品信息录入 01/06</div>', unsafe_allow_html=True)
    st.progress(1 / 6)
    st.markdown('<div class="herti-title" style="font-size:28px;letter-spacing:.12em;">PRODUCT INPUT</div>', unsafe_allow_html=True)
    st.markdown('<div class="herti-subtitle">请填写你的产品信息</div>', unsafe_allow_html=True)

    top_l, top_r = st.columns(2, gap="medium")
    with top_l:
        if st.button("返回首页", key="back_home_from_form"):
            st.session_state.page = "home"
            st.rerun()
    with top_r:
        # 按需求移除“历史分析”入口（保留空列用于布局对齐）
        st.markdown("")

    # 6 张输入卡片（按你要求的结构）；保持原有清空按钮与表单持久化
    st.markdown('<div class="card"><div class="card-title">01 / BRAND</div>', unsafe_allow_html=True)
    field_row_clear("品牌名（选填）", "form_brand", "clr_brand")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="card"><div class="card-title">02 / CATEGORY</div>', unsafe_allow_html=True)
    field_row_clear("品类（必填）", "form_category", "clr_category")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="card"><div class="card-title">03 / PRICE BAND</div>', unsafe_allow_html=True)
    select_row_clear("价格带（必填）", PRICE_BAND_OPTIONS, "form_price_band", "clr_price", default_idx=2)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="card"><div class="card-title">04 / AUDIENCE</div>', unsafe_allow_html=True)
    field_row_clear("目标人群（选填）", "form_audience", "clr_audience")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="card"><div class="card-title">05 / SELLING POINTS</div>', unsafe_allow_html=True)
    field_row_clear("核心卖点（必填）", "form_selling", "clr_selling", multiline=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="card"><div class="card-title">06 / BUDGET</div>', unsafe_allow_html=True)
    # 保留原有功能：小红书基础仍可选择，但合并到预算卡片中保持 6 卡结构
    select_row_clear("当前小红书基础（必填）", XHS_BASE_OPTIONS, "form_xhs_base", "clr_xhs", default_idx=0)
    select_row_clear("营销预算（必填）", MONTHLY_BUDGET_OPTIONS, "form_monthly_budget", "clr_budget", default_idx=1)
    st.markdown("</div>", unsafe_allow_html=True)

    backend_url = get_llm_backend_url().rstrip("/")
    internal_tok = (_secret("INTERNAL_API_TOKEN") or os.getenv("INTERNAL_API_TOKEN", "")).strip()
    gen_busy = bool(st.session_state.get("llm_busy"))

    if not backend_url:
        st.error("未连接API：请配置 `LLM_BACKEND_URL`（例如 http://127.0.0.1:8000）。")

    b1, b2 = st.columns(2, gap="medium")
    with b1:
        if st.button("返回首页", disabled=gen_busy, key="back_home_bottom"):
            st.session_state.page = "home"
            st.rerun()
    with b2:
        if st.button("生成分析报告", type="primary", disabled=gen_busy or not bool(backend_url), key="btn_generate"):
            snap = {
                "brand_name": str(st.session_state.form_brand).strip(),
                "category": str(st.session_state.form_category).strip(),
                "price_band": str(st.session_state.form_price_band),
                "selling_points": str(st.session_state.form_selling).strip(),
                "target_audience": str(st.session_state.form_audience).strip(),
                "xhs_base": str(st.session_state.form_xhs_base),
                "monthly_budget": str(st.session_state.form_monthly_budget),
            }
            miss = []
            if not snap["category"]:
                miss.append("品类")
            if not snap["monthly_budget"]:
                miss.append("营销预算")
            if not snap["selling_points"]:
                miss.append("核心卖点")
            if miss:
                st.error("请先补全必填项：" + "、".join(miss))
            else:
                st.session_state.input_snapshot = snap
                st.session_state.llm_busy = True
                st.session_state._llm_run_gen = True
                st.rerun()

    if st.session_state.pop("_llm_run_gen", False):
        try:
            snap = dict(st.session_state.get("input_snapshot") or {})
            user_prompt = build_generation_prompt(snap)
            with st.spinner("正在生成你的冷启动增长方案，约 1–2 分钟…"):
                try:
                    raw = call_llm_backend(
                        backend_url,
                        internal_tok,
                        SYSTEM_PROMPT_ANALYST,
                        user_prompt,
                        on_retry=lambda m: st.warning(m),
                    )
                except LlmUserError as e:
                    more = []
                    if getattr(e, "error_code", ""):
                        more.append(f"错误码：{e.error_code}")
                    if getattr(e, "request_id", ""):
                        more.append(f"请求ID：{e.request_id}")
                    suffix = f"（{'，'.join(more)}）" if more else ""
                    st.error(e.message + suffix)
                    if getattr(e, "debug", ""):
                        with st.expander("调试信息（可复制给管理员）", expanded=False):
                            st.code(str(e.debug)[:4000])
                    raw = None
                except Exception:
                    st.error("网络异常，请检查网络后重试")
                    raw = None

            if raw is None:
                pass
            else:
                summary, report_md, err = parse_model_output(raw)
                if err:
                    st.error(err + " 请重试。")
                    with st.expander("原始返回"):
                        st.code(raw[:8000])
                else:
                    if summary:
                        summary["score"] = clamp_int_score(summary.get("score"))
                        # 分维度打分：优先使用模型返回，缺失则自动补齐（并同步总分为 0-80）
                        if isinstance(summary, dict):
                            _normalize_dimension_scores(summary)

                    st.session_state.report_data = summary or {
                        "score": 0,
                        "rating_label": "待评估",
                        "highlights": "",
                        "pitfalls": "",
                        "strategy_brief": "",
                        "execution": "",
                    }
                    st.session_state.report_markdown = report_md
                    need_pay = stripe_enabled() or stripe_subscription_enabled()
                    st.session_state.report_unlocked = not need_pay
                    st.session_state.pending_pay_token = py_secrets.token_urlsafe(16) if need_pay else ""

                    if sb is not None and supabase_enabled():
                        save_analysis_cloud(sb, snap, report_md)
                    # 无 Supabase 也保留本地历史（仅本次会话）
                    try:
                        _push_local_history(snap, summary=st.session_state.report_data, report_md=report_md)
                    except Exception:
                        pass

                    st.session_state.page = "result"
                    st.rerun()
        finally:
            st.session_state.llm_busy = False


# ---------------------------------------------------------------------------
# UI：结果页
# ---------------------------------------------------------------------------
def _esc_html(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_summary_cards(summary: Dict[str, Any]) -> None:
    score = clamp_int_score(summary.get("score"))
    # 结果页要求：评分/报告均为楷体（仅结果页注入 CSS，这里只调整卡片内字体）
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            f'<div class="card"><div class="card-title">分数 Score</div>'
            f'<div style="font-family: KaiTi, 楷体, KaiTi_GB2312, STKaiti, serif; font-weight:700; font-size:34px; letter-spacing:.08em;">{score}</div>'
            f'<div style="color:var(--muted);font-size:12px;letter-spacing:.08em;margin-top:6px;">{_esc_html(str(summary.get("rating_label", "")))}</div></div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="card"><div class="card-title">亮点 Highlights</div><p style="font-family: KaiTi, 楷体, KaiTi_GB2312, STKaiti, serif; font-size:13px;line-height:1.9;color:var(--text);margin:0;">{_esc_html(str(summary.get("highlights", "")))}</p></div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f'<div class="card"><div class="card-title">痛点 Pitfalls</div><p style="font-family: KaiTi, 楷体, KaiTi_GB2312, STKaiti, serif; font-size:13px;line-height:1.9;color:var(--text);margin:0;">{_esc_html(str(summary.get("pitfalls", "")))}</p></div>',
            unsafe_allow_html=True,
        )
    c4, c5 = st.columns(2)
    with c4:
        st.markdown(
            f'<div class="card"><div class="card-title">策略 Strategy</div><p style="font-family: KaiTi, 楷体, KaiTi_GB2312, STKaiti, serif; font-size:13px;line-height:1.9;color:var(--text);margin:0;">{_esc_html(str(summary.get("strategy_brief", "")))}</p></div>',
            unsafe_allow_html=True,
        )
    with c5:
        st.markdown(
            f'<div class="card"><div class="card-title">行动 Action</div><p style="font-family: KaiTi, 楷体, KaiTi_GB2312, STKaiti, serif; font-size:13px;line-height:1.9;color:var(--text);margin:0;">{_esc_html(str(summary.get("execution", "")))}</p></div>',
            unsafe_allow_html=True,
        )


def render_result_page(sb: Optional[Any]) -> None:
    snap = st.session_state.get("input_snapshot") or {}
    title = snap.get("brand_name") or snap.get("category") or "分析报告"

    # 结果页：强制报告区域楷体（不影响表单页/首页）
    st.markdown(
        """
<style>
  .kai-scope,
  .kai-scope * {
    font-family: KaiTi, "KaiTi_GB2312", "STKaiti", "Songti SC", "Noto Serif SC", serif !important;
  }
  .kai-scope div[data-testid="stMarkdownContainer"],
  .kai-scope div[data-testid="stMarkdownContainer"] * {
    font-family: KaiTi, "KaiTi_GB2312", "STKaiti", "Songti SC", "Noto Serif SC", serif !important;
  }
  /* 报告内标题：暗金色 + 仍为楷体栈（由上层继承） */
  .kai-scope div[data-testid="stMarkdownContainer"] h1,
  .kai-scope div[data-testid="stMarkdownContainer"] h2,
  .kai-scope div[data-testid="stMarkdownContainer"] h3,
  .kai-scope div[data-testid="stMarkdownContainer"] h4 {
    color: #7a6328 !important;
  }
  .kai-scope div[data-testid="stMarkdownContainer"] h1 { font-size: 1.22rem !important; line-height: 1.55 !important; font-weight: 700 !important; }
  .kai-scope div[data-testid="stMarkdownContainer"] h2 { font-size: 1.16rem !important; line-height: 1.55 !important; font-weight: 700 !important; }
  .kai-scope div[data-testid="stMarkdownContainer"] h3 { font-size: 1.10rem !important; line-height: 1.55 !important; font-weight: 700 !important; }
  .kai-scope div[data-testid="stMarkdownContainer"] h4 { font-size: 1.06rem !important; line-height: 1.55 !important; font-weight: 700 !important; }
  .kai-scope div[data-testid="stMarkdownContainer"] p,
  .kai-scope div[data-testid="stMarkdownContainer"] li {
    color: #1a1a1a !important;
    font-size: 1.00rem !important;
    line-height: 1.75 !important;
  }
  .report-title-kai-gold {
    font-family: KaiTi, "KaiTi_GB2312", "STKaiti", "Songti SC", "Noto Serif SC", serif !important;
    color: #7a6328 !important;
  }
</style>
""",
        unsafe_allow_html=True,
    )

    top_l, top_r = st.columns(2, gap="medium")
    with top_l:
        if st.button("返回首页", key="back_home_from_result"):
            st.session_state.page = "home"
            st.rerun()
    with top_r:
        if st.button("重新分析", key="back_form"):
            st.session_state.page = "form"
            st.rerun()

    st.markdown('<div class="progress-line">RESULT</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="herti-title report-title-kai-gold" style="font-size:30px;letter-spacing:.12em;">你的冷启动增长方案</div>',
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="herti-meta">{_esc_html(str(title))}</div>', unsafe_allow_html=True)
    summary = st.session_state.report_data or {}

    # 模块 1：总分（百分制）+ 四维度分数（总分下直接展示）
    dims: Dict[str, Dict[str, Any]] = {}
    if isinstance(summary, dict):
        dims = _normalize_dimension_scores(summary)
        total100 = clamp_int_score(summary.get("score"))
        rating = str(summary.get("rating_label") or "").strip()
        st.markdown(
            "<div class='card'><div class='card-title report-title-kai-gold'>总分 Total</div></div>",
            unsafe_allow_html=True,
        )
        lines = [f"- 总分：{total100}/100" + (f"｜{rating}" if rating else "")]
        for k in ["市场定位匹配度", "卖点竞争力", "价格带合理性", "冷启动可操作性"]:
            it = dims.get(k) or {}
            sc = it.get("score", "")
            cm = it.get("comment", "")
            lines.append(f"- {k}：{sc}/25｜{cm}")
        st.markdown('<div class="kai-scope">\n' + "\n".join(lines) + "\n</div>", unsafe_allow_html=True)

        # 模块 2：分维度打分 + 亮点/痛点/要做的事（简洁）
        st.markdown(
            "<div class='card'><div class='card-title report-title-kai-gold'>要点 Key Points</div></div>",
            unsafe_allow_html=True,
        )
        pts: List[str] = []
        pts.append("#### **分维度打分 Dimension**")
        for k in ["市场定位匹配度", "卖点竞争力", "价格带合理性", "冷启动可操作性"]:
            it = dims.get(k) or {}
            pts.append(f"- {k}：{it.get('score','')}/25｜{it.get('comment','')}")
        hi = str(summary.get("highlights") or "").strip()
        pi = str(summary.get("pitfalls") or "").strip()
        stg = str(summary.get("strategy_brief") or "").strip()
        exe = str(summary.get("execution") or "").strip()
        if hi:
            pts.append("")
            pts.append("#### **商家亮点 Highlights**")
            pts.append(f"- {hi}")
        if pi:
            pts.append("")
            pts.append("#### **商家痛点 Pitfalls**")
            pts.append(f"- {pi}")
        if stg or exe:
            pts.append("")
            pts.append("#### **要做的事 Action**")
            if stg:
                pts.append(f"- {stg}")
            if exe:
                pts.append(f"- {exe}")
        st.markdown('<div class="kai-scope">\n' + "\n".join(pts) + "\n</div>", unsafe_allow_html=True)

    # 价格带建议：不改模型逻辑，只做 UI 展示
    price_band = str(snap.get("price_band") or "").strip()
    budget = str(snap.get("monthly_budget") or "").strip()
    price_hint = (f"当前选择：{price_band}。" if price_band else "未选择价格带。") + (f" 预算：{budget}。" if budget else "")
    st.markdown(
        f'<div class="card"><div class="card-title">价格带 Price Band</div><p style="font-family: KaiTi, KaiTi_GB2312, STKaiti, Songti SC, Noto Serif SC, serif;margin:0;line-height:1.9;font-size:13px;color:var(--text);">{_esc_html(price_hint)}</p></div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="hr-soft"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="card-title report-title-kai-gold" style="text-align:center;margin-top:4px;">完整报告 Full Report</div>',
        unsafe_allow_html=True,
    )

    full_md = st.session_state.get("report_markdown") or ""
    unlocked = bool(st.session_state.get("report_unlocked"))

    if (stripe_enabled() or stripe_subscription_enabled()) and not unlocked:
        st.info("完整版报告需付费解锁。以下为预览（前约 800 字）。")
        preview = full_md[:800] + ("…" if len(full_md) > 800 else "")
        st.markdown('<div class="kai-scope">\n' + preview + "\n</div>", unsafe_allow_html=True)
        c_pay, c_sub = st.columns(2)
        with c_pay:
            if stripe_enabled():
                try:
                    pay_url = create_checkout_session(st.session_state.pending_pay_token, mode="payment")
                    if pay_url:
                        st.link_button("按次付费 · 解锁本报告", pay_url)
                except Exception as ex:
                    st.error(f"创建按次支付链接失败：{ex}")
        with c_sub:
            if stripe_subscription_enabled():
                try:
                    sub_url = create_checkout_session(st.session_state.pending_pay_token, mode="subscription")
                    if sub_url:
                        st.link_button("订阅 · 解锁本报告", sub_url)
                except Exception as ex:
                    st.error(f"创建订阅链接失败：{ex}")
    else:
        with st.expander("展开查看完整报告", expanded=True):
            st.markdown('<div class="kai-scope">\n' + full_md + "\n</div>", unsafe_allow_html=True)

    # 最后：分享/导出（放在完整报告后面）
    share_on = bool(st.session_state.get("share_mode"))
    cshare1, cshare2 = st.columns(2, gap="medium")
    with cshare1:
        if st.button("分享结果（生成长图）", key="btn_share_long_poster"):
            st.session_state.share_mode = not share_on
            st.rerun()
    with cshare2:
        try:
            export_summary = st.session_state.report_data if isinstance(st.session_state.report_data, dict) else {}
            html = _make_printable_html(snap if isinstance(snap, dict) else {}, export_summary, str(full_md))
            st.download_button(
                "下载结果HTML（可打印为PDF）",
                data=html.encode("utf-8"),
                file_name="xhs_result.html",
                mime="text/html",
            )
        except Exception:
            pass

    if st.session_state.get("share_mode"):
        st.markdown('<div class="card"><div class="card-title report-title-kai-gold">分享长图预览</div></div>', unsafe_allow_html=True)
        st.caption("提示：向下滑动查看完整长图，iPhone 可直接截图/长截图保存。")
        try:
            export_summary = st.session_state.report_data if isinstance(st.session_state.report_data, dict) else {}
            poster_html = _make_printable_html(snap if isinstance(snap, dict) else {}, export_summary, str(full_md))
            st.components.v1.html(poster_html, height=900, scrolling=True)
        except Exception:
            pass

    st.markdown('<div class="footer-note">© 2026 Merchant Launch · 仅供策略参考</div>', unsafe_allow_html=True)


def _escape_md_title(s: str) -> str:
    return s.replace("<", "&lt;").replace(">", "&gt;")


def main() -> None:
    set_styles()
    init_session()

    sb = None
    if supabase_enabled():
        try:
            sb = get_supabase_client()
            if st.session_state.sb_access and st.session_state.sb_refresh:
                sb_set_session(sb, st.session_state.sb_access, st.session_state.sb_refresh)
        except Exception as ex:
            st.warning(f"Supabase 初始化失败：{ex}")
            sb = None

    # Stripe 支付回跳：必须在路由页面前处理，否则回到表单页时无法解锁
    try_unlock_from_stripe_query(sb)

    page = str(st.session_state.get("page") or "home")
    if page == "result" and (st.session_state.report_markdown or st.session_state.report_data):
        render_result_page(sb)
        return
    if page == "form":
        render_form_page(sb)
        return
    st.session_state.page = "home"
    render_home_page(sb)


if __name__ == "__main__":
    main()
