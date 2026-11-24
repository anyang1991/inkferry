# 雁书 InkFerry

把中文 TXT 排成电子书，或导入现有的 MOBI 和 EPUB。Windows 和 Android 各自运行转换器及传书服务。文件在自己的 Wi‑Fi 内传输，无需登录亚马逊账号。

## 启动

- 解压 Windows 便携版后双击 **InkFerry.exe**。从源码运行时，双击 **start.cmd**，需要 Python 3.10 或更新版。
- Windows 发送页会自动在浏览器打开。收书时保持雁书运行。
- 64 位 ARM 手机（Android 7 或更新版本）安装 `InkFerry-Android-arm64-v8a.apk` 后直接打开雁书。转换和传书都在手机上完成，断网时也可以转换并保存。Wi‑Fi 发送时让手机与 Kindle 连同一网络，并保持 Android 应用打开。
- 在目标 Kindle 自带的浏览器打开发送界面显示的「Kindle 收件地址」。从 Android 发送时，这个地址属于手机。

## 传书

1. 在 Kindle 打开收件页，保持页面打开。
2. 等几秒，电脑或手机发送页会自动列出这台收件设备。找到同名设备，点「配对」。后续发送可直接选择已配对的设备。
3. 选择文件和收件设备。TXT 可预览排版并转为 MOBI，格式检查通过的 MOBI 会原样发送。EPUB 发给 Kindle 时点「转为 MOBI 并发送」，发给能下载 EPUB 的其他浏览器时传原文件。
4. 在 Kindle 收件箱点「下载」，再到设备图书馆打开。收件箱支持自动刷新，也可手动点击「刷新收件箱」。

可以在 Kindle 上收藏收件页。路由器如果给发送设备换了局域网 IP，用新地址打开并重新配对。

## 排版与导出

- 支持 UTF‑8、UTF‑16、GB18030 和 Big5。自动识别有误时，可以手动选编码后重新导入。
- 自动识别常见中文章、卷、回以及英文 Chapter。书名、作者可改，两套版式都带标题页和可跳转目录。
- MOBI 用于 Kindle 本机阅读及 Wi‑Fi 收书。EPUB 适合归档或在其他阅读器上使用。两个格式都由程序直接生成。
- 导入的 EPUB 与 MOBI 先检查文件结构，单个文件限 20 MB。EPUB 转 MOBI 适合无 DRM 的流式文字书，保留文字和章节，省略图片和原版排版。固定版式与纯图片书可保留原 EPUB。
- 可以切换「单章合并」保留正文顺序。页面展示排版样张，实际字体和间距也受 Kindle 阅读设置影响。

## 连不上时

- 确认发送设备和 Kindle 在同一家庭网络。访客 Wi‑Fi 可能隔离设备。可用同一网络上的另一台设备打开 Kindle 收件地址，确认局域网可达。
- 如果 Windows 防火墙询问，允许程序在家里的专用网络上通信。使用源码版时，系统可能显示 Python 为程序名。
- 在可信家庭网络使用局域网 HTTP。手机发送链接含访问密钥。设备列表只显示刚打开或刷新过收件页的设备，配对后只有对应的浏览器能下载。
- 待收书籍在此次启动的发送端内暂存，保留最多 8 小时。空间不足时，优先清理已收取或未发送的转换文件。Windows 和 Android 分别保存自己的 Kindle 配对记录。
- 「已下载」表示文件已从主机传给 Kindle 浏览器。入库结果在 Kindle 上查看。Wi‑Fi 传书写入这一台设备，亚马逊云端书库与其他设备不会同步。
- 如果旧版生成的 MOBI 在阅读工具栏里缺少返回图书馆的按钮，用新版重新转换后再导入。新版 MOBI 省略了部分固件处理异常的文档类型字段。

PowerShell 可以用 `python server.py` 启动源码。端口占用时可用 `python server.py --port 8878`。仅在 Windows 本机转换时可加 `--local-only`。

## 测试与构建

在项目目录运行 `python -m unittest discover -s tests -v`，可验证文件格式、中文编码、目录定位和目标设备隔离。

只有从源码打包 Windows 便携版时才需要这一步。在项目目录打开 PowerShell，运行下面两条命令。

```powershell
python -m pip install PyInstaller
./build.ps1
```

生成的 `dist/InkFerry-Windows.zip` 包含可执行文件、使用说明和 Python 许可证。

Android 构建需要 JDK 17 或更新版、Python 3.13、Android SDK 35 和 Build Tools 35.0.0。在 Windows 上运行 `./build-android.ps1`，在 `dist` 生成调试签名的 ARM64 手机和 x86‑64 模拟器 APK。脚本会在临时目录构建，支持原项目路径包含中文。

## 参考

[KAF CLI](https://github.com/ystyle/kaf-cli) 的中文 TXT 工作流、[KAF WiFi](https://github.com/ystyle/kaf-wifi) 的 Kindle 浏览器取书方式和 [send2ereader](https://github.com/daniel-j/send2ereader) 的短码配对提供了设计参考。雁书独立实现这些功能，MOBI 容器参考了 [MobileRead 格式文档](https://wiki.mobileread.com/wiki/MOBI)。
