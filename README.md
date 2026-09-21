# GDUFE Campus Balance

广东财经大学校园余额助手。Windows 原生窗口，在自己的电脑上查询宿舍电量、自来水及力王热水余额，余额不足时弹窗提醒。

本项目由个人维护，与学校及校园卡平台无隶属关系。

## 下载与使用

前往 [Releases](https://github.com/cd233ljx/gdufe-campus-balance/releases/latest)，下载 `gdufe-campus-balance-v0.1.2-windows-x64.zip`，**完整解压**后双击 `gdufe-campus-balance.exe`。不要单独移动 EXE，旁边的 `_internal` 是运行必需文件。

1. 选择需要关注的项目，点击「登录学校账号」，在独立的 Edge / Chrome 窗口完成学校认证。
2. 登录成功后自动返回软件，优先选中学校已绑定的房间或力王手机号。核对后点击「确认房间并开始监控」。没有有效绑定时，可直接在软件中选择校区、楼栋和房间。
3. 以后打开会沿用本地登录，刷新余额并恢复监控。只有凭证失效时需要重新登录。

关闭窗口后程序留在托盘继续监控；再次双击 EXE 可打开窗口。点击「退出程序」会停止监控。「设置」中可以更换房间、调整查询时间和提醒阈值，阈值留空表示不提醒。

默认北京时间每天 22:00 查询：电量低于 20 度、自来水低于 1 元提醒；力王热水默认不提醒，开启提醒后按现金余额判断。电量单位为度，水费单位为元。同一低额周期只提醒一次。

## 运行条件

- Windows 10/11 x64；已在 Windows 11 验证，Windows 10 尚未实测。
- 已安装 Edge 或 Chrome，能正常访问学校认证及校园卡平台。
- 电脑开机、联网且程序运行时才能查询。关机或睡眠期间无法监控；当天设定时间后恢复运行会补查，不补查过去多天。
- 当前不设置开机自动启动。重启电脑后请打开软件。
- Release 尚未做代码签名；在完全未安装 Python 的干净系统上仍待验证。

## 登录与本地数据

密码和验证码只在学校页面输入，程序不保存。浏览器使用独立临时会话，不读取日常浏览器资料。取得的校园卡凭证、房间设置与余额使用 Windows DPAPI 加密，保存在程序旁的 `data` 文件夹：源码运行时位于仓库根目录，打包版位于 EXE 旁边，与启动时所在目录无关。首次启动会自动迁移旧版用户目录中的有效登录和设置，原文件保留。加密仍与当前 Windows 用户绑定；更新程序时保留或复制整个 `data` 文件夹即可沿用登录。此文件夹已加入 Git 忽略规则，并从 Release 包中排除。

如需卸载，先退出程序，再删除解压目录；删除程序旁的 `data` 文件夹也会清除本地登录。请勿在 Issue 中上传 HAR、Cookie、Token、手机号或包含个人房间信息的截图。

## 从源码运行

需要 Windows、Python 3.12+ 和 Edge / Chrome；当前构建使用 Python 3.13。双击 `start-windows.cmd`，首次会创建项目内虚拟环境并安装锁定依赖，随后打开软件。

也可以在 PowerShell 中运行：

```powershell
py -3.13 -m venv .venv-windows
.\.venv-windows\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv-windows\Scripts\python.exe -m cardsclaim.desktop.app
```

## 测试与打包

```powershell
.\.venv-windows\Scripts\python.exe -m unittest discover -s tests -v
.\windows\build.cmd
.\.venv-windows\Scripts\python.exe windows\release.py
```

`build.cmd` 使用固定版本的 PyInstaller，生成 `dist/gdufe-campus-balance`；`release.py` 生成带版本号的 Windows 包、源码包及 `SHA256SUMS.txt`。发布文件在 `dist/release`，不进入 Git。源码包从 Git 跟踪文件生成，发布前应确保工作区干净。

原生界面回归测试会打开测试窗口，使用临时目录及模拟账户，不访问学校接口：

```powershell
Start-Process .\dist\gdufe-campus-balance\gdufe-campus-balance.exe -ArgumentList '--gui-self-test build/gui-report.json' -Wait
Get-Content build/gui-report.json
```

自动化测试覆盖阈值、提醒去重、凭证保存、绑定选择、登录窗口关闭、迁移及跨启动环境共享数据。浏览器测试使用真实 Edge 驱动和拦截后的模拟校园页面。已有本机实际登录、查询、绑定读取及重启复用验证；未验证所有校区房间与长时间休眠恢复。

## 仓库结构

```text
cardsclaim/          程序源码与离线诊断入口
  desktop/           Windows 窗口、选房、登录、监控及加密存储
tests/               自动化测试，仅使用合成数据
windows/             源码启动、构建、许可收集与发布脚本
requirements.lock   含哈希的运行依赖锁定文件
```

提交前请运行测试，只提交源码和必要文档。反馈问题时提供版本、Windows 版本、操作步骤及不含个人信息的错误提示。涉及凭证泄露等安全问题，请使用仓库 Security 页的私密漏洞报告（如已启用），勿公开敏感数据。

## 许可证

Copyright 2026 CardsClaim contributors. 项目采用 [Apache License 2.0](LICENSE)。第三方依赖保留各自许可证，打包版随附 `THIRD-PARTY-LICENSES`。
