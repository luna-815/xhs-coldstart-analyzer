# 小红书冷启动潜力分析器

使用 Python + Streamlit + 火山方舟（豆包大模型）生成「小红书冷启动分析报告」的完整 Web 应用。

## 1. 安装依赖

建议使用虚拟环境（可选），然后安装依赖：

```bash
pip install -r requirements.txt
```

## 2. 运行应用

在项目根目录执行：

```bash
streamlit run app.py
```

启动后在浏览器打开 Streamlit 提示的本地地址即可。

## 3. 如何获取豆包（火山方舟）API Key

1. 打开火山方舟控制台（Ark）。
2. 在 API Key（或密钥管理）创建一个新的 API Key。
3. 将 Key 配置到运行环境中（推荐环境变量），应用即可无需用户输入直接使用。

### 配置方式 A：环境变量（推荐）

在启动前设置环境变量 `ARK_API_KEY`：

```bash
set ARK_API_KEY=你的Key
streamlit run app.py
```

或在 PowerShell 中：

```powershell
$env:ARK_API_KEY="你的Key"
streamlit run app.py
```

### 配置方式 B：Streamlit secrets

在项目根目录创建 `.streamlit/secrets.toml`，写入：

```toml
ARK_API_KEY = "你的Key"
```

> 提示：API Key 属于敏感信息，请勿提交到代码仓库或分享给他人。

