# 招生公告监控

一个可在 GitHub Actions 云端或 Windows 本机运行的零成本 Python 小工具。

## 推荐：GitHub 云端运行

云端版不依赖你的电脑开机，每小时自动检查一次。请直接阅读：

**[GitHub 云端部署说明](GITHUB_CLOUD.md)**

云端版已经包含：

- `.github/workflows/admission-radar.yml` 定时工作流
- `config.cloud.json` 云端配置
- GitHub Secrets 邮箱授权码保护
- 云端 SQLite 状态持久化
- 每 30 天一次的轻量心跳，防止公开仓库因长期无活动而停用计划任务
- 手动“只测试邮件”入口

下面的内容是 Windows 本地运行方式，可作为备用。

当前版本精准监控：

- 中央财经大学研究生院“硕士招生（双证）”
- <https://gs.cufe.edu.cn/zsgz/sszs_sz_.htm>

程序只抓取该栏目第一页的公告标题、发布日期和原文链接。首次运行会把当前公告保存为基线，不发邮件；之后出现数据库中没有见过的新链接时，才发送邮件。邮件发送失败不会丢通知，下次运行会自动重试。

## 功能

- 精准抓取公告列表，不抓导航栏和分页链接
- 优先读取链接的完整 `title`，避免长标题被省略
- SQLite 保存历史，无需安装数据库
- 首次运行只建立基线
- 后续检测新增公告并发送 HTML + 纯文本邮件
- 邮件标题可点击直达学校官网
- 网络请求失败自动重试
- 页面结构变化时安全失败，不覆盖历史、不误报
- 控制台日志 + 自动轮转的文件日志
- Windows 任务计划程序安装与卸载脚本

## 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- 一台在检测时间处于开机状态且能够联网的电脑
- 一个支持 SMTP 的邮箱

项目本身不需要服务器、域名、云数据库或付费推送服务。

## 1. 安装

打开 PowerShell，进入本项目目录：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .\config.example.json .\config.json
```

如果电脑找不到 `py`，可把第一条命令改成：

```powershell
python -m venv .venv
```

## 2. 配置邮箱

用记事本打开 `config.json`，修改 `email` 部分：

```json
{
  "enabled": true,
  "smtp_host": "smtp.qq.com",
  "smtp_port": 465,
  "security": "ssl",
  "username": "你的QQ邮箱@qq.com",
  "password": "",
  "password_env": "ADMISSION_RADAR_SMTP_PASSWORD",
  "from_address": "你的QQ邮箱@qq.com",
  "to_addresses": [
    "接收提醒的邮箱@example.com"
  ],
  "subject_prefix": "[招生公告监控]",
  "timeout_seconds": 30
}
```

QQ 邮箱等服务通常要求先在邮箱设置里开启 SMTP，再使用单独生成的“授权码”，不是 QQ/邮箱登录密码。具体入口和服务器参数可能调整，请以邮箱服务商当前说明为准。

### 推荐：把授权码放在用户环境变量中

在 PowerShell 执行下面的命令，把示例文字换成真实授权码：

```powershell
[Environment]::SetEnvironmentVariable(
  "ADMISSION_RADAR_SMTP_PASSWORD",
  "这里填写邮箱授权码",
  "User"
)
```

关闭并重新打开 PowerShell，使新环境变量生效。此时 `config.json` 中的 `password` 保持为空即可。

### 简单方式：授权码写入本地配置

也可以把授权码填入 `config.json` 的 `password`。该文件已被 `.gitignore` 排除，但仍应注意：

- 不要把 `config.json` 发给别人
- 不要上传到网盘公开链接或 GitHub
- 不要填写邮箱登录密码，应填写 SMTP 授权码

如果 `password_env` 对应的环境变量存在，程序会优先使用环境变量。

## 3. 测试邮件

先单独测试邮箱配置：

```powershell
.\.venv\Scripts\python.exe .\main.py --config .\config.json --test-email
```

成功时会看到：

```text
测试邮件发送成功，请检查收件箱和垃圾邮件文件夹。
```

## 4. 首次抓取并建立基线

运行：

```powershell
.\.venv\Scripts\python.exe .\main.py --config .\config.json
```

首次运行的典型日志：

```text
成功提取 10 条公告。
首次运行：已为“中央财经大学硕士招生（双证）”建立 10 条公告基线，本次不发邮件。
```

生成的本地文件：

```text
data\radar.db
logs\admission_radar.log
```

以后再次运行，如果列表中出现新公告，程序会：

1. 把新标题、日期和链接写入 SQLite
2. 发送一封包含可点击链接的邮件
3. 邮件成功后标记为已通知

## 5. 安装 Windows 定时任务

下面的命令默认每 60 分钟检查一次：

```powershell
powershell -ExecutionPolicy Bypass -File `
  .\scripts\install_scheduled_task.ps1 `
  -IntervalMinutes 60
```

脚本会优先使用项目中的 `.venv\Scripts\python.exe`，并创建名为“Admission Radar”的任务。任务会阻止多个实例同时运行；电脑当时关机或睡眠时会错过该次检查，恢复后由 Windows 尽快补跑。

手动立即触发一次：

```powershell
Start-ScheduledTask -TaskName "Admission Radar"
```

查看状态：

```powershell
Get-ScheduledTask -TaskName "Admission Radar" |
  Get-ScheduledTaskInfo
```

删除定时任务：

```powershell
powershell -ExecutionPolicy Bypass -File `
  .\scripts\uninstall_scheduled_task.ps1
```

更换项目目录后，请重新运行安装脚本，以便任务使用新路径。

## 配置说明

| 字段 | 含义 |
|---|---|
| `database_path` | SQLite 文件路径；相对路径以 `config.json` 所在目录为基准 |
| `log_path` | 日志文件路径 |
| `request.timeout_seconds` | 单次网页请求超时 |
| `request.retries` | 网络或服务器临时错误的重试次数 |
| `email.enabled` | 是否真正发送邮件 |
| `email.security` | `ssl`、`starttls` 或 `none` |
| `email.password_env` | 存放 SMTP 授权码的环境变量名 |
| `websites[].parser` | 当前中财页面必须使用 `cufe_master` |

## 日志与故障排查

日志位置默认为：

```text
logs\admission_radar.log
```

日志达到约 2 MB 后自动轮转，最多保留 5 个旧文件。

### 测试邮件认证失败

- 确认已开启 SMTP
- 确认填写的是授权码，不是邮箱登录密码
- 确认用户名是完整邮箱地址
- 确认 `smtp_host`、端口和加密方式与邮箱服务商要求一致
- 如果刚设置用户环境变量，请重新登录 Windows，确保计划任务能读取它

### 手动运行成功，计划任务失败

- 检查 `logs\admission_radar.log`
- 确认 `.venv` 没有被移动或删除
- 确认 `config.json` 仍在项目根目录
- 项目移动后重新安装计划任务

### 页面结构变化

程序在提取不到符合条件的公告时会报错并保留原历史，不会把空页面当作“全部公告被删除”。如中财官网改版，需要调整：

```text
admission_radar\fetcher.py
```

中的 `parse_cufe_master` 解析规则。

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖：

- 完整标题、链接和日期提取
- 导航链接过滤
- 页面结构异常时安全失败
- 首次建立基线
- 后续识别新公告
- 邮件成功后的通知状态

## 数据与隐私

- 公告历史只保存在本机 SQLite
- 邮箱授权码只从本地配置或环境变量读取
- 程序不会收集或上传用户信息
- 除访问学校官网和连接你的 SMTP 服务器外，不访问其他服务
