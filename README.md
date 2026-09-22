# GDUFE Campus Balance

广东财经大学校园余额助手。Windows 原生窗口，在自己的电脑上查询宿舍电量、自来水及力王热水余额，余额不足时弹窗提醒，并可选发送 QQ 邮箱告警。

本项目由个人维护，与学校及校园卡平台无隶属关系。

## 下载与使用

前往 [Releases](https://github.com/cd233ljx/gdufe-campus-balance/releases/latest)，下载 `gdufe-campus-balance-v<版本号>-windows-x64-setup.exe`，双击打开中文安装向导。仅安装给当前 Windows 用户，默认无需管理员权限；可选择安装路径，**桌面快捷方式和开机自启默认勾选**，安装完成后可立即启动。已包含 Python 运行环境，无需另外安装 Python。

另提供 `gdufe-campus-balance-v<版本号>-windows-x64.zip` 便携版：完整解压后运行，不要单独移动 EXE，旁边的 `_internal` 是运行必需文件。安装版和便携版使用不同数据目录，请只运行其中一个。

1. 首次使用先显示三步引导：登录选房、QQ 邮箱告警、历史查询与托盘常驻。可点「下一步」逐步查看，也可跳过；之后可从主窗口底部「使用指南」重新打开。安装版沿用安装时的自启选择，不会再次询问；之后可在「设置」中随时开关。
2. 选择需要关注的项目，点击「登录学校账号」，在独立的 Edge / Chrome 窗口完成学校认证。
3. 登录成功后自动返回软件，优先选中学校已绑定的房间或力王手机号。核对后点击「确认房间并开始监控」。没有有效绑定时，可直接在软件中选择校区、楼栋和房间。
4. 以后打开会沿用本地登录，刷新余额并恢复监控。只有凭证失效时需要重新登录。

关闭窗口后程序留在托盘继续监控；再次双击 EXE 可打开窗口。点击「关闭程序」会先弹出二次确认，说明将关闭后台、停止自动查询和告警；确认后才停止，取消则继续监控。「设置」中可以更换房间、调整提醒阈值、绑定或修改 QQ 邮箱，阈值留空表示不提醒。

常驻期间每 30 分钟查询一次，手动刷新后重新计时：电量低于 20 度、自来水低于 1 元提醒；力王热水默认不提醒，开启提醒后按现金余额判断。电量单位为度，水费单位为元。同一低额周期只提醒一次。

主窗口默认以 1000×900 打开，小屏会按可用区域自动缩小。首次在鼠标所在屏幕居中，之后记住调整后的位置和大小；弹窗跟随主窗口居中。主窗口底部按钮固定可见，放不下的内容可滚动查看。

## QQ 邮箱告警

首次设置页面可点击「可选：绑定 QQ 邮箱告警」，也可以跳过，之后从「设置 → QQ 邮箱告警」进入。只需填写自己的 `@qq.com` 邮箱和 SMTP 授权码，程序给同一邮箱发信。QQ 邮箱网页版的设置中找到 POP3/IMAP/SMTP 服务，开启 SMTP 并生成授权码；授权码不是 QQ 登录密码。

点击「发送测试邮件并保存绑定」，发送成功后才保存设置，并提示检查收件箱或垃圾箱。修改失败会保留原绑定。邮箱地址不变时，授权码留空可沿用已保存的授权码；也可关闭告警并删除授权码。首次低于阈值发一次，持续低余额不重复发送；恢复后再次低于阈值重新提醒。发送失败每分钟重试，余额恢复后取消未发送的低额提醒。邮件是否最终进入收件箱仍取决于邮箱服务。

## 查询历史与日期范围

主界面「查询历史」显示查询时间、来源、项目、房间、成功或失败状态、余额及较上次同房间成功查询的变化。默认查询近 7 天，可点击日历选择开始和结束日期（包含起止两天），并提供「今天」「近 7 天」「近 30 天」快捷范围。结果每页 100 次，可翻页查看全部匹配记录，无需手动输入日期格式；新版本启用后开始积累，升级前和关机期间没有记录。历史按日加密保存在 `data` 中，更换房间不会删除旧记录。变化包含充值、扣费等结果，不等同于学校消费明细；失败记录不会将旧余额冒充本次查询结果。

邮件使用 QQ SMTP 的加密连接（`smtp.qq.com:465`），参见 [腾讯邮件连接器说明](https://main.qcloudimg.com/raw/document/product/pdf/1270_46586_cn.pdf)；发送实现使用 [Python SMTP_SSL](https://docs.python.org/3/library/smtplib.html)。

## 升级到新版

**安装版**：在程序中点击「关闭程序」并确认，然后运行新版安装器。向导自动沿用安装路径，覆盖更新程序，保留用户数据，并沿用当前自启状态；无需复制数据。运行中的窗口或后台监控会阻止安装和卸载，请按提示退出后重试。本版不包含在线自动更新，需手动下载新版安装器。

**便携版**：先在旧版设置中关闭开机自启，再关闭程序并确认；完整解压新版，把旧程序旁的整个 `data` 文件夹复制到新版 EXE 旁边。打开新版后，可重新开启自启。

请在同一 Windows 用户下迁移数据；保留旧目录作为备份。不要只替换 EXE，也不要把个人 `data` 文件夹上传到仓库或发给他人。

## 运行条件

- Windows 10/11 x64；已在 Windows 11 验证，Windows 10 尚未实测。
- 已安装 Edge 或 Chrome，能正常访问学校认证及校园卡平台。
- 电脑开机、联网且程序运行时才能查询。关机或睡眠期间无法监控；恢复运行时若距上次查询已超过 30 分钟会补查一次，不补造睡眠期间的记录。
- 可以开启开机自启：登录当前 Windows 用户后在托盘中运行并恢复监控，手动打开快捷方式会显示窗口。未开启时，重启电脑后需手动打开软件。便携版移动目录后，请在新位置的「设置」中重新开启自启以更新路径。
- Release 尚未做代码签名；在完全未安装 Python 的干净系统上仍待验证。

## 登录与本地数据

密码和验证码只在学校页面输入，程序不保存。浏览器使用独立临时会话，不读取日常浏览器资料。取得的校园卡凭证、房间设置、余额、查询历史及邮箱授权码使用 Windows DPAPI 加密。**安装版**保存在 `%LOCALAPPDATA%\GDUFE Campus Balance\data`，与程序安装路径分离；**便携版**保存在 EXE 旁的 `data`，源码运行时位于仓库根目录，与启动工作目录无关。加密与当前 Windows 用户绑定。数据不会打包进分发文件。

**卸载安装版**：先关闭程序，再从 Windows「设置 → 应用 → 已安装的应用」卸载 GDUFE Campus Balance。卸载会移除程序、快捷方式和自启项；默认保留数据，重新安装可继续使用。卸载时选择清除数据会永久删除安装版登录、邮箱授权码、设置和历史。静默卸载始终保留数据。

**卸载便携版**：关闭自启和程序后，删除解压目录即可。请勿在 Issue 中上传 HAR、Cookie、Token、手机号或包含个人房间信息的截图。

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
.\windows\build-installer.cmd
.\.venv-windows\Scripts\python.exe windows\release.py
```

`build.cmd` 生成便携版到 `dist/gdufe-campus-balance`。`build-installer.cmd` 使用固定版本的 PyInstaller，独立构建到 `dist/installer-stage`，不覆盖正在使用的便携版目录；需要 [Inno Setup 6.7.3](https://jrsoftware.org/isdl.php)，可用 `ISCC` 环境变量指定编译器路径。生成的安装器及 SHA256 校验文件在 `dist/release`。中文翻译来自 Inno Setup 官方仓库的 `Files/Languages/ChineseSimplified.isl`，保留文件内的贡献者信息。

`release.py` 在工作区干净、应用和安装器均匹配当前提交时生成便携 ZIP、源码 ZIP 及包含安装器的 `SHA256SUMS.txt`。开发构建可用于本地验收，但不会通过正式发布检查。发布文件不进入 Git，源码包从 Git 跟踪文件生成。

原生界面回归测试会打开测试窗口，使用临时目录及模拟账户，不访问学校接口：

```powershell
Start-Process .\dist\gdufe-campus-balance\gdufe-campus-balance.exe -ArgumentList '--gui-self-test build/gui-report.json' -Wait
Get-Content build/gui-report.json
```

自动化测试覆盖阈值、邮件提醒去重与失败重试、凭证保存、日期范围及超过 1000 条记录的分页、绑定选择、登录窗口关闭、迁移及跨启动环境共享数据。原生界面回归覆盖使用引导、邮箱入口、日历范围和托盘恢复。浏览器测试使用真实 Edge 驱动和拦截后的模拟校园页面。已有本机实际登录、查询、绑定读取及重启复用验证；未验证所有校区房间与长时间休眠恢复。

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
