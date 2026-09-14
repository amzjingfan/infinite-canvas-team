# 团队安装与更新

公开仓库：https://github.com/amzjingfan/infinite-canvas-team

本项目基于 hero8152/Infinite-Canvas，保留原作者署名和原 LICENSE。仅用于公司内部协作，不修改原许可条款。

## 同事首次安装

无需邀请、GitHub 账号或 GitHub CLI，直接下载公开 Release 即可。公开内容不包含平台密钥和个人资料。

在 Releases 下载 `infinite-canvas-windows-x64.zip` 和校验文件，解压到固定的新目录，然后让 Codex 执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\deploy\install.ps1
```

包内有 Python 运行环境和公共图片风格库，不包含任何平台密钥、画布、素材、对话或 Codex 登录。已有独立平台密钥文档可通过受控方式交给同事放到根目录 PRIVATE-API-KEYS.txt 后由安装脚本导入；不要上传到 GitHub。不建议把新安装包解压覆盖旧目录。

Codex Agent、即梦和 ComfyUI 仍需本机各自的登录/服务。网页启动成功不等于外部工具均已可用。

## 已安装旧打包版：只做一次过渡升级

**不卸载、不把新安装包覆盖到旧目录。** 请阅读 [旧版过渡升级与 Codex 提示词](transition-upgrade.md)。脚本在原目录更新程序，保留原密钥，无需同事重新填写。

## 日常更新

首页的更新入口进入“团队版本更新”；也可直接打开 `/static/team-update.html`。

保存画布并完成生成/取回任务，点击“检查更新”，再点击“更新并重启”。2026.09.14.2 起直接匿名下载公开版本，无需 GitHub CLI 或登录。网络不通或访问频率受限会提示稍后重试，不会切换到原作者更新源。

只替换程序清单中的文件。API、data、assets、output、历史记录、机器配置均不在更新清单。已存在的 workflows、static/runninghub、static/system-prompts 文件保留本地版本；需要新版默认模板时由用户单独确认导入。不会删除没有出现在新版里的本地文件。

下载包校验、导入检查通过后，才停止同一个 Windows 计划任务。新版本启动检查失败时恢复旧程序。备份和状态保存在 data/team-update。依赖清单变化时本版明确拒绝自动更新，需要新的完整安装版；本版不执行数据结构迁移。

若电脑在更新过程中断电，页面可能显示 recovery_required 或长期 prepared/applying；请交给 Codex 检查对应 backup-*/journal.json 后恢复，不应手工重试生成。文件事务能处理正常写入失败和启动失败，不保证跨断电完全自动恢复。

## 维护者发布

修改 VERSION（格式如 2026.09.15.1），运行测试，再构建新的输出目录：

```powershell
.\python\python.exe -B .\tools\team-release\build.py --out .\dist\release-2026.09.15.1
```

加 `--full` 会同时构建首次安装包，需要本机已有运行时和风格库。`source` 是白名单源码，不含原仓库历史。首次初始化该目录为 Git 仓库；后续将审核过的源码变更同步到团队仓库的干净 checkout，提交后推送。不要把 data、API 或 PRIVATE 部署包加入提交。

发布命令：`gh release create v2026.09.15.1 <更新zip> <sha256文件> --repo amzjingfan/infinite-canvas-team --target <已验证提交SHA> --title "2026.09.15.1" --notes "更新说明"`。

发布资产名必须为 infinite-canvas-update.zip 及 infinite-canvas-update.zip.sha256；不要替换已发布版本的资产，修复用新版本号。GitHub Release 是同事更新源，不使用 git pull 覆盖同事安装目录。
