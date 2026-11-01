# TROPOS 模型 API 能力说明

## 已经接通的能力

| 能力 | DeepSeek | OpenAI | Kimi | 千问（Qwen） | TROPOS 中的使用方式 |
|---|---|---|---|---|---|
| 文本理解与回答 | Chat Completions | Responses API | Chat Completions | 百炼 Chat Completions；新款 Qwen3 可用 Responses | 普通问答、意图理解、任务拆解 |
| 结构化输出 | JSON 输出后服务端白名单校验 | Responses JSON 输出后服务端白名单校验 | JSON 输出后服务端白名单校验 | `json_object` 后服务端白名单校验 | 只接受已声明动作与字段 |
| 多轮对话 | 应用管理最近对话 | 应用管理最近对话 | 应用管理最近对话 | 应用管理最近对话，原生长文本回答 | 仅用于只读问答中的指代与延续话题 |
| 实时网页检索 | 官方 Search API | Responses 原生 `web_search` | 官方 Search Pro | 兼容 `enable_search`；新款 Qwen3 使用 Responses `web_search` | 明确要求联网、最新信息、来源或链接时调用 |
| 网页提取与计算 | — | 供应商工具 | — | 新款 Qwen3 可用 `web_extractor` 与 `code_interpreter` | 只在百炼供应商沙箱中处理只读研究与计算 |
| 来源展示 | 搜索结果链接 | Web Search 引用与来源 | Search Pro 结果链接 | Responses 实际返回来源时展示；否则明确说明未返回 | 页面只显示实际返回的标题、域名和链接 |
| 财务/生活方案 | 已接通 | 已接通 | 已接通 | 已接通 | 模型使用脱敏汇总，预算上限由服务端校验 |
| TROPOS 服务入口 | 已接通 | 已接通 | 已接通 | 已接通 | 模型只能建议或草拟；执行前仍逐项确认 |

## “和官方终端一样”做不到完全等同的原因

DeepSeek、Kimi 与 ChatGPT 的网页或 App 不是一个裸模型接口。官方终端通常还组合了自有系统提示、搜索编排、文件解析、长期记忆、代码执行、账号数据与产品专属工具。API Key 只授予开放平台明确提供的接口能力，因此会员订阅也不能代替 API 额度。

TROPOS 现在尽量使用每家的正式能力，而不是把所有服务硬套成同一个兼容接口：OpenAI 走 Responses；DeepSeek 走模型接口与 Search；Kimi 走模型接口与 Search Pro；千问根据模型自动选择路径。`qwen-plus` 等旧兼容模型继续使用 Chat Completions，并按场景开启推理、结构化 JSON 或 `enable_search`；受百炼支持的新款 Qwen3 模型优先使用 Responses，同时开放 `web_search`、`web_extractor` 与 `code_interpreter`。若当前地域或业务空间没有 Responses 端点，会自动回退到兼容联网路径。这样能够获得实时信息、多轮连续问答、网页提取和复杂计算，但仍不会绕过银行侧的预算、收款人、金额和确认校验。

千问兼容 Chat Completions 的联网结果不保证返回可展示的来源 URL。TROPOS 会展示接口实际返回的链接；若只有联网回答而没有 URL，会明确提示“未返回来源”，不会生成看似真实的引用。Responses 路径会从 `web_search_call.action.sources` 提取真实 URL。供应商原生函数调用能力已识别，但不会直接绑定转账、付款、下单或账户修改工具；任何意外返回的自定义函数或 MCP 调用都会被拒绝。

### 千问推荐配置

- 兼容优先：接口地址可使用 `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型使用账号已开通的 `qwen-plus` 等模型。
- 工具增强：在百炼控制台取得对应地域和业务空间的 `https://{WorkspaceId}.<region>.maas.aliyuncs.com/compatible-mode/v1` 地址，并选择百炼当前声明支持 Responses 工具的 Qwen3 模型。
- “我的 API”模式下，API Key 与模型费用只属于当前登录用户；平台模式由管理员配置和承担费用。
- 联网、网页提取与代码解释器可能产生额外工具费用。TROPOS 不会把完整账本、会话令牌、API Key 或银行执行工具发送给这些工具。

## 供应商支持但当前页面尚未开放

- 流式逐字输出：供应商 API 可用；当前 TROPOS 为了先完成结构校验，再一次性展示通过校验的结果。
- 图片理解：部分所选模型支持；当前聊天框尚未提供安全上传、内容扫描与图片生命周期管理。
- 文件输入与文件检索：OpenAI 等服务提供相关能力；当前尚未实现按用户隔离的文件库、删除与保留期限。
- 深度研究、PPT 等：这些往往是官方产品能力或独立工具，不一定由普通聊天 API 提供；当前不会声称已经接通。千问新款 Responses 模型的供应商代码解释器已用于只读研究与计算，但它不是本机代码执行，也不能访问 TROPOS 服务器、数据库或银行工具。
- 供应商原生函数调用：TROPOS 已有自己的受控业务动作层，但不会把转账、付款、下单或取消直接交给模型工具自动执行。

## 隐私与执行边界

- API Key 只保存在服务器内存，不进入浏览器、SQLite、审计日志或模型提示。
- 普通问答发送当前消息；连续问答可发送近期普通对话文字。
- 财务方案只在用户同意读取合成账本后发送服务端生成的脱敏汇总。
- 原始流水备注、商户明细、收款账号、用户标识和会话令牌不发送给模型。
- 搜索网页是不可信证据，不能触发操作。模型的建议也不能代替用户确认。
