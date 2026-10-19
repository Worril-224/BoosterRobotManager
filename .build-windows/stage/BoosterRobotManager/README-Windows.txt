Booster 机器人管理器（Windows 便携版）
====================================

【运行】
1. 把整个 BoosterRobotManager 文件夹解压到有写入权限的位置
   （例如桌面 / 文档；不要放在 C:\Program Files，否则无法保存配置与日志）。
2. 双击 BoosterRobotManager.exe 即可启动，无需安装 Python。
3. 首次启动请点击右上角「⚙ 密码设置」设置机器人默认密码。

【目录说明】
- python\                 内置的 Windows Python 运行时（自带依赖，请勿删除）
- brm\                    程序源码与配置
- brm\config\robots.json  机器人配置（密码 Base64 混淆存储，非加密）
- brm\logs\               部署日志
- BoosterRobotManager-Console.bat  控制台启动方式（排查问题时使用）

【常见问题】
1. Windows 提示"已保护你的电脑"/"未知发布者"：
   这是未签名程序的正常提示。点击"更多信息"→"仍要运行"，
   或右键 exe →"属性"→勾选"解除锁定"→确定。
2. 双击没反应或提示找不到 python：
   请确认整个文件夹（尤其是 python\ 目录）都解压出来了，
   不要只复制 exe 一个文件。
3. 启动后窗口出不来 / 缺少 DLL：
   请安装"Microsoft Visual C++ 2015-2022 运行库（x64）"后重试。
4. 想确认启动错误：
   双击 BoosterRobotManager-Console.bat，控制台会显示 Python 报错信息。

【安全说明】
- 配置里的密码仅为 Base64 混淆，不是加密；请只在可信任的 Windows 电脑上使用。
- 日志中的密码会显示为 ******。
