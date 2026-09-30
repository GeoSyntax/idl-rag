# GEE、IDL 与 Python 接入

IDL RAG Panel 的数据和执行方式可以按需要组合：

| 路线 | 用途 | 是否必须安装 |
|---|---|---|
| Google Earth Engine | 获取小范围公开遥感数据，保存为当前会话的数据 artifact | 需要 Earth Engine 账号和 Google Cloud 项目 |
| 本地 IDL | 运行 Agent 生成的 `.pro` 脚本，作为教学或结果对照 | 需要本机有许可证的 IDL/ENVI |
| Python/GDAL | 处理本地 GeoTIFF、GEE 下载结果和研究项目数据 | 项目后端依赖已包含常用 Python 栅格库 |

三条路线可以一起使用，也可以只启用 Python。GEE 负责提供数据，Python 或 IDL 负责处理数据，结果会回到 Chat 或 Research 页面。

## 1. 先完成通用配置

在仓库根目录执行：

```powershell
Copy-Item .env.example .env
```

至少修改：

```text
IDLRAG_AUTH_SECRET=换成一段随机字符串
VITE_API_BASE_URL=http://127.0.0.1:8000/api
```

启动后端和前端：

```powershell
uv run --project backend uvicorn app.main:app --app-dir backend --reload
```

```powershell
npm run dev --prefix frontend
```

### 1.1 本地使用 gemin2api

如果本机已经运行 `gemin2api` 的 OpenAI 兼容服务，Chat 和 Agent 不需要再接入云端 OpenAI。当前开发机使用的实例是：

```text
API Base URL: http://127.0.0.1:8081/v1
聊天模型：gemini-3.6-flash
```

在平台“设置”页填写：

```text
Provider 名称：gemin2api-local
API Base URL：http://127.0.0.1:8081/v1
聊天模型：gemini-3.6-flash
API Key：填写 gemin2api/config.json 中 api_keys 的值
```

保存并测试连接后，普通 Chat 和 Agent 的回答都会通过该 OpenAI 兼容接口进行 SSE 流式生成。API Key 只保存在本地数据库的加密设置项中，不要写入 Git、README 或截图。若 `gemin2api` 尚未启动，平台会显示模型服务连接失败，并停止当前流，不会伪造“未连接云端模型”的正常回答。

Agent 的工具决策接口本身是一次一次的 JSON 请求；本地模型首 token 较慢时，后端会每 8 秒发送一次 `waiting` 状态，前端在同一个回答气泡内显示“模型仍在响应”，而不是生成第二条回答。真正的 `done`/`error` 仍然只发送一次；点击停止后，迟到的模型结果不会继续写入会话。

`gemin2api` 当前提供的是聊天兼容接口（`/v1/chat/completions`），不等于 Embedding 服务。设置页的 **Embedding** 卡片需要单独填写一个实现了 `/v1/embeddings` 的本地或 OpenAI-compatible 服务；留空时平台会兼容性地复用聊天地址，但如果该网关没有 Embedding 路由，测试会明确返回 401/404，而不会把聊天连接误判为向量服务。没有可用 Embedding 时，默认检索会退回 FTS/规则排序，`vector_only` 仅用于诊断。

在“对话 → Agent”中打开“允许外部文献搜索”后，即使没有选择研究项目，也可以明确要求 Agent 查询 Crossref、OpenAlex 或 Semantic Scholar 的公开论文元数据。这个入口只发送整理后的公开检索词，返回候选标题、作者、年份、DOI 和来源链接，不上传影像、私有路径、项目文件或密钥，也不会自动写入项目审计、证据卡或 RAG。需要导入项目、形成可复核证据链时，再到研究页绑定项目并使用项目内的文献搜索。

## 2. 接入 Google Earth Engine

### 2.1 准备 Google Cloud 项目

1. 登录有 Earth Engine 权限的 Google 账号。
2. 创建或选择一个 Google Cloud 项目，记下 **Project ID**，不要填写数字型 Project Number。
3. 在 Google Cloud Console 中启用 **Google Earth Engine API**。
4. 确认该项目可以初始化 Earth Engine。

### 2.2 本地开发使用 ADC 登录

本地电脑推荐 ADC 方式，不需要把服务账号密钥写进仓库。

先在浏览器完成一次授权：

```powershell
uv run --project backend python -c "import ee; ee.Authenticate(auth_mode='localhost')"
```

在 `.env` 中设置：

```text
IDLRAG_GEE_ENABLED=true
IDLRAG_GEE_AUTH_MODE=adc
IDLRAG_GEE_PROJECT=你的 Google Cloud Project ID
```

重启后端。进入 Chat 页面后，点击“获取 GEE 数据”，选择允许的数据集、空间范围、波段、分辨率和 CRS，下载结果会保存为当前会话中的 `gee_data` artifact。

### 2.3 课题组服务器使用服务账号

多人共用的内网服务可以使用服务账号：

```text
IDLRAG_GEE_ENABLED=true
IDLRAG_GEE_AUTH_MODE=service_account
IDLRAG_GEE_PROJECT=你的 Google Cloud Project ID
IDLRAG_GEE_SERVICE_ACCOUNT_EMAIL=service-account-name@project-id.iam.gserviceaccount.com
IDLRAG_GEE_SERVICE_ACCOUNT_KEY_JSON={...}
```

服务账号必须有 Earth Engine 项目访问权限。`IDLRAG_GEE_SERVICE_ACCOUNT_KEY_JSON` 只能放在服务器的环境变量或密钥管理器中，不能提交到 Git、README 或聊天记录。

### 2.4 GEE 接入边界

当前 GEE 接入是受限的数据下载，不执行任意 Earth Engine Python/JavaScript 代码，也不提交长期 Drive 或 Cloud Storage 导出任务。下载大小、范围、波段数量和分辨率由 `.env` 中的限制控制：

```text
IDLRAG_GEE_ALLOWED_DATASETS=CGIAR/SRTM90_V4,COPERNICUS/S2_SR_HARMONIZED,LANDSAT/LC08/C02/T1_L2
IDLRAG_GEE_MAX_DOWNLOAD_MB=100
IDLRAG_GEE_MAX_BBOX_DEGREES=5
IDLRAG_GEE_MAX_BANDS=12
IDLRAG_GEE_MIN_SCALE=1
IDLRAG_GEE_MAX_SCALE=10000
```

### 2.5 GEE 最小验证

授权和配置完成后，可以下载一小块 SRTM 检查连接：

```powershell
$env:PYTHONPATH='backend'
$env:IDLRAG_GEE_ENABLED='true'
$env:IDLRAG_GEE_AUTH_MODE='adc'
$env:IDLRAG_GEE_PROJECT='你的 Google Cloud Project ID'
uv run --project backend python -c "import ee, httpx; ee.Initialize(project='$env:IDLRAG_GEE_PROJECT'); region=ee.Geometry.Rectangle([116.30,39.85,116.31,39.86]); image=ee.Image('CGIAR/SRTM90_V4').select(['elevation']).clip(region); url=image.getDownloadURL({'name':'idlrag_gee_smoke','scale':90,'crs':'EPSG:4326','region':region,'format':'GEO_TIFF'}); response=httpx.get(url,timeout=120,follow_redirects=True); response.raise_for_status(); print({'bytes':len(response.content),'content_type':response.headers.get('content-type')})"
```

看到非零的 `bytes` 就说明 Earth Engine 下载链路已打通。

## 3. 接入本地 IDL

### 3.1 准备命令行 IDL

需要一份有许可证的 IDL/ENVI 安装，并且能在命令行中找到真正的 `idl.exe`。Windows 示例：

```text
IDLRAG_IDL_EXECUTABLE=D:\envi5.6\ENVI56\IDL88\bin\bin.x86_64\idl.exe
```

不要把下面这些程序填入 `IDLRAG_IDL_EXECUTABLE`：

- `idlde.exe`：Workbench 启动器；
- `envi_idl.exe`：ENVI 图形启动器；
- `idlrt.exe`：只能运行 SAV 的运行时。

平台需要调用命令行 IDL：

```text
idl.exe -batch <runner.pro>
```

在 `.env` 中设置：

```text
IDLRAG_IDL_EXECUTABLE=D:\envi5.6\ENVI56\IDL88\bin\bin.x86_64\idl.exe
IDLRAG_IDL_RUN_TIMEOUT_SECONDS=30
```

重启后端，平台会读取新的配置。

### 3.2 在 Chat 中运行 IDL

1. 在知识库中导入 IDL 手册、示例 `.pro` 文件和项目规范。
2. 在 Chat 中选择知识库，要求 Agent 生成 `.pro` 文件。
3. 检查生成的代码和引用来源。
4. 点击“运行 IDL”。
5. 查看退出码、stdout/stderr、输出图片和可下载文件。

平台只允许用户主动运行当前用户拥有的 `.pro` artifact，不接受任意 shell 命令或任意本地路径。输入数据会暂存到本次 run 的 `inputs/`，脚本输出会收集到 `outputs/`。

### 3.3 在 Research 页面使用 IDL 对照

Research 页面可以把 IDL/ENVI 生成的 GeoTIFF 登记为 `derived` 资产，再与 Python Runner 的结果放在同一验证方案下比较。这里的 IDL 结果是实现对照，不会自动被当作真实真值。

如果使用 ENVI API，需要在 `.pro` 中按本机许可环境完成 batch 初始化。先用简单的纯 IDL 输出测试命令行，再接入 ENVI 栅格处理，排错会更容易。

### 3.4 IDL 接入检查表

- `IDLRAG_IDL_EXECUTABLE` 指向 `idl.exe`，不是图形启动器；
- IDL 许可证在当前 Windows 用户下可用；
- 生成的 `.pro` 能在 IDL 命令行中独立运行；
- 输出图片或 GeoTIFF 写入脚本约定的输出目录；
- ENVI API 脚本包含必要的 batch 初始化；
- 后端重启后再点击“运行 IDL”。

## 4. Python/GDAL 路线

Python 不需要额外的商业许可证。项目已经包含 Rasterio、NumPy、Matplotlib 等依赖，适合处理：

- 用户上传的 GeoTIFF；
- GEE 下载的 `gee_data` artifact；
- Research 项目中冻结的数据快照；
- IDL/ENVI 导出的 GeoTIFF 对照结果。

本地安装依赖：

```powershell
uv sync --project backend
```

Python 实验会在 Research 页面保存运行状态、阶段图、GeoTIFF、指标和证据包。需要修改公式时，建议先运行 `preview`，确认图像和指标后，再创建正式实验。

## 5. 常见问题

| 现象 | 处理方式 |
|---|---|
| GEE 提示 API 未启用 | 在对应 Project ID 下启用 Google Earth Engine API，并重启后端。 |
| GEE 授权成功但初始化失败 | 检查 `.env` 中的 Project ID 是否为字符串 ID，而不是数字 Project Number。 |
| Chat 中没有“获取 GEE 数据” | 确认 `IDLRAG_GEE_ENABLED=true`，然后重启后端并重新登录。 |
| IDL 被拒绝执行 | 检查配置是否指向 `idl.exe`，不要使用 `idlde.exe`、`envi_idl.exe` 或 `idlrt.exe`。 |
| IDL 运行成功但没有图片 | 检查 `.pro` 是否把 PNG/TIFF 写入受限的输出目录，并确认扩展名在允许列表中。 |
| Python 可以运行，IDL 不行 | 先使用纯 IDL 栅格读写做最小测试，再逐步加入 ENVI API 初始化。 |

更完整的环境变量、数据限制和安全规则见 [`configuration.md`](./configuration.md)。
