# EasySpider AI 流程生成原型

本目录独立于 `EasySpider` 源码。模型先生成可供确认的工作步骤，再根据真实网页的 DOM 候选请求 `input`、`click`、`extract` 等受限动作。确定性构建器把动作和字段定位编译成 EasySpider 原生任务 JSON。原型提供只读流程图和需要确认的 AI 局部修改；手工修改和网页调试使用 **EasySpider 原版设计器**。点击打开原版设计器或导入时，才会写入原系统任务列表。

代码布局、模块职责和主要函数说明见 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)。

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
AI_RPA_MODEL=deepseek-flash
DEEPSEEK_API_KEY=填入你的Key
OPENAI_API_KEY=
GEMINI_API_KEY=
AI_RPA_MAX_ACTIONS=30
AI_RPA_RISK_REVIEW=0
```

`AI_RPA_PROVIDER` 可选 `deepseek`、`gpt`、`gemini`。对应默认模型分别是 `deepseek-flash`、`gpt-4o-mini`、`gemini-2.5-flash`；可以修改 `AI_RPA_MODEL`。界面也可临时覆盖服务、模型和 Key，Key 不会出现在生成的 JSON 或状态接口中。修改 `.env` 后重新加载页面即可查看配置；运行中的任务仍使用启动时提交的服务和 Key。

Windows 上默认使用仓库的 `EasySpider/ElectronJS/chrome_win64`。若需自定义 Chrome 与驱动，设置进程环境变量 `AI_RPA_CHROME_BINARY`、`AI_RPA_CHROMEDRIVER`。设置 `AI_RPA_HEADLESS=1` 可用无头 Chrome。浏览器和驱动主版本必须匹配。

## 操作顺序

1. 在初始页面设置 API 服务、模型和可选的 Key，点击“进入对话”。采集需求在后续对话框中输入。
2. AI 在对话中回复起始网址、工作步骤、字段和需要澄清的问题。用户可以继续发送一条或多条补充信息、修改意见；每轮都会基于此前要求和上一版计划重新生成流程。确认之前不会打开目标网页。页面刷新后重新进入对话可恢复本次会话；重启服务会清空会话。
3. 计划明确后点击“确认流程并开始探索”。浏览器观察 DOM 候选和可选截图，模型只提交候选 ID；后端解析真实输入目标并试采当前页最多 5 条样例。
   若目标页或详情页要求登录，界面会暂停；请只在弹出的 Chrome 中自行登录，再点击“已完成登录”。系统验证后从起始网址重新试采，登录输入不会成为流程节点。
4. 探索完成后，在原型查看试采样例和只读流程图。流程图显示操作顺序、嵌套循环与条件分支，可缩放、滚动及点击节点查看详情。可先要求 AI 修改，查看差异并确认后，流程图自动刷新。手工修改时启动原版 EasySpider，在首页选择“使用带用户信息浏览器设计”，把原型显示的资料目录填到“指定用户信息目录”，开始设计。源码版可直接复制显示的相对路径；打包版需按其数据根目录换算相对路径。原版 Chrome 会复用探索阶段的登录资料。
5. 回到原型点击“在 EasySpider 原版设计器打开流程”。首次创建原版任务，随后调用原版任务列表“修改”按钮的入口，打开真正的 Electron 流程图和设计 Chrome。在原版自由修改节点、拖动、选网页元素和调试，完成后点击原版保存。
6. 继续对话或使用修改输入框要求 AI 调整。AI 每次先读取原版**最新保存**的图；涉及新元素时还读取原版设计 Chrome 的 DOM 候选。查看差异后点击“确认写回并刷新原版设计器”：修改写入同一个任务 ID，原型预览和原版图都会刷新。建议生成后如果又在原版保存了新改动，会阻止覆盖，请重新生成建议。
7. 手工修改先在 EasySpider 保存，再点击原型的“读取原版已保存流程”，同步最新任务并刷新只读流程图。点击“样例正确，保存流程”导出当前已保存任务到 `generated/`。再次打开或导入复用同一个任务 ID；重新打开会读取最新保存内容，不创建重复任务。改动后旧试采样例会清空，应在原版调试并检查完整任务输出。

原版设计器接入的完整调用链和限制见 [PROJECT_STRUCTURE.md 第 11 节](PROJECT_STRUCTURE.md#11-使用-easyspider-原版设计器修改与调试)。**原版未保存的改动不能通过任务接口读取**，请在请求 AI 修改或重新打开、刷新任务前先保存。网页链接仅用于查看流程图，完整调试需由 EasySpider 首页启动设计 Chrome；若自动连接失败，可在原版任务列表按显示的 ID 手动点击“修改”，AI 仍可读取保存的图并修改页数等参数。正式执行前先退出原版设计模式，释放资料目录。

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

确认流程后，浏览器扫描当前主文档的各类元素（含自定义标签），返回网址、标题、前 6000 字符可见正文、默认最多 1000 个可见元素候选、最多 80 组重复列表和下一页候选。模型收到的正文进一步截为 4000 字符。搜索、输入、交互控件优先保留，记录字段在多个列表之间轮流分配名额，并保留更多容器、标签、父节点 ID 和页面区域信息。元素包含短文本、链接、常见属性、输入与点击能力、边界框、ARIA/标签语义、图标提示和遮挡状态。覆盖统计明确给出符合条件的元素数、返回数和省略数。候选 XPath 留在本机，模型通过 ID 选择元素。

列表排序采用重复结构、一致性、文本与字段密度、标题、时间信息和所在区域等共同特征，不给价格或销量加分。导航短标签和包含多条记录的大容器降低优先级，但仍可被模型选择。每个列表 XPath 会核对是否确实匹配该组记录。

模型可先请求 `observe`，按控件、内容或均衡方式补充至最多 3000 个元素；需要层级结构时请求 `inspect_dom`，读取元素子树、搜索区域或首条记录。仍无法定位时可分块读取清理后的整页主文档：局部默认 16000 字符，整页默认 48000 字符，响应给出 `next_offset`。HTML 中的 `data-rpa-id` 和 `data-rpa-list` 对应本地元素和列表映射，新发现的元素、列表均可用于生成流程。清理去掉脚本/样式正文、常见敏感输入和表单值，脱敏 URL 参数，保留标签层级和渲染状态。分块期间 DOM 改变会要求重新读取，中文和 emoji 不会在截断处被拆坏。

探索和详情页共用最多 12 次补充读取预算；这些工具不操作网页、不写进流程、不占输入/点击预算。上限可在 `settings.py` 修改。若同一栏目分为多个结构兼容的列表，可用 `extract.list_ids` 合并最多 16 组，生成一个原生 XPath 联合列表循环。探索还检查搜索是否完成，并在原生采集节点等待最后一条记录出现实际字段，减少异步加载少采；这不能保证无限滚动的所有内容都已加载。

DeepSeek Flash、GPT 与 Gemini 接收上述摘要，并按需接收清理后的 DOM；截图条件允许时附带 PNG，计划要求登录或本次已经人工接管登录时不附截图。DeepSeek/GPT 使用 `image_url`，Gemini 使用 `inline_data`。图片 API 请求失败会抛出异常，不自动删除图片改走文字请求。模型只返回候选 ID 和受限动作，后端据此定位、试采并生成 EasySpider 节点。分层观察的调用链和配置见 [PROJECT_STRUCTURE.md 第 14 节](PROJECT_STRUCTURE.md#14-通用列表与分层-dom-观察2026-10-09)。较大的候选集合增加模型输入，所选模型上下文较小时应调低预算。

计划要求翻页时，探索阶段必须选中下一页元素；未找到时会报错并阻止保存单页任务。由于网页结构和反爬限制，仍应检查试采结果及原版执行器的跨页运行。

探索允许正常 HTTP(S) 跨域跳转，例如百度新闻首页 `news.baidu.com` 的搜索结果实际位于 `www.baidu.com`。模型会收到起始与当前主机名，并结合当前结果、已执行动作和确认计划继续探索；域名变化本身不会停止流程，也不会被直接当成登录。只有页面实际出现登录或验证码信号时才转交人工处理。跨域后的搜索仍需通过搜索完成检查，原来的输入、点击顺序会保留并编译为可重复流程。

## 当前能力与限制

- 支持单页提取、列表项循环提取、搜索后提取和基于下一页链接的翻页。字段限文本、`href`、`src`、`value`、`title`、`number`，最多 12 个。`title` 和 `number` 使用构建器内置且校验的固定读取脚本，不接受模型生成的自定义代码。`number` 只保存数字及小数点，不保存货币符号或单位。
- 网页探索默认发送语义候选，按需补充局部或整页清理 DOM；不发送未经清理的原始 HTML、完整可访问性树、Cookie 和密码框值。当前覆盖率衡量的是主文档中符合本地可见性等条件的候选，不代表整个网站的全部信息。真实 XPath 留在本机，用于生成原版 EasySpider 的列表循环和相对字段 XPath。
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

通用搜索回归使用 `tests/semantic_search_browser.py`，覆盖导航干扰、图片/SVG 无文字控件、透明输入、受控输入、附件按钮、遮挡、登录后 DOM 改变和延迟记录；加 `--native` 后，两种布局均由原版执行器输出 8 条完整记录。`tests/live_search_case.py` 默认测试小红书“合肥、标题与作者、一页”，支持 `--url`、`--request` 换站点。本轮真实模型试采 5 条，原版回放输出 20 条且与当前页面逐条一致；其中两条卡片在网页中也没有标题。详细核对见 [TEST_CASES.md](TEST_CASES.md)。

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
node --check app.js
node --check editor.js
node --check flowchart.js
python tests/ui_smoke.py
python tests/flowchart_browser_smoke.py
python tests/editor_browser_smoke.py
python tests/editor_browser_smoke.py --real-model
python tests/editor_browser_smoke.py --require-discovery
python tests/editor_browser_smoke.py --native
python tests/editor_browser_smoke.py --real-model --native
python tests/multimodal_smoke.py
python tests/multimodal_smoke.py --real-model
python tests/real_chat_smoke.py
python tests/smoke_browser.py
python tests/semantic_search_browser.py
python tests/semantic_search_browser.py --native
python tests/layered_observation_browser.py
python tests/chinese_web_observation.py
python tests/news_native_replay.py generated/science_news_complete_task.json
python tests/live_search_case.py --inspect
python tests/live_search_case.py --run
python run_native.py --task-file generated/live_search_task.json --timeout 150
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

原版设计器联调使用 `tests/editor_browser_smoke.py`。它在隔离数据目录加载未改动的原版 Electron 代码，验证原版手工保存、节点调试、AI 建议确认、真实 DOM 字段绑定和同 ID 刷新；需 Windows 原版 Electron 及空闲的 `8084` 端口。`--real-model` 会把本地商品测试页和图摘要发送给 `.env` 配置的模型；`--require-discovery` 额外检查实际 Windows ChromeDriver 进程发现。测试会关闭自身创建的进程，不操作用户的浏览器或任务。

只读预览联调使用 `tests/flowchart_browser_smoke.py`：在真实 Chrome 中检查嵌套循环、并排分支、缩放与节点选择不修改任务、AI 确认后的刷新、原版保存内容同步、页面刷新恢复及同 ID 重开。模型建议使用模拟响应，任务存储使用原版 `server.js` 的隔离实例；不占用原版固定端口，也不连接用户正在使用的原版浏览器。

主要文件：`llm.py` 为 DeepSeek/GPT/Gemini 适配；`observer.py` 生成 DOM 候选；`agent.py` 做计划和探索；`workflow.py` 生成与校验原生任务；`editor.py` 应用局部图修改；`native_bridge.py` 连接原版存储与设计 Chrome；`server.py`、`index.html`、`app.js`、`editor.js` 提供本地测试界面；`flowchart.js` 与 `flowchart.css` 渲染只读流程图。

`dom_observer.js` 由 `observer.py` 在 Selenium 内执行，负责通用候选、列表识别和清理 DOM，不是前端页面脚本。中文站点覆盖测试包括百度、人民网、中新网、澎湃、央视、科学网、哔哩哔哩与豆瓣读书；真实模型与原版执行器分别完成中新新闻列表和科学网“要闻”全部分组。结果见 [TEST_CASES.md](TEST_CASES.md)。
