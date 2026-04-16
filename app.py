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
SYSTEM_PROMPT_ANALYST = """你是一位资深「小红书品牌营销分析师」，当前只服务【从未在小红书投放过、0站内数据、新入场】的商家。你的文风：专业、数据化、结构化、有判断、有依据、有策略，完全对标高水准品牌方案。

请等待用户输入：1）品类 2）月预算 3）产品核心卖点（用户还可能补充单品价格带、品牌名、目标人群、账号基础等，可作为辅助约束）。

生成一份完整、深度、数据感强、篇幅约2000字以上的《小红书冷启动成功率分析报告》。报告排版清晰、章节完整、内容极度详实，不允许简略、不允许空洞。

=== 输出固定6大模块（必须全部写满，不可省略）===
一、市场赛道机会分析（必须包含：品类供需格局、平台搜索趋势、行业竞争格局、红利判断、对标头部优秀方案的赛道定位）
二、目标人群匹配度深度分析（必须包含：核心人群画像、性别/年龄/城市/婚姻/消费力/场景偏好、高潜破圈人群、人群匹配度评分、匹配依据）
三、产品适配性与内容可行性分析（必须包含：内容适配类型、核心卖点承接、关键词布局、合规风险评估、可量产笔记方向）
四、小红书30天冷启动营销策略（必须包含：三阶段节奏：蓄水期→种草期→爆发期、预算分配比例、KFS种草模型、搜索卡位策略、流量路径）
五、冷启动成功率综合判定（必须包含：成功率评级、核心成功优势、核心风险点、30天量化目标）
六、冷启动执行标准与止损红线（必须包含：成功指标、内容底线、投放红线、止损条件）

=== 强制要求 ===
1. 全文必须≥2000字（中文），每个模块都要有完整推导，不写短句、不做简略版。
2. 只针对【从未投放过小红书】的新商家，不涉及历史站内数据、不做账号诊断。
3. 网页友好排版：使用 Markdown，大标题用 # / ##，小标题用 ###，有序列表、加粗重点；不要使用复杂 HTML。
4. 若需引用“数据”，请用“行业公开口径/平台趋势推演/合理区间估计”等方式表述，避免编造不可验证的具体站内数据。
5. 严禁输出任何与用户当前输入无关的旧参考文案/模板/案例内容；严禁提及任何参考品牌、历史资料或内部代号（包括但不限于“帅康”等）。如你在生成过程中出现这些词或相关段落，必须立即删除并重写为通用表达，只保留针对用户当前产品与信息的分析。
6. 不要复述“你被提供过的参考资料/原文/示例”；不要引用任何既有方案原句。输出必须是面向当前用户输入产品的原创分析与可执行策略。

=== 输出格式（必须严格遵守，便于程序解析）===
先输出一段 JSON（单行或多行均可），用如下标记包裹：

<<<SUMMARY_JSON>>>
{ ... JSON ... }
<<<END_SUMMARY_JSON>>>

JSON 字段必须为：
{
  "score": 0-100 的整数,
  "rating_label": "成功率评级文字（如：中高 / 中等偏上 等）",
  "highlights": "核心优点（亮点总结，200字以内，条理化）",
  "pitfalls": "痛点与避雷点（运营避坑清单，250字以内）",
  "strategy_brief": "冷启动策略要点总览（300字以内，覆盖蓄水/种草/爆发与预算逻辑）",
  "execution": "执行建议（可立即落地的行动清单，200字以内）"
}

然后在 JSON 之后输出完整 Markdown 报告正文（包含上述六大模块，标题清晰）。

注意：<<<SUMMARY_JSON>>> 与 <<<END_SUMMARY_JSON>>> 标记必须原样出现；JSON 内不要包含上述标记字符串。
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
    if len(report_md) < 500:
        return summary, report_md, "报告正文过短，可能被截断，请重试或调大模型输出限制。"

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

请严格按 system 指令输出（先 SUMMARY_JSON 标记块，再 Markdown 六大模块正文）。"""


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


def render_home_page(sb: Optional[Any]) -> None:
    st.markdown('<div class="herti-eyebrow">MERCHANT LAUNCH<br/>— a growth strategy map —</div>', unsafe_allow_html=True)
    st.markdown('<div class="herti-title">商船下水</div>', unsafe_allow_html=True)
    st.markdown('<div class="herti-subtitle">商家冷启动分析器</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="herti-lead">流量藏在细节里，<br/>但你的产品里，有一套专属的增长逻辑，<br/>正等待被精准激活。</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="herti-meta">20+分析维度 · 1次智能生成 · 约3分钟</div>', unsafe_allow_html=True)

    # 只保留一个简洁输入框 + 两个核心按钮
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="card-title">SUMMARY</div>', unsafe_allow_html=True)
    st.text_input("一句话概括你的产品（选填）", key="form_brand")
    st.markdown("</div>", unsafe_allow_html=True)

    gen_busy = bool(st.session_state.get("llm_busy"))
    if st.button("开始分析", type="primary", disabled=gen_busy, key="btn_start_home"):
        st.session_state.page = "form"
        st.rerun()

    if st.button("查看历史分析", key="btn_go_history"):
        st.session_state.page = "history"
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
        st.info("未配置 Supabase，无法查看云端历史。")
        return
    hist = load_history(sb)
    if not hist:
        st.info("暂无历史记录。")
        return

    for row in hist:
        snap = row.get("input_snapshot") or {}
        label = f"{row.get('created_at', '')[:19]} · {snap.get('category', '')} · {snap.get('price_band', '')}"
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown(f'<div class="card-title">{_esc_html(label)}</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="card-body" style="color:var(--muted);font-size:13px;line-height:1.8;">'
            f'卖点：{_esc_html(str(snap.get("selling_points",""))[:120])}…</div>',
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
        if st.button("查看历史分析", key="go_history_from_form"):
            st.session_state.page = "history"
            st.rerun()

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
            with st.spinner("正在生成你的冷启动增长方案，约 3 分钟…"):
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
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            f'<div class="card"><div class="card-title">SCORE</div>'
            f'<div style="font-family: Noto Serif SC, Times New Roman, serif; font-weight:700; font-size:34px; letter-spacing:.08em;">{score}</div>'
            f'<div style="color:var(--muted);font-size:12px;letter-spacing:.08em;margin-top:6px;">{_esc_html(str(summary.get("rating_label", "")))}</div></div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="card"><div class="card-title">HIGHLIGHTS</div><p style="font-size:13px;line-height:1.9;color:var(--text);margin:0;">{_esc_html(str(summary.get("highlights", "")))}</p></div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f'<div class="card"><div class="card-title">PITFALLS</div><p style="font-size:13px;line-height:1.9;color:var(--text);margin:0;">{_esc_html(str(summary.get("pitfalls", "")))}</p></div>',
            unsafe_allow_html=True,
        )
    c4, c5 = st.columns(2)
    with c4:
        st.markdown(
            f'<div class="card"><div class="card-title">CORE STRATEGY</div><p style="font-size:13px;line-height:1.9;color:var(--text);margin:0;">{_esc_html(str(summary.get("strategy_brief", "")))}</p></div>',
            unsafe_allow_html=True,
        )
    with c5:
        st.markdown(
            f'<div class="card"><div class="card-title">TACTICS</div><p style="font-size:13px;line-height:1.9;color:var(--text);margin:0;">{_esc_html(str(summary.get("execution", "")))}</p></div>',
            unsafe_allow_html=True,
        )


def render_result_page(sb: Optional[Any]) -> None:
    snap = st.session_state.get("input_snapshot") or {}
    title = snap.get("brand_name") or snap.get("category") or "分析报告"

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
    st.markdown('<div class="herti-title" style="font-size:30px;letter-spacing:.12em;">你的冷启动增长方案</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="herti-meta">{_esc_html(str(title))}</div>', unsafe_allow_html=True)
    summary = st.session_state.report_data or {}
    render_summary_cards(summary if isinstance(summary, dict) else {})

    # 价格带建议：不改模型逻辑，只做 UI 展示
    price_band = str(snap.get("price_band") or "").strip()
    budget = str(snap.get("monthly_budget") or "").strip()
    price_hint = (f"当前选择：{price_band}。" if price_band else "未选择价格带。") + (f" 预算：{budget}。" if budget else "")
    st.markdown(
        f'<div class="card"><div class="card-title">PRICE BAND</div><p style="margin:0;line-height:1.9;font-size:13px;color:var(--text);">{_esc_html(price_hint)}</p></div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="hr-soft"></div>', unsafe_allow_html=True)
    st.markdown('<div class="card-title" style="text-align:center;margin-top:4px;">FULL REPORT</div>', unsafe_allow_html=True)

    full_md = st.session_state.get("report_markdown") or ""
    unlocked = bool(st.session_state.get("report_unlocked"))

    if (stripe_enabled() or stripe_subscription_enabled()) and not unlocked:
        st.info("完整版报告需付费解锁。以下为预览（前约 800 字）。")
        preview = full_md[:800] + ("…" if len(full_md) > 800 else "")
        st.markdown(preview)
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
            st.markdown(full_md)

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
    if page == "history":
        render_history_page(sb)
        return
    if page == "form":
        render_form_page(sb)
        return
    st.session_state.page = "home"
    render_home_page(sb)


if __name__ == "__main__":
    main()
