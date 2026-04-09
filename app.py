import json
import os
import re
from typing import Any, Dict, Optional, Tuple, List

import streamlit as st
import requests


SYSTEM_PROMPT = """你是一位拥有8年小红书品牌运营经验的顶级策略顾问，曾服务过完美日记、花西子、珀莱雅等头部国货品牌，深度参与过超过200个品牌的小红书冷启动项目。

你的分析风格是：数据驱动、逻辑严谨、建议落地可执行，不说废话，每一条建议都有明确的行动指引。

请基于用户提供的品牌信息，生成一份专业的小红书冷启动分析报告。

请严格按照以下JSON格式返回，只返回纯JSON，不要包含任何markdown符号或额外文字：

{
  "score": 数字(0-100),
  "verdict": "一句话总结这个品牌的冷启动潜力（15字以内）",
  "market": "市场机会判断：分析该品类在小红书的整体机会、竞争激烈程度、品牌差异化优势，200字以内",
  "persona": "核心人群画像：她是谁、她在小红书看什么、她怎么做购买决策，200字以内",
  "strategy": "冷启动策略建议：第一个月重点做什么、推荐博主类型、内容方向、预算分配，250字以内",
  "risk": "风险提示：这个品牌冷启动最可能踩的2-3个坑，150字以内",
  "score_reason": "评分说明：从市场机会、竞争难度、品牌差异化、预算匹配度四个维度说明评分依据，150字以内"
}

评分参考标准：
- 90-100分：强烈推荐，天然适合小红书，机会窗口明显
- 75-89分：推荐入场，有明确路径，需要精细化执行
- 60-74分：谨慎推荐，有机会但挑战不小，需差异化破局
- 45-59分：风险较高，需先验证核心假设再大规模投入
- 0-44分：不建议现阶段入场，建议先完善产品或选择其他平台
"""


# 重要：火山方舟模型 ID 以控制台为准（下方为你截图中常见的一个）
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
        --border: rgba(17,17,17,.10);
        --shadow: 0 8px 24px rgba(0,0,0,.06);
        --black: #1A1A1A;
      }

      html, body, [class*="css"]  {
        font-family: 'Noto Sans SC', system-ui, -apple-system, Segoe UI, Roboto, Arial, 'PingFang SC','Hiragino Sans GB','Microsoft YaHei', sans-serif;
        color: var(--text);
      }

      .stApp{
        background: var(--bg);
      }

      /* Center max width */
      section.main > div { max-width: 720px; padding-top: 28px; padding-bottom: 20px; }

      .title-wrap{
        margin-bottom: 16px;
      }
      .title{
        font-family: 'Noto Serif SC', serif;
        font-weight: 700;
        letter-spacing: .5px;
        font-size: 30px;
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
        padding: 16px 16px;
        margin: 12px 0;
      }

      .score-card{
        background: var(--black);
        color: #fff;
        border-radius: 20px;
        padding: 18px 16px;
        margin: 14px 0 12px 0;
        border: 1px solid rgba(255,255,255,.10);
        box-shadow: 0 10px 28px rgba(0,0,0,.12);
      }
      .score-label{
        font-size: 12px;
        opacity: .80;
        letter-spacing: .6px;
      }
      .score-value{
        font-family: 'Noto Serif SC', serif;
        font-weight: 700;
        font-size: 56px;
        line-height: 1.05;
        margin-top: 8px;
      }
      .score-hint{
        margin-top: 10px;
        font-size: 13px;
        opacity: .85;
        line-height: 1.6;
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

      /* Inputs: round corners + hover border */
      .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] > div {
        border-radius: 12px !important;
      }
      .stTextInput input:hover, .stTextArea textarea:hover {
        border-color: var(--black) !important;
      }
      div[data-baseweb="select"]:hover > div{
        border-color: var(--black) !important;
      }

      /* Button: full width, rounded */
      div.stButton > button, button[kind="primary"]{
        background: var(--black) !important;
        color: #fff !important;
        border: 1px solid rgba(255,255,255,.15) !important;
        border-radius: 14px !important;
        width: 100% !important;
        padding: 0.7rem 1rem !important;
        font-weight: 600 !important;
      }
      div.stButton > button:hover, button[kind="primary"]:hover{
        filter: brightness(0.95);
        border-color: rgba(255,255,255,.25) !important;
      }

      .copyright{
        margin-top: 22px;
        padding: 10px 0 4px 0;
        color: rgba(17,17,17,.50);
        font-size: 12px;
        text-align: center;
      }

      /* Mobile adapt */
      @media (max-width: 560px) {
        section.main > div { padding-top: 18px; }
        .title{ font-size: 24px; }
        .subtitle{ font-size: 13px; }
        .card{ padding: 14px 14px; border-radius: 18px; }
        .score-card{ padding: 16px 14px; border-radius: 18px; }
        .score-value{ font-size: 48px; }
        .section-body{ font-size: 13.5px; }
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


def parse_report(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    required = ["score", "verdict", "market", "persona", "strategy", "risk", "score_reason"]

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

        missing = [k for k in required if k not in data]
        if missing:
            last_error = f"JSON 缺少字段：{', '.join(missing)}"
            continue

        data["score"] = clamp_score(data.get("score"))
        for k in required[1:]:
            if not isinstance(data.get(k), str):
                data[k] = str(data.get(k, "")).strip()
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


def call_doubao(user_prompt: str) -> str:
    api_key = _get_ark_api_key()
    if not api_key:
        raise RuntimeError(
            "未配置豆包 API Key。请在运行环境中设置环境变量 ARK_API_KEY（或 VOLC_ARK_API_KEY），"
            "或在 .streamlit/secrets.toml 中配置 ARK_API_KEY。"
        )
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

    r = requests.post(ARK_ENDPOINT, headers=headers, json=payload, timeout=60)
    if r.status_code >= 400:
        # 尽量把错误 JSON 展示出来，方便用户在页面看到原因
        try:
            err = r.json()
        except Exception:
            err = r.text
        raise RuntimeError(f"{r.status_code} - {err}")

    data = r.json()
    # 尽量从返回结构中提取模型输出文本（兼容不同字段形态）
    texts = _extract_texts(data)
    if not texts:
        return json.dumps(data, ensure_ascii=False)
    return "\n".join(t.strip() for t in texts if t.strip()).strip()


def card(title: str, body: str) -> None:
    st.markdown(
        f"""
        <div class="card">
          <div class="section-title">{title}</div>
          <div class="section-body">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def score_card(score: int, verdict: str) -> None:
    st.markdown(
        f"""
        <div class="score-card">
          <div class="score-label">冷启动潜力评分（0-100）</div>
          <div class="score-value">{score}</div>
          <div class="score-hint">{verdict}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    set_styles()

    st.markdown(
        """
        <div class="title-wrap">
          <div class="title">小红书冷启动潜力分析器</div>
          <div class="subtitle">填写品牌信息，一键生成专业冷启动分析报告（评分 + 机会判断 + 策略建议 + 风险提示）。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "report" not in st.session_state:
        st.session_state.report = None
    if "raw" not in st.session_state:
        st.session_state.raw = None

    with st.form("brand_form", clear_on_submit=False):
        st.markdown('<div class="card">', unsafe_allow_html=True)
        brand_name = st.text_input("品牌名称", placeholder="例如：XXX")
        category = st.text_input("品类", placeholder="例如：彩妆-遮瑕膏")
        price_band = st.selectbox(
            "价格带",
            ["50元以内", "50-100元", "100-300元", "300-500元", "500元以上"],
            index=2,
        )
        selling_points = st.text_area("核心卖点", placeholder="用要点描述：成分/功效/质地/场景/差异化等", height=110)
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

    if submitted:
        required_fields = {
            "品牌名称": brand_name.strip(),
            "品类": category.strip(),
            "核心卖点": selling_points.strip(),
            "目标人群": target_audience.strip(),
        }
        missing = [k for k, v in required_fields.items() if not v]
        if missing:
            st.error("请先补全必填项：" + "、".join(missing))
        else:
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
            with st.spinner("正在调用 豆包 生成报告…"):
                try:
                    raw = call_doubao(prompt)
                except Exception as e:
                    st.session_state.report = None
                    st.session_state.raw = None
                    st.error(f"调用 豆包 API 失败：{e}")
                else:
                    report, err = parse_report(raw)
                    st.session_state.raw = raw
                    if err:
                        st.session_state.report = None
                        st.error("报告解析失败：" + err)
                        with st.expander("查看模型原始返回（用于排查）"):
                            st.code(raw)
                    else:
                        st.session_state.report = report

    report = st.session_state.report
    if report:
        score_card(report["score"], report["verdict"])
        card("市场机会判断", report["market"])
        card("核心人群画像", report["persona"])
        card("冷启动策略建议", report["strategy"])
        card("风险提示", report["risk"])
        card("评分说明", report["score_reason"])

    st.markdown('<div class="copyright">© 2026 小红书冷启动潜力分析器 · 仅供策略参考</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()

