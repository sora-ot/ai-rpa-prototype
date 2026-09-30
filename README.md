# EasySpider AI 流程生成原型

本目录独立于 `EasySpider` 源码。模型先生成可供确认的工作步骤，再根据真实网页的 DOM 候选请求 `input`、`click`、`extract` 等受限动作。确定性构建器把动作和字段定位编译成 EasySpider 原生任务 JSON。只有点击“导入运行中的 EasySpider”才会写入原系统任务列表。

## 安装与启动

无需专门创建 Conda 环境；建议使用 Python 3.10+ 的虚拟环境，避免依赖影响其他项目。PowerShell 示例：

```powershell
cd ai-rpa-prototype
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

也可以在已有且安装了 `selenium`、`lxml` 的 Python 环境中运行 `python server.py`。访问 `http://127.0.0.1:8765`。

在本目录的 `.env` 填写模型配置；文件已被 `.gitignore` 忽略，不会写入生成任务：

```dotenv
AI_RPA_PROVIDER=deepseek
AI_RPA_MODEL=deepseek-chat
DEEPSEEK_API_KEY=填入你的Key
OPENAI_API_KEY=
GEMINI_API_KEY=
AI_RPA_MAX_ACTIONS=30
AI_RPA_RISK_REVIEW=0
```

`AI_RPA_PROVIDER` 可选 `deepseek`、`gpt`、`gemini`。对应默认模型分别是 `deepseek-chat`、`gpt-4o-mini`、`gemini-2.5-flash`；可以修改 `AI_RPA_MODEL`。界面也可临时覆盖服务、模型和 Key，Key 不会出现在生成的 JSON 或状态接口中。修改 `.env` 后重新加载页面即可查看配置；运行中的任务仍使用启动时提交的服务和 Key。

Windows 上默认使用仓库的 `EasySpider/ElectronJS/chrome_win64`。若需自定义 Chrome 与驱动，设置进程环境变量 `AI_RPA_CHROME_BINARY`、`AI_RPA_CHROMEDRIVER`。设置 `AI_RPA_HEADLESS=1` 可用无头 Chrome。浏览器和驱动主版本必须匹配。

## 操作顺序

1. 在初始页面设置 API 服务、模型和可选的 Key，点击“进入对话”。采集需求在后续对话框中输入。
2. AI 在对话中回复起始网址、工作步骤、字段和需要澄清的问题。用户可以继续发送一条或多条补充信息、修改意见；每轮都会基于此前要求和上一版计划重新生成流程。确认之前不会打开目标网页。页面刷新后重新进入对话可恢复本次会话；重启服务会清空会话。
3. 计划明确后点击“确认流程并开始探索”。浏览器观察 DOM 候选和可选截图，模型只提交候选 ID；后端解析真实输入目标并试采当前页最多 5 条样例。
   若目标页或详情页要求登录，界面会暂停；请只在弹出的 Chrome 中自行登录，再点击“已完成登录”。系统验证后从起始网址重新试采，登录输入不会成为流程节点。
4. 检查样例和字段 XPath，点击“样例正确，保存流程”。任务 JSON 保存到 `generated/`。
5. EasySpider 主程序运行时，可点击“导入运行中的 EasySpider”，经本机 `http://127.0.0.1:8074/manageTask` 创建任务。之后在 EasySpider 中编辑或执行。

### 本地带用户信息执行

原版命令行的 `--user_data 1` 默认读取原版 `config.json` 中的 `absolute_user_data_folder`；仅加这个参数不会自动使用 AI 设计浏览器的登录资料。原型提供独立执行入口，直接把设计阶段同一个 Profile 通过 `--user_folder` 交给原版执行器，任务副本、配置、日志和输出均保存在 `ai-rpa-prototype/executor_sandbox/`，不改写原版 EasySpider 文件：

```powershell
cd ai-rpa-prototype
python run_native.py --task-id 14 --filter-nonrecords
# 或使用刚保存的任务 JSON：
python run_native.py --task-file generated/easyspider_ai_你的会话ID.json
```

先用 `--dry-run` 可检查将使用的任务、资料目录和 Chrome 路径。浏览器默认可见；加 `--headless` 可隐藏。旧任务若把问卷或推荐卡片混进商品列表，可加 `--filter-nonrecords`；它会在 `generated/` 另存修正版 JSON，供重新导入。新生成的任务会在试采时核对列表字段并自动排除完全不含采集字段的非商品卡片。若站点只显示“行业热销”等标签而没有数字销量，流程会保留网页原文，不推断数字。

如果在原版 EasySpider 界面执行已导入的任务，先关闭 AI 探索浏览器，再进入任务执行页，将原型结果中显示的**源码版相对资料目录**填到“用户本地浏览器数据目录”，点击“本地直接执行（带用户信息模式）”。只勾选带用户信息模式而不填写该目录，原版会读取自己的默认资料目录，因此会重新要求登录。打包版须按其实际数据目录重新计算相对路径。原版执行器运行时会复制资料目录；在它弹出的浏览器中重新登录，不会同步回 AI 设计资料目录。

`AI_RPA_MAX_ACTIONS` 只限制**首次网页探索**中的输入/点击次数，默认 30，以免模型反复尝试。它与样例行数无关。未指定页数时，翻页流程使用 EasySpider 原生循环和“下一页存在”条件分支，持续到没有下一页；用户明确说“采集 3 页”时，计划会保留 `page_limit=3`，流程首面加两次翻页后停止。这个页数上限只影响生成的 EasySpider 流程，不是模型探索动作预算。运行时仍应在 EasySpider 中观察站点异常和页面变化。

对话默认最多规划 15 轮（首次需求算第 1 轮），每条用户消息最多 1000 字。可在 `settings.py` 中修改 `MAX_PLAN_TURNS` 和 `MAX_USER_MESSAGE_CHARS`；前端会读取这些值，后端也会强制校验。达到轮数上限后，仍可确认已明确的流程，或开始新对话。

### 网页信息如何交给模型

确认流程后，浏览器在当前网页执行只读观察脚本，返回网址、标题、前 4500 字可见正文、最多 160 个可见元素候选、最多 20 组重复列表和专门标出的下一页候选。重复列表项及第一批列表项子元素会优先进入候选集合，避免导航和筛选区占满候选名额。元素候选包含短文本、链接、常见属性、是否可输入、边界框及后端生成的 XPath。DeepSeek 接收这些文字和结构化候选；GPT 与 Gemini 还可接收浏览器截图。当前不会把完整网页源码交给模型。模型只返回候选 ID 和受限动作，后端据此定位、试采并生成 EasySpider 节点。

计划要求翻页时，探索阶段必须选中下一页元素；未找到时会报错并阻止保存单页任务。由于网页结构和反爬限制，仍应检查试采结果及原版执行器的跨页运行。

## 当前能力与限制

- 支持单页提取、列表项循环提取、搜索后提取和基于下一页链接的翻页。字段限文本、`href`、`src`、`value`、`title`、`number`，最多 12 个。`title` 和 `number` 使用构建器内置且校验的固定读取脚本，不接受模型生成的自定义代码。`number` 只保存数字及小数点，不保存货币符号或单位。
- 网页探索默认把标题、可见文本摘要、控件、同类列表及第一条记录内的字段候选发给模型；完整 HTML、完整可访问性树、Cookie 和密码框值不在模型输入中。真实 XPath 留在本机，用于生成原版 EasySpider 的列表循环和相对字段 XPath。可用 `python tests/inspect_taobao_dom.py` 在本机比较当前网页的 HTML、可访问性树和摘要长度。
- 模型 API 的 JSON 响应会检查是否截断或为空；遇到无效 JSON 会附加严格格式要求重试一次，第二次给推理模型更大的输出预算。错误信息会区分空响应、截断和格式错误。
- 输入动作可选择真正可编辑元素，或只含一个可编辑元素的外层容器。定位失败时会刷新 DOM 快照，并把当前元素、列表和下一页 ID 重新交给模型，最多重新选择两次。元素 ID、列表 ID 和下一页 ID 按类型校验，不能互相替代。
- 已支持站点专属 Chrome 资料目录、初始页或详情页登录接管、列表→详情→返回、新标签页详情、关键词循环、原生下拉框、有限滚动加载、明确页数上限、字段默认值、Excel 多网址输入及 JSON 输出。明显的支付、购买、下单、删除、发送按钮仍会被拦截；搜索、筛选、排序和普通“提交筛选”按钮属于采集动作，可以执行；复杂 iframe 尚未覆盖。
- Excel 多网址的单页采集会在必需字段缺失时重新打开当前网址，最多尝试 3 次。仍失败时，在原有字段中记录失败网址和 `ERROR:`，避免把错误页当成正常商品；成功与错误行需由使用者分别核对。重跑整个任务时要用新输出文件并核对唯一标识，原版的断点续跑和整行去重都可能改变结果。
- 登录资料保存在本目录被忽略的 `profiles/`，其中可能含会话凭据，请只在本机保存。试采后在原版 EasySpider 中选择“带用户信息模式”，填写界面展示的**源码版相对资料目录**。打包版的数据根目录可能不同，应按绝对资料目录位置重新计算相对路径。原版执行器通常复制资料目录后运行；执行中的登录不会同步回原目录。
- 登录后的页面片段仍会发送给所选模型 API 进行采集决策，但密码输入框、常见令牌输入项和敏感 URL 参数会在观察前过滤，认证采集不发送截图。页面正文中的私有内容仍可能发送给模型；仅在你有权访问并允许发送这些页面内容时使用。
- 如果用户直接关闭自动控制浏览器，探索会转换为 `closed` 状态，界面显示“自动控制浏览器已关闭，网页探索已结束。”并停止轮询，不会展示 Selenium 的 `invalid session id` 堆栈。
- 若正式执行中会话过期，原版执行器可能正常退出并仅保存前几页，当前流程不能自动暂停并让用户登录。请核对输出与预期范围，在**原资料目录**重新登录，然后从头运行并将结果另存新文件；确认新文件完整后再使用。不要直接信任原版“从上次位置继续”：本地订单故障测试中它漏掉过一条订单的标题。

恢复时，在 EasySpider 首页用“使用带用户信息浏览器设计”打开同一资料目录，完成登录后关闭该浏览器；随后在任务保存设置中把“数据写入方式”选为**重命名写入**（值 3）、“从上次保存位置开始执行”选为**否**（值 0），再从执行页面选择“带用户信息模式”运行。重命名写入保留旧的部分结果，便于逐条核对新文件。原版保存对话框对应字段是 `dataWriteMode` 和 `startFromExit`。
- 浏览器试采验证的是当前页及字段定位。本机三页案例及两项公开站点的真实 DeepSeek 任务，均已由原版 EasySpider 打包执行器运行到末页。站点结构变化时仍需重新检查定位器。

## 验证与案例

### 登录遮罩与人工接管

如果网页使用全屏登录遮罩（例如 `J_MIDDLEWARE_FRAME_WIDGET`）导致点击被拦截，系统会把异常和脱敏后的当前地址交给模型，在 `human_login`、`retry`、`abort` 三个受限结果中选择。模型不能点击遮罩、输入账号密码或执行脚本；检测到登录/验证信号时始终优先暂停并显示人工登录界面。用户完成登录后，系统会在同一 Profile 中重新打开起始页、重新观察 DOM，再继续生成流程。

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
node --check app.js
python tests/ui_smoke.py
python tests/real_chat_smoke.py
python tests/smoke_browser.py
python tests/public_cases.py
python tests/public_browser_cases.py
python tests/executor_smoke.py
python tests/taobao_like_executor.py
python tests/taobao_like_executor.py --real-model
python tests/real_model_cases.py plan quotes
python tests/real_model_cases.py run quotes
python tests/real_model_cases.py execute quotes
python tests/real_model_cases.py plan books
python tests/real_model_cases.py run books
python tests/real_model_cases.py execute books
python tests/real_model_cases.py plan vague
python tests/real_model_cases.py revise vague
```

测试代码集中在 `tests/`，功能模块仍在本目录顶层。`tests/ui_smoke.py` 使用模拟模型和本地 Chrome 验证设置页、两轮对话、澄清门、确认和预览。`tests/real_chat_smoke.py` 与 `tests/real_model_cases.py` 会调用已配置的模型。`tests/taobao_like_executor.py` 使用不含账户信息的临时商品站点，验证销量排序、四字段、三页及原版执行器；加 `--real-model` 后由模型自行选择控件、列表和字段。`tests/inspect_taobao_dom.py` 只在本机检查淘宝页面；`tests/real_taobao_debug.py` 会把当前淘宝页面摘要发给模型，使用已登录资料目录前须确认允许向所配置的模型 API 发送该页面内容。`tests/smoke_browser.py` 使用本机页面验证输入框容器映射、点击、列表试采与任务图构建。Windows 上运行原版执行器案例前，需在本目录建立资源目录连接：

```powershell
New-Item -ItemType Directory -Force -Path .\executor_sandbox\EasySpider\resources\app
New-Item -ItemType Junction -Path .\executor_sandbox\EasySpider\resources\app\chrome_win64 -Target ..\EasySpider\ElectronJS\chrome_win64
```

执行器测试只在已忽略的 `executor_sandbox/` 写入临时任务和 CSV，验证搜索输入、点击、完整书名属性读取、列表提取和翻页。真实模型测试脚本会调用 `.env` 中配置的模型；请先检查 `plan` 阶段写入 `generated/` 的网址与步骤，再运行对应 `run` 和 `execute`。已用配置的 DeepSeek 模型完成 Quotes 的 10 页 100 条，以及 Books 的 50 页 1000 条。详见 [TEST_CASES.md](TEST_CASES.md)。

复杂用例可运行 `python tests/complex_cases.py t1` 至 `t6`；配置模型联测为 `python tests/real_complex_cases.py t1` 至 `t6`。故障注入示例：`python tests/complex_cases.py t5 --fault temporary_503`、`--fault persistent_503`，以及 `python tests/complex_cases.py t6 --fault session_expires --recover`。这些测试只使用本地模拟站和模拟账号，不需要真实网站登录。

主要文件：`llm.py` 为 DeepSeek/GPT/Gemini 适配；`observer.py` 生成 DOM 候选；`agent.py` 做计划和探索；`workflow.py` 生成与校验原生任务；`server.py`、`index.html`、`app.js` 提供本地测试界面。
