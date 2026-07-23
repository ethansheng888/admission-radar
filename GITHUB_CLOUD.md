# GitHub Actions 云端部署

部署完成后，GitHub 会每小时第 17 分钟在云端检查一次中财硕士招生公告。你的电脑关机、休眠或者断网都不影响云端任务。

## 为什么选择公开仓库

GitHub 的标准云端运行器对公开仓库免费。私有仓库也有免费分钟额度，但每次运行的分钟数会计入额度。

本项目不会把邮箱地址或授权码写进代码：

- 邮箱地址保存在 GitHub Secret `SMTP_USERNAME`
- SMTP 授权码保存在 GitHub Secret `SMTP_PASSWORD`
- 朋友的收件地址保存在 GitHub Secret `BJTU_RECIPIENT`
- 仓库中保存的 SQLite 只包含学校公开公告标题、链接和通知状态

因此，为了确保接近零成本，建议使用公开仓库。

## 第一步：创建 GitHub 仓库

1. 登录 <https://github.com/>
2. 右上角点击 `+`
3. 选择 `New repository`
4. Repository name 填写：

   ```text
   admission-radar
   ```

5. 选择 `Public`
6. 不要额外创建 README、`.gitignore` 或 License
7. 点击 `Create repository`

## 第二步：上传项目

在新仓库页面点击：

```text
uploading an existing file
```

把解压后的项目内容上传到仓库根目录。上传后仓库首页应直接看到：

```text
.github
admission_radar
scripts
state
tests
config.cloud.json
main.py
requirements.txt
```

不要让文件多套一层目录。错误结构示例：

```text
admission-radar/admission-radar/main.py
```

正确结构：

```text
admission-radar/main.py
```

确认后点击 `Commit changes`。

## 第三步：添加邮箱 Secrets

进入仓库：

```text
Settings
→ Secrets and variables
→ Actions
→ Secrets
→ New repository secret
```

添加三个 Secret。

### Secret 1

名称：

```text
SMTP_USERNAME
```

值：

```text
你的完整QQ邮箱地址，例如 123456789@qq.com
```

### Secret 2

名称：

```text
SMTP_PASSWORD
```

值：

```text
你的QQ邮箱SMTP授权码
```

这里仍然是 SMTP 授权码，不是 QQ 登录密码。

Secret 保存后不能再次查看明文，这是正常现象。需要修改时重新保存一个同名 Secret。

### Secret 3

名称：

```text
BJTU_RECIPIENT
```

值：

```text
接收北京交通大学公告的朋友邮箱
```

程序会按学校分发：

- 中央财经大学公告发送到 `SMTP_USERNAME`
- 北京交通大学公告发送到 `BJTU_RECIPIENT`

如果北交公告需要同时发送给多人，可以在一个 Secret 中用英文逗号分隔：

```text
friend1@example.com,friend2@example.com
```

## 第四步：允许工作流保存公告状态

进入：

```text
Settings
→ Actions
→ General
→ Workflow permissions
```

选择：

```text
Read and write permissions
```

点击 `Save`。

这个权限只用于把 `state/radar.db` 的新公告记录提交回当前仓库。工作流文件本身也明确只申请了 `contents: write`。

## 第五步：先测试邮件

进入仓库的 `Actions` 页面。

1. 左侧选择 `Admission Radar`
2. 点击 `Run workflow`
3. 勾选：

   ```text
   只测试邮件，不抓取和保存公告
   ```

4. 在“测试邮件发给谁”中选择：

   - `owner`：发送给仓库主人
   - `bjtu-friend`：发送给接收北交公告的朋友

5. 点击绿色的 `Run workflow`
6. 等待任务完成

绿色对勾表示成功。此时 QQ 邮箱应该收到“邮件配置测试”。

如果失败，点进失败任务，再展开红色步骤查看错误。

## 第六步：手动建立首次基线

再次点击 `Run workflow`，这次不要勾选测试邮件。

第一次正式运行会：

1. 抓取中财和北交当前公告
2. 建立 `state/radar.db`
3. 不发送旧公告邮件
4. 自动提交一条：

   ```text
   chore: update admission radar state
   ```

看到这条自动提交，表示云端状态保存成功。

之后检测到新公告时才会发送邮件，并把新状态提交回仓库。

## 自动运行时间

工作流配置为：

```yaml
cron: "17 * * * *"
timezone: "Asia/Shanghai"
```

即北京时间每小时第 17 分钟运行一次。选择第 17 分钟是为了避开 GitHub Actions 整点高峰。

计划任务不是严格实时服务。GitHub 负载较高时可能延迟，极端情况下也可能跳过一次；下一小时仍会继续检查。

## 公开仓库的 60 天规则

GitHub 可能停用连续 60 天没有仓库活动的公开仓库定时任务。本项目每 30 天更新一次：

```text
state/heartbeat.txt
```

因此即使很久没有新公告，也会产生一次轻量提交，保持工作流活跃。

## 修改检查频率

打开：

```text
.github/workflows/admission-radar.yml
```

当前每小时运行一次：

```yaml
- cron: "17 * * * *"
```

每两小时运行一次：

```yaml
- cron: "17 */2 * * *"
```

每天北京时间 08:17 和 20:17：

```yaml
- cron: "17 8,20 * * *"
```

不要设置得过于频繁。招生公告通常没有分钟级监控的必要。

## 常见错误

### `需要环境变量 SMTP_USERNAME`

没有创建 `SMTP_USERNAME` Secret，或者名称拼写错误。

Secret 名称必须完全是：

```text
SMTP_USERNAME
```

### `需要收件人环境变量 BJTU_RECIPIENT`

没有创建朋友的收件邮箱 Secret。进入仓库：

```text
Settings → Secrets and variables → Actions
```

添加：

```text
BJTU_RECIPIENT
```

### 邮箱认证失败

- 确认 `SMTP_PASSWORD` 是 SMTP 授权码
- 确认 QQ 邮箱已经开启 SMTP 服务
- 重新生成授权码后更新 Secret
- 如果 QQ 邮箱拒绝 GitHub 云端 IP，可更换支持 SMTP 的其他邮箱，并同步修改 `config.cloud.json`

### `403` 或无法推送状态

进入：

```text
Settings → Actions → General → Workflow permissions
```

确认选择了 `Read and write permissions`。

如果主分支启用了禁止机器人直接推送的分支保护，也需要为这个小仓库取消该限制。

### 没有出现定时任务

- 工作流必须位于默认分支
- 检查 `.github/workflows/admission-radar.yml` 是否上传到了正确位置
- 进入 Actions 页面确认工作流处于启用状态

## 停止云端监控

进入：

```text
Actions → Admission Radar
```

点击右上角菜单，选择：

```text
Disable workflow
```

重新启用时选择 `Enable workflow`。

## 官方参考

- [GitHub Actions 定时事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [GitHub Actions Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
- [GitHub Actions 计费与免费额度](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
