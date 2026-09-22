# 药品使用统计系统（化学 / 物理双空间）

实验室药品的**入库 / 使用登记、实时库存、低库存提醒**工具，面向实验室多人共用场景。
**同一套系统同时服务化学、物理两个实验室**：首次登记时输入哪个实验室的密码，就进入哪个实验室的独立界面——标题、主题配色不同，数据库文件各自独立、数据完全隔离。
后端 Flask + SQLite（免安装数据库服务，数据就是程序目录下的文件），前端原生 HTML / CSS / JavaScript（无构建步骤），手机浏览器也能用。

## 功能特色

- **化学 / 物理双空间**：一套服务、一组入口，首次登记时用密码分流——化学实验室密码进化学空间（蓝色主题），物理实验室密码进物理空间（紫色主题）；两个空间各自使用独立数据库（`chemicals.db` / `physics.db`），药品、记录、低库存阈值完全互不可见；AI 助手身份、Excel 模板名称、页面标题都随空间切换
- **手动登记**：入库 / 使用两种操作，选择药品即显示当前剩余量与登记后剩余量，库存不足实时提醒
- **AI 辅助登记**：一句话自然语言（兼容语音输入，自动剔除错误标点）即可同时拆分多种药品、多条入库/使用记录；返回结构化预览，逐条检查修改、删除问题条目后再批量提交
  - 接入硅基流动（SiliconFlow）Qwen 模型，免费模型即可使用，失败自动重试最多 3 次
  - **入库允许登记库中没有的药品**：预览中标注"新药"，提交时自动按识别单位建立药品档案；使用类操作若药品不存在则标红阻断
  - 服务端带时间戳的 AI 调试日志（输入原文、模型、token 用量、返回原文、解析结果），便于排查问题
- **实时库存**：入库为正、使用为负实时汇总；**低库存药品红色数字 + 整行浅红底并固定置顶**；阈值可在线设置，也可在 Excel 模板中随药品导入
- **Excel 批量导入**：随时可用（不限于空系统），提供四列模板（药品名称、单位、入库量、低库存提醒），新药自动建档、老药追加入库；导入区默认折叠
- **登记记录**：每条记录含操作人与时间；**默认只加载最近 7 天**，可按日期范围查询、"加载更多"每次追加 50 条、"查看全部记录"前弹窗提醒大量数据可能较慢
- **管理员机制**：首次使用时输入管理员密码的人成为管理员（角色存浏览器本地）；管理员可撤销任意记录——库存按该记录逆向恢复（入库减回、使用加回），原记录保留并灰显标注撤销人，同时生成一条撤销说明
- **首次使用强制登记**：第一次打开必须登记真实姓名（存浏览器本地，作为之后每次操作的操作人）并输入密码，之后免重复登记
- **退出登录**：顶栏右上角一键退出，只清除本机的登录记录（姓名、角色、实验室空间三条），界面偏好等其余本地数据保留，退出后重新登记即可再次进入
- **部署友好**：一个 `config.py` 管全部配置（也支持环境变量）；前端自动探测反向代理子路径，**直连与反代到任意子路径（如 `/lab`）使用同一份代码，后端零配置、零重定向**

## 技术栈

- Python 3 + Flask + 标准库 sqlite3
- 前端：原生 JavaScript（无框架、无打包），Excel 处理由后端 openpyxl 完成
- AI：OpenAI SDK 兼容方式调用硅基流动 API

## 文件结构

```
├── app.py                # 后端主程序（页面路由 + 全部 API）
├── ai_client.py          # AI 辅助登记模块（硅基流动 Qwen，含调试日志）
├── config.example.py     # 配置模板（复制为 config.py 后填写）
├── requirements.txt      # Python 依赖
├── delete_db_linux.sh    # Linux 生产环境重置数据库脚本（两个空间的库都删除并按当前格式重建，无需重启服务）
├── test_ai.py            # 硅基流动 API 连通性测试脚本（可选）
├── templates/            # 页面模板（登记 / 实时库存 / 登记记录，共用 base）
└── static/               # 前端 js / css
```

首次运行会自动在程序目录创建两个数据库：化学空间 `chemicals.db`、物理空间 `physics.db`。

## 快速开始

```bash
pip install -r requirements.txt

# 1. 复制配置模板并填写（端口、API Key、化学/物理各两组密码）
cp config.example.py config.py      # Windows: copy config.example.py config.py
#    然后用编辑器打开 config.py 填好各项

# 2. 启动
python app.py
```

默认监听 `127.0.0.1:5014`，浏览器访问 `http://127.0.0.1:5014/`（自动进入登记页）。
启动横幅会打印端口、AI Key 是否已配置；任一密码为空、或四个密码之间有重复时会有警告。

> 不配置 `SILICONFLOW_API_KEY` 也能正常使用手动登记、库存、Excel 导入等全部非 AI 功能，仅 AI 辅助登记不可用。

## 配置说明

所有配置集中在项目根目录的 `config.py`（由 `config.example.py` 复制而来，**该文件含敏感信息，已被 .gitignore 忽略**）：

| 文件中的配置项 | 含义 | 对应环境变量（优先级更高） |
|---|---|---|
| `PORT_IN_FILE` | 服务监听端口，默认 5014 | `PORT` |
| `SILICONFLOW_API_KEY_IN_FILE` | 硅基流动 API Key（留空则禁用 AI 功能） | `SILICONFLOW_API_KEY` |
| `USER_PASSWORD_IN_FILE` | **化学**实验室普通用户首次登记密码（**部署时务必修改**） | `CHEM_USER_PASSWORD` |
| `ADMIN_PASSWORD_IN_FILE` | **化学**实验室管理员首次登记密码，大小写不敏感（**部署时务必修改**） | `CHEM_ADMIN_PASSWORD` |
| `PHYSICS_USER_PASSWORD_IN_FILE` | **物理**实验室普通用户首次登记密码（**部署时务必修改**） | `PHYSICS_USER_PASSWORD` |
| `PHYSICS_ADMIN_PASSWORD_IN_FILE` | **物理**实验室管理员首次登记密码（**部署时务必修改**） | `PHYSICS_ADMIN_PASSWORD` |

首次登记时输入哪个实验室的密码就进入哪个空间；四个密码必须互不相同（启动时会自检并警告）。

规则：环境变量已设置且为非空值（忽略首尾空格）时覆盖文件值，因此同一份代码可以直接进 Docker / 宝塔进程管理，靠环境变量注入密钥与密码。

## 生产部署（反向代理子路径）

前端会自动探测反代前缀：取地址栏路径第一段，若不是应用自身路由（`register` / `inventory` / `records` / `api` / `static`），就作为 base 前缀拼到所有链接、静态资源和 API 请求上。因此：

- 直连 `http://host:5014/register` → 无前缀
- 反代到 `http://host/lab/`、`http://host/chemicals/` 等任意路径 → 自动适配
- 换路径、换端口都不需要改代码，后端不生成任何重定向

nginx 只需最普通的"剥前缀"转发（`proxy_pass` 末尾带 `/`）：

```nginx
location /lab/ {
    proxy_pass http://127.0.0.1:5014/;   # 末尾斜杠：剥掉 /lab 前缀
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

进程方式：

```bash
# 后台常驻
nohup python3 app.py > app.log 2>&1 &
# 或 gunicorn
gunicorn -b 127.0.0.1:5014 app:app
```

## 常用接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/drugs` | 药品列表 + 实时库存 |
| POST | `/api/drugs/<id>/threshold` | 设置低库存提醒阈值（空值表示不提醒，仅接受正数） |
| GET  | `/api/records` | 登记记录（支持 `start`/`end` 日期、`limit`/`offset` 分页、`all=1`，返回 `{records,total,has_more}`） |
| POST | `/api/records` | 单条登记（必须带 `operator`） |
| POST | `/api/records/batch` | 批量登记（AI 预览确认提交；入库新药自动建档） |
| POST | `/api/records/<id>/revoke` | 管理员撤销一条记录（库存按记录逆向恢复） |
| POST | `/api/ai/parse` | AI 解析自然语言描述 |
| GET  | `/api/import/template` | 下载 Excel 导入模板 |
| POST | `/api/import/excel` | Excel 批量导入入库 |
| POST | `/api/user/verify` | 首次使用登记校验（姓名 + 密码，返回角色与实验室空间 `role`/`lab`） |

除模板下载用 `?lab=chem|physics` 外，业务接口均通过请求头 `X-Lab: chem|physics` 选择实验室空间（缺省为 `chem`），两个空间的所有数据接口因此完全隔离。

## 清空开发测试数据（Linux 生产环境）

在服务器程序目录执行：

```bash
bash delete_db_linux.sh
```

按提示输入 `y` 确认后，脚本会把**化学与物理两个空间的库**都删除并**按当前表结构立即重建空库**，**无需重启服务**。
