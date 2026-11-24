# 雁书 InkFerry 任务清单

- [x] 连接远程仓库，保留已有历史，核对参考项目的许可证
- [x] 原生 TXT 排版与 MOBI、EPUB 导出
- [x] Kindle 局域网配对、定向收件和下载状态
- [x] Windows、Android、iOS 共用发送界面与 Windows 启动入口
- [x] 回归验证、便携构建与使用说明
- [x] 调试重复投递、暂存上限、超长书名和相邻卷章
- [x] 自动发现打开收件页的 Kindle，支持一键配对
- [x] 在 Android 本机运行转换器和 Kindle 收件服务，验证离线转换与局域网收书
- [x] 构建并核验独立 Android APK，用模拟发送界面验证导入、导出与一键配对
- [x] 移除可能隐藏 Kindle 返回按钮的 MOBI 文档类型字段，测试并重建 Windows 和 Android 安装包
- [x] 在 Android 模拟器验证系统导入、保存和 Wi‑Fi 投递，修复长书名首次及重复保存失败
- [x] 将中文名称统一为雁书，核验发送页、收件页和两端安装包

每完成一项，更新此清单并将对应提交推送到 main。

## 开发依据

- [kaf-cli](https://github.com/ystyle/kaf-cli) 使用木兰宽松许可证第 1 版，参考中文 TXT 分章和排版流程。
- [kaf-wifi](https://github.com/ystyle/kaf-wifi) 未找到公开许可证，仅参考局域网浏览器收书方式。
- [send2ereader](https://github.com/daniel-j/send2ereader) 使用 MIT 许可证，参考短码配对方式。

雁书独立实现排版、文件格式和传输。Kindle 使用本机浏览器收书，保留 Amazon.cn 账号即可。
