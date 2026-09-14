# 旧打包版过渡升级

不需要卸载。只需让同事电脑上的 Codex 做一次过渡升级，以后就在画布的“团队版本更新”页面更新。

保留原安装目录、浏览器地址和端口，不覆盖画布、素材、历史记录、平台密钥、本机配置、已有工作流和模板。脚本复用已经安装的 Python；不重新填写密钥，也不需要 GitHub 账号。

## 发给同事

先保存画布，等 Agent、图片、视频和待取回任务全部结束，再关闭画布页面。把下面整段发给本机 Codex：

```text
请把这台电脑已安装的 Infinite Canvas 旧打包版，原地过渡升级到我们的公开团队版。
仓库：https://github.com/amzjingfan/infinite-canvas-team

请直接完成安装检查、下载、升级和验证，不要让我手动填写平台密钥。
1. 从当前工作目录和 Windows 计划任务定位已有安装目录、启动任务和实际端口；不要创建第二套安装，不要卸载，不要 git pull 覆盖安装目录。
2. 确认画布已保存、所有 Agent/生成/取回任务已结束、画布浏览器页面已关闭。检查队列及保存的数据；不能确认空闲时停止并告诉我具体原因，不要清除任务标记来绕过检查。
3. 阅读仓库 docs/transition-upgrade.md。下载最新正式 Release 的 transition-upgrade.ps1 到安装目录以外的临时目录；如资产不可访问，停止，不要改用原作者更新源。
4. 用 powershell.exe -NoProfile -ExecutionPolicy Bypass -File <脚本绝对路径> -InstallDir <原安装目录> -Port <实际端口> -SavedAndIdle 执行。路径有空格必须正确加引号。
5. 保留 API、data、assets、output、history.json、global_config.json、PRIVATE-API-KEYS.txt、运行环境和已有工作流/模板。不打印、上传或替换任何密钥，不运行旧安装器，不提交本机资料到 GitHub。
6. 检查 data/team-update/status.json 必须是 completed；请求 /api/team-update/health 核对版本，确认原画布、图片和平台配置仍可读取，再打开 /static/team-update.html。验证不需要发起付费生图或视频任务。
7. 如果失败，读取状态和对应 backup-*/journal.json，检查旧版是否已恢复；不要重装或删除备份。成功后告知版本和以后更新入口。
```

## 脚本入口

[下载过渡升级脚本](https://github.com/amzjingfan/infinite-canvas-team/releases/latest/download/transition-upgrade.ps1)

示例（目录和端口应由 Codex 根据同事电脑实际安装识别）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Downloads\transition-upgrade.ps1" -InstallDir "D:\Apps\Infinite-Canvas" -Port 3000 -SavedAndIdle
```

脚本先下载并校验正式更新包、检查依赖和新程序能否导入，再停止对应计划任务、备份并替换程序。新版启动失败会尝试恢复旧程序，兼容旧版没有 VERSION 和更新健康接口的情况。运行中的旧版缺少完整维护锁，所以执行前必须保存并关闭画布、结束所有任务。

如果运行环境或依赖不匹配、端口属于其他程序、有未结束任务或已有中断更新，脚本会停止；不自动卸载或清理资料。自动回退不等于断电恢复保证。程序备份保存在原目录 data/team-update，下载缓存位置在脚本输出中。

## 以后怎么更新

保存工作、完成任务后，打开画布首页更新入口 → 检查更新 → 更新并重启。不用再次传安装包或运行过渡脚本。公开仓库只提供程序，个人作品和密钥不会同步到 GitHub。
