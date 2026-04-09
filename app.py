import json
import os
import re
from typing import Any, Dict, Optional, Tuple, List

import streamlit as st
import requests


SYSTEM_PROMPT = """你是一位拥有8年小红书品牌运营经验的顶级策略顾问，曾服务过完美日记、花西子、珀莱雅等头部国货品牌，深度参与过超过200个品牌的小红书冷启动项目。你的分析风格是：数据驱动、逻辑严谨、建议落地可执行，不说废话，每一条建议都有明确的行动指引。

请基于用户提供的品牌信息，生成一份专业的小红书冷启动分析报告。

请严格按照以下JSON格式返回，只返回纯JSON，不要包含任何markdown符号或额外文字：

{
  "score": 数字(0-100),
  "verdict": "一句话总结冷启动潜力（15字以内）",
  "market": {
    "heat": "品类热度：该品类在小红书的搜索热度和内容增长趋势，100字以内",
    "competition": "竞争格局：该价格带主要竞争者现状，强弱如何，100字以内",
    "opportunity": "你的机会点：基于品牌卖点的差异化切入方向，100字以内"
  },
  "persona": {
    "profile": "基础画像：年龄、职业、生活状态，50字以内",
    "content": "内容偏好：她爱看的3种具体内容形式，80字以内",
    "purchase": "购买决策路径：从看到产品到下单的完整心理过程，100字以内",
    "tags": ["标签1", "标签2", "标签3", "标签4", "标签5"]
  },
  "strategy": {
    "week1_2": "第1-2周：找什么博主、发什么内容、重点建立什么，100字以内",
    "week3_4": "第3-4周：如何在前期基础上放量和优化，100字以内",
    "month2": "第二个月：如何规模化收割转化，100字以内"
  },
  "action": "今天就能执行的第一件事，具体到可操作的动作，50字以内",
  "score_reason": "评分说明：从市场机会、竞争难度、品牌差异化、预算匹配度四维度说明，150字以内"
}

评分标准：90-100强烈推荐；75-89推荐入场；60-74谨慎推荐；45-59风险较高；0-44不建议现阶段入场
"""


# 按用户要求保持“豆包 Ark 接口”。模型 ID 以控制台为准。
MODEL_NAME = "doubao-seed-2-0-pro-260215"
ARK_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/responses"


def set_styles() -> None:
    st.set_page_config(
        page_title="小红书冷启动潜力分析器",
        page_icon="🧾",
        layout="centered",
        initial_sidebar_state="collapsed",
    )

    css = """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600&family=Noto+Serif+SC:wght@600;700&display=swap');

      /* Hide Streamlit default chrome */
      header {visibility: hidden;}
      footer {visibility: hidden;}
      #MainMenu {visibility: hidden;}

      :root{
        --bg: #FAF9F7;
        --card: #FFFFFF;
        --text: #111111;
        --muted: rgba(17,17,17,.65);
        --border: #F0EEEB;
        --shadow: 0 2px 20px rgba(0,0,0,0.05);
        --accent: #FF6B6B;
        --focus: #111111;
        --green: #4CAF50;
        --orange: #FF9800;
        --gray: #999999;
      }

      html, body, [class*="css"]  {
        font-family: 'Noto Sans SC', system-ui, -apple-system, Segoe UI, Roboto, Arial, 'PingFang SC','Hiragino Sans GB','Microsoft YaHei', sans-serif;
        color: var(--text);
      }

      .stApp{
        background: var(--bg);
      }

      /* Center max width */
      section.main > div { max-width: 800px; padding-top: 28px; padding-bottom: 24px; }

      .title-wrap{
        margin: 4px 0 16px 0;
      }
      .title{
        font-family: 'Noto Serif SC', serif;
        font-weight: 700;
        letter-spacing: .5px;
        font-size: 34px;
        line-height: 1.2;
        margin: 0;
      }
      .subtitle{
        margin-top: 10px;
        font-size: 14px;
        color: var(--muted);
        line-height: 1.6;
      }

      .card{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 20px;
        box-shadow: var(--shadow);
        padding: 2rem;
        margin: 14px 0;
      }

      .section-title{
        font-family: 'Noto Serif SC', serif;
        font-weight: 700;
        font-size: 16px;
        margin: 0 0 10px 0;
        letter-spacing: .4px;
      }
      .section-body{
        font-size: 14px;
        line-height: 1.75;
        color: rgba(17,17,17,.88);
        white-space: pre-wrap;
      }

      /* Inputs */
      .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] > div {
        border-radius: 12px !important;
      }
      .stTextInput input:focus, .stTextArea textarea:focus {
        border-color: var(--focus) !important;
        box-shadow: 0 0 0 2px rgba(17,17,17,0.08) !important;
      }
      div[data-baseweb="select"] > div:focus-within{
        border-color: var(--focus) !important;
        box-shadow: 0 0 0 2px rgba(17,17,17,0.08) !important;
      }

      /* Primary button */
      div.stButton > button, button[kind="primary"]{
        background: var(--accent) !important;
        color: #fff !important;
        border: 1px solid rgba(0,0,0,.0) !important;
        border-radius: 14px !important;
        width: 100% !important;
        padding: 0.7rem 1rem !important;
        font-weight: 600 !important;
      }
      div.stButton > button:hover, button[kind="primary"]:hover{
        opacity: 0.85 !important;
      }

      /* Text link button (back) */
      .back-btn button{
        background: transparent !important;
        color: rgba(17,17,17,.80) !important;
        border: none !important;
        padding: 0 !important;
        width: auto !important;
        border-radius: 0 !important;
        font-weight: 500 !important;
      }
      .back-btn button:hover{
        text-decoration: underline !important;
        opacity: 0.9 !important;
      }

      /* Header row on result page */
      .result-header{
        display:flex;
        align-items: baseline;
        gap: 12px;
        margin: 6px 0 10px 0;
      }
      .brand-title{
        font-family: 'Noto Serif SC', serif;
        font-weight: 700;
        font-size: 30px;
        line-height: 1.15;
        margin: 0;
      }
      .score-badge{
        display:inline-flex;
        align-items:center;
        gap: 6px;
        padding: 6px 10px;
        border-radius: 999px;
        border: 1px solid var(--border);
        background: #fff;
        color: rgba(17,17,17,.78);
        font-size: 13px;
        font-weight: 600;
      }

      /* Score card */
      .score-card{
        background: #fff;
        border: 1px solid var(--border);
        border-radius: 20px;
        box-shadow: var(--shadow);
        padding: 2rem;
        margin: 0 0 14px 0;
      }
      .score-number{
        font-family: 'Noto Serif SC', serif;
        font-weight: 700;
        font-size: 5rem;
        line-height: 1;
        margin: 0;
      }
      .score-verdict{
        margin-top: 10px;
        font-size: 16px;
        font-weight: 600;
        color: rgba(17,17,17,.90);
      }
      .score-reason{
        margin-top: 12px;
        font-size: 13.5px;
        line-height: 1.75;
        color: rgba(17,17,17,.72);
      }

      /* Pills */
      .pills{
        margin-top: 14px;
        display:flex;
        flex-wrap: wrap;
        gap: 10px;
      }
      .pill{
        display:inline-block;
        background: var(--accent);
        color: #fff;
        padding: 6px 10px;
        border-radius: 999px;
        font-size: 12.5px;
        font-weight: 600;
        letter-spacing: .2px;
      }

      /* Market items with left accent bar */
      .market-item{
        display:flex;
        gap: 12px;
        margin: 10px 0;
      }
      .market-bar{
        width: 4px;
        border-radius: 999px;
        background: var(--accent);
        flex: 0 0 auto;
        margin-top: 4px;
      }
      .market-title{
        font-weight: 700;
        font-size: 13.5px;
        margin: 0 0 4px 0;
      }
      .market-text{
        margin: 0;
        font-size: 13.5px;
        line-height: 1.75;
        color: rgba(17,17,17,.82);
        white-space: pre-wrap;
      }

      /* Timeline */
      .timeline{
        display:flex;
        flex-direction: column;
        gap: 14px;
        margin-top: 8px;
      }
      .timeline-item{
        display:flex;
        gap: 12px;
        align-items:flex-start;
      }
      .timeline-dot{
        width: 26px;
        height: 26px;
        border-radius: 999px;
        background: var(--accent);
        color:#fff;
        display:flex;
        align-items:center;
        justify-content:center;
        font-weight: 800;
        font-size: 13px;
        flex: 0 0 auto;
        margin-top: 2px;
      }
      .timeline-content{
        flex: 1 1 auto;
      }
      .timeline-title{
        margin: 0 0 4px 0;
        font-weight: 700;
        font-size: 13.5px;
      }
      .timeline-text{
        margin: 0;
        font-size: 13.5px;
        line-height: 1.75;
        color: rgba(17,17,17,.82);
        white-space: pre-wrap;
      }

      /* Action card */
      .action-card{
        background: var(--accent);
        color: #fff;
        border-radius: 20px;
        box-shadow: var(--shadow);
        padding: 2rem;
        margin: 14px 0 6px 0;
        border: 1px solid rgba(255,255,255,.20);
      }
      .action-title{
        font-family: 'Noto Serif SC', serif;
        font-weight: 700;
        font-size: 16px;
        margin: 0 0 10px 0;
        letter-spacing: .3px;
      }
      .action-text{
        margin: 0;
        font-size: 16px;
        line-height: 1.7;
        font-weight: 600;
        white-space: pre-wrap;
      }

      .copyright{
        margin-top: 22px;
        padding: 10px 0 4px 0;
        color: rgba(17,17,17,.50);
        font-size: 12px;
        text-align: center;
      }

      /* Mobile adapt */
      @media (max-width: 768px) {
        section.main > div { padding-top: 18px; }
        .title{ font-size: 26px; }
        .brand-title{ font-size: 24px; }
        .card{ padding: 1.25rem; border-radius: 18px; }
        .score-card{ padding: 1.25rem; border-radius: 18px; }
        .score-number{ font-size: 4rem; }
        .action-card{ padding: 1.25rem; border-radius: 18px; }
      }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


def clamp_score(value: Any) -> int:
    try:
        n = int(round(float(value)))
    except Exception:
        return 0
    return max(0, min(100, n))


def _extract_json_candidate(text: str) -> Optional[str]:
    """
    模型被要求只返回 JSON，但在极少数情况下仍可能夹带前后文本。
    这里尽量鲁棒地提取第一个看起来像 JSON 对象的片段。
    """
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    return m.group(0).strip()


def _extract_json_objects(text: str) -> List[str]:
    """
    从任意文本中按括号栈提取所有顶层 JSON 对象（{...}）。
    兼容模型输出“解释文字 + 多段 JSON”的情况。
    """
    objs: List[str] = []
    start: Optional[int] = None
    depth = 0
    in_str = False
    esc = False

    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue

        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    objs.append(text[start : i + 1].strip())
                    start = None

    return objs


def _get_dict(d: Any, key: str) -> Dict[str, Any]:
    v = d.get(key) if isinstance(d, dict) else None
    return v if isinstance(v, dict) else {}


def parse_report(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    required_top = ["score", "verdict", "market", "persona", "strategy", "action", "score_reason"]

    # 先用更可靠的“括号栈”提取所有 JSON 对象，优先选择最后一个完整且字段齐全的
    candidates = _extract_json_objects(text)
    if not candidates:
        # 回退到原先的简单提取（极端情况下仍可能有用）
        c = _extract_json_candidate(text)
        candidates = [c] if c else []

    last_error: Optional[str] = None
    for candidate in reversed([c for c in candidates if c]):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as e:
            last_error = f"JSON 解析失败：{e}"
            continue

        if not isinstance(data, dict):
            last_error = "JSON 顶层不是对象。"
            continue

        missing = [k for k in required_top if k not in data]
        if missing:
            last_error = f"JSON 缺少字段：{', '.join(missing)}"
            continue

        data["score"] = clamp_score(data.get("score"))
        if not isinstance(data.get("verdict"), str):
            data["verdict"] = str(data.get("verdict", "")).strip()
        if not isinstance(data.get("action"), str):
            data["action"] = str(data.get("action", "")).strip()
        if not isinstance(data.get("score_reason"), str):
            data["score_reason"] = str(data.get("score_reason", "")).strip()

        market = _get_dict(data, "market")
        persona = _get_dict(data, "persona")
        strategy = _get_dict(data, "strategy")

        for k in ("heat", "competition", "opportunity"):
            if not isinstance(market.get(k), str):
                market[k] = str(market.get(k, "")).strip()
        for k in ("profile", "content", "purchase"):
            if not isinstance(persona.get(k), str):
                persona[k] = str(persona.get(k, "")).strip()
        tags = persona.get("tags")
        if not isinstance(tags, list):
            persona["tags"] = []
        else:
            persona["tags"] = [str(x).strip() for x in tags if str(x).strip()][:10]
        for k in ("week1_2", "week3_4", "month2"):
            if not isinstance(strategy.get(k), str):
                strategy[k] = str(strategy.get(k, "")).strip()

        data["market"] = market
        data["persona"] = persona
        data["strategy"] = strategy
        return data, None

    if candidates:
        return None, last_error or "模型返回的 JSON 无法通过校验。"
    return None, "模型返回内容中未找到可解析的 JSON。"


def build_user_prompt(form: Dict[str, str]) -> str:
    return (
        "品牌信息如下：\n"
        f"1. 品牌名称：{form['brand_name']}\n"
        f"2. 品类：{form['category']}\n"
        f"3. 价格带：{form['price_band']}\n"
        f"4. 核心卖点：{form['selling_points']}\n"
        f"5. 目标人群：{form['target_audience']}\n"
        f"6. 当前小红书基础：{form['xhs_base']}\n"
        f"7. 月预算范围：{form['monthly_budget']}\n"
        "\n"
        "请严格按 system 指令输出 JSON。\n"
        "硬性要求：只输出一段纯 JSON（必须以 { 开头，以 } 结尾），不要输出任何解释、分析过程、标题、换行前后缀或多段 JSON。"
    )


def _extract_texts(obj: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "text" and isinstance(v, str):
                texts.append(v)
            else:
                texts.extend(_extract_texts(v))
    elif isinstance(obj, list):
        for item in obj:
            texts.extend(_extract_texts(item))
    return texts


def _get_ark_api_key() -> str:
    # 优先从 Streamlit secrets 读取，其次环境变量
    try:
        v = st.secrets.get("ARK_API_KEY")
        if v:
            return str(v).strip()
    except Exception:
        pass

    for k in ("ARK_API_KEY", "VOLC_ARK_API_KEY", "ARK_BEARER_TOKEN"):
        v = os.getenv(k)
        if v and v.strip():
            return v.strip()
    return ""


def call_doubao(user_prompt: str, api_key: str) -> str:
    # Ark Responses API：参考用户提供的 curl 规范
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL_NAME,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"{SYSTEM_PROMPT}\n\n---\n\n{user_prompt}",
                    }
                ],
            }
        ],
    }

    try:
        r = requests.post(ARK_ENDPOINT, headers=headers, json=payload, timeout=60)
    except requests.Timeout as e:
        raise RuntimeError("网络超时，请稍后重试。") from e
    except requests.RequestException as e:
        raise RuntimeError("网络异常，请检查网络/代理设置后重试。") from e

    if r.status_code >= 400:
        # 尽量把错误 JSON 展示出来，方便用户在页面看到原因
        try:
            err = r.json()
        except Exception:
            err = r.text
        if r.status_code in (401, 403):
            raise RuntimeError("API Key 无效或无权限，请检查 Key 是否正确、是否已开通权限。")
        raise RuntimeError(f"{r.status_code} - {err}")

    data = r.json()
    # 尽量从返回结构中提取模型输出文本（兼容不同字段形态）
    texts = _extract_texts(data)
    if not texts:
        return json.dumps(data, ensure_ascii=False)
    return "\n".join(t.strip() for t in texts if t.strip()).strip()

def _score_color(score: int) -> str:
    if score >= 90:
        return "#4CAF50"
    if score >= 75:
        return "#FF6B6B"
    if score >= 60:
        return "#FF9800"
    return "#999999"


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _card(title: str, inner_html: str) -> None:
    st.markdown(
        f"""
        <div class="card">
          <div class="section-title">{_escape_html(title)}</div>
          {inner_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _bullet(label: str, text: str) -> str:
    return f"<div class='section-body'><b>{_escape_html(label)}</b><br/>{_escape_html(text)}</div>"


def render_form_page() -> None:
    st.markdown(
        """
        <div class="title-wrap">
          <div class="title">小红书冷启动潜力分析器</div>
          <div class="subtitle">填写品牌信息，一键生成专业冷启动分析报告（评分 + 市场机会 + 人群画像 + 策略 + 行动建议）。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("xhs_form", clear_on_submit=False):
        st.markdown('<div class="card">', unsafe_allow_html=True)
        brand_name = st.text_input("品牌名称", placeholder="例如：XXX")
        category = st.text_input("品类", placeholder="例如：彩妆-遮瑕膏")
        price_band = st.selectbox(
            "价格带",
            ["50元以内", "50-100元", "100-300元", "300-500元", "500元以上"],
            index=2,
        )
        selling_points = st.text_area("核心卖点", placeholder="要点描述：成分/功效/质地/场景/差异化等", height=120)
        target_audience = st.text_input("目标人群", placeholder="例如：18-28岁油皮、学生/白领、成分党等")
        xhs_base = st.selectbox(
            "当前小红书基础",
            ["无账号从零开始", "有账号但无内容", "已有少量笔记", "已有一定内容积累"],
            index=0,
        )
        monthly_budget = st.selectbox(
            "月预算范围",
            ["1万以内", "1-3万", "3-5万", "5-10万", "10万以上"],
            index=1,
        )
        st.markdown("</div>", unsafe_allow_html=True)
        submitted = st.form_submit_button("生成冷启动分析报告", type="primary")

    if not submitted:
        st.markdown('<div class="copyright">© 2026 小红书冷启动潜力分析器 · 仅供策略参考</div>', unsafe_allow_html=True)
        return

    required_fields = {
        "品牌名称": brand_name.strip(),
        "品类": category.strip(),
        "核心卖点": selling_points.strip(),
        "目标人群": target_audience.strip(),
    }
    missing = [k for k, v in required_fields.items() if not v]
    if missing:
        st.error("请先补全必填项：" + "、".join(missing))
        st.markdown('<div class="copyright">© 2026 小红书冷启动潜力分析器 · 仅供策略参考</div>', unsafe_allow_html=True)
        return

    api_key = _get_ark_api_key()
    if not api_key:
        st.error("未配置豆包 API Key。请在运行环境中设置环境变量 ARK_API_KEY（或 VOLC_ARK_API_KEY），或在 Streamlit Secrets 中配置 ARK_API_KEY。")
        st.markdown('<div class="copyright">© 2026 小红书冷启动潜力分析器 · 仅供策略参考</div>', unsafe_allow_html=True)
        return

    payload = {
        "brand_name": brand_name.strip(),
        "category": category.strip(),
        "price_band": price_band,
        "selling_points": selling_points.strip(),
        "target_audience": target_audience.strip(),
        "xhs_base": xhs_base,
        "monthly_budget": monthly_budget,
    }

    prompt = build_user_prompt(payload)
    with st.spinner("正在生成报告…"):
        try:
            raw = call_doubao(prompt, api_key)
        except Exception as e:
            st.error(f"调用 API 失败：{e}")
            st.markdown('<div class="copyright">© 2026 小红书冷启动潜力分析器 · 仅供策略参考</div>', unsafe_allow_html=True)
            return

        report, err = parse_report(raw)
        if err:
            st.error("JSON 解析失败，请重试。")
            with st.expander("查看模型原始返回（用于排查）"):
                st.code(raw)
            st.markdown('<div class="copyright">© 2026 小红书冷启动潜力分析器 · 仅供策略参考</div>', unsafe_allow_html=True)
            return

    st.session_state.report_data = report
    st.session_state.brand_name = payload["brand_name"]
    st.session_state.page = "result"
    st.rerun()


def render_result_page() -> None:
    report: Dict[str, Any] = st.session_state.get("report_data") or {}
    brand_name = st.session_state.get("brand_name") or "分析报告"
    score = int(report.get("score", 0))
    verdict = str(report.get("verdict", "")).strip()
    score_reason = str(report.get("score_reason", "")).strip()

    back_col, _ = st.columns([1, 6])
    with back_col:
        st.markdown('<div class="back-btn">', unsafe_allow_html=True)
        if st.button("← 重新分析", key="back_to_form"):
            st.session_state.page = "form"
            st.session_state.report_data = None
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(
        f"""
        <div class="result-header">
          <div class="brand-title">{_escape_html(brand_name)}</div>
          <div class="score-badge">评分 <span style="color:{_score_color(score)};font-weight:800;">{score}</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([1, 1], gap="large")

    with left:
        st.markdown(
            f"""
            <div class="score-card">
              <div class="score-number" style="color:{_score_color(score)};">{score}</div>
              <div class="score-verdict">{_escape_html(verdict)}</div>
              <div class="score-reason">{_escape_html(score_reason)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        persona = report.get("persona") if isinstance(report.get("persona"), dict) else {}
        tags = persona.get("tags") if isinstance(persona.get("tags"), list) else []
        pills = "".join([f"<span class='pill'>{_escape_html(str(t))}</span>" for t in tags if str(t).strip()])
        persona_html = (
            _bullet("基础画像", str(persona.get("profile", "")))
            + "<hr style='border:none;border-top:1px solid #F0EEEB;margin:14px 0;'/>"
            + _bullet("内容偏好", str(persona.get("content", "")))
            + "<hr style='border:none;border-top:1px solid #F0EEEB;margin:14px 0;'/>"
            + _bullet("购买决策路径", str(persona.get("purchase", "")))
            + (f"<div class='pills'>{pills}</div>" if pills else "")
        )
        _card("核心人群画像", persona_html)

    with right:
        market = report.get("market") if isinstance(report.get("market"), dict) else {}
        market_html = f"""
          <div class="market-item">
            <div class="market-bar"></div>
            <div>
              <div class="market-title">品类热度</div>
              <p class="market-text">{_escape_html(str(market.get("heat","")))}</p>
            </div>
          </div>
          <div class="market-item">
            <div class="market-bar"></div>
            <div>
              <div class="market-title">竞争格局</div>
              <p class="market-text">{_escape_html(str(market.get("competition","")))}</p>
            </div>
          </div>
          <div class="market-item">
            <div class="market-bar"></div>
            <div>
              <div class="market-title">你的机会点</div>
              <p class="market-text">{_escape_html(str(market.get("opportunity","")))}</p>
            </div>
          </div>
        """
        _card("市场机会", market_html)

        strategy = report.get("strategy") if isinstance(report.get("strategy"), dict) else {}
        timeline_html = f"""
          <div class="timeline">
            <div class="timeline-item">
              <div class="timeline-dot">1</div>
              <div class="timeline-content">
                <div class="timeline-title">第1-2周</div>
                <p class="timeline-text">{_escape_html(str(strategy.get("week1_2","")))}</p>
              </div>
            </div>
            <div class="timeline-item">
              <div class="timeline-dot">2</div>
              <div class="timeline-content">
                <div class="timeline-title">第3-4周</div>
                <p class="timeline-text">{_escape_html(str(strategy.get("week3_4","")))}</p>
              </div>
            </div>
            <div class="timeline-item">
              <div class="timeline-dot">3</div>
              <div class="timeline-content">
                <div class="timeline-title">第二个月</div>
                <p class="timeline-text">{_escape_html(str(strategy.get("month2","")))}</p>
              </div>
            </div>
          </div>
        """
        _card("冷启动策略", timeline_html)

    action = str(report.get("action", "")).strip()
    st.markdown(
        f"""
        <div class="action-card">
          <div class="action-title">行动建议</div>
          <p class="action-text">{_escape_html(action)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="copyright">© 2026 小红书冷启动潜力分析器 · 仅供策略参考</div>', unsafe_allow_html=True)


def main() -> None:
    set_styles()
    if "page" not in st.session_state:
        st.session_state.page = "form"
    if "report_data" not in st.session_state:
        st.session_state.report_data = None
    if "brand_name" not in st.session_state:
        st.session_state.brand_name = ""

    if st.session_state.page == "result" and st.session_state.report_data:
        render_result_page()
        return

    st.session_state.page = "form"
    render_form_page()


if __name__ == "__main__":
    main()

