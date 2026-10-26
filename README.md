# 语音助手 · Qwen3-ASR-1.7B Q5

Windows 本地语音识别悬浮窗，使用 CapsWriter-Offline v2.6、Qwen3-ASR-1.7B Q5_K 和 Qwen3-ForcedAligner-0.6B。支持说话打字、音视频转写，以及独立 HTTP / Python 接口。

## 日常使用

双击 **打开语音助手.cmd**，等待“已就绪”。

- **说话打字**：点击输入位置，按住 CapsLock 说话，松开自动输入。
- **录音转文字**：切换标签，选择音视频文件，完成后复制或打开结果。
- 可收起、置顶、暂停服务；点击 × 退出。

## 在新电脑部署

需要 Windows 10/11、Python 3.10 以上。当前配置采用 DirectML + Vulkan GPU 加速，已在 RTX 4060 Laptop 上验证。

在仓库根目录依次运行：

```powershell
powershell -ExecutionPolicy Bypass -File tools/Install-Runtime.ps1
powershell -ExecutionPolicy Bypass -File desktop/build.ps1
```

第一步从官方地址下载引擎、两个模型和 FFmpeg，验证 SHA256 后解压。第二步自动建立 Python 构建环境并生成 `SpeechDesk/SpeechDesk.exe`。随后双击“打开语音助手.cmd”。需要代理时可为安装脚本传入 `-Proxy http://127.0.0.1:7897`。

已经安装好的本机无需重复下载或构建。首次部署需联网，运行识别无需云端识别 API。

## 接入其他系统

- HTTP：`http://127.0.0.1:8765`，支持同步转写、异步任务、取消和 TXT / JSON / SRT 导出。
- Python：可安装 `desktop` 包，使用 `ASRClient` 调用。
- 只运行接口时，双击“启动接口服务.cmd”。

详见 [接口接入说明](desktop/接口接入说明.md) 和 [HTTP 示例](desktop/examples/http_client.py)。

## 仓库内容

`desktop/src/local_asr` 包含识别模块、服务、HTTP 接口、麦克风、Windows 输入和悬浮窗；`configs` 保存引擎配置；`tools` 保存运行环境安装和停止脚本。

仓库只保存源码、配置和说明。模型、第三方程序、构建产物、API 令牌、录音、识别结果和本地日志不提交到 Git。完整运行目录需同时保留 `SpeechDesk`、`CapsWriter-Offline` 和 `desktop/data`，令牌与任务数据由应用自动生成。

## 第三方来源

- [CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline)，MIT 许可证，见 [许可证副本](third_party/CapsWriter-LICENSE.txt)。
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)，模型从 CapsWriter 官方模型发布页获取。
- [FFmpeg](https://ffmpeg.org/)，Windows essentials 构建来自 Gyan，见 [许可证副本](third_party/FFmpeg-LICENSE.txt)。

上述组件保持各自的许可证和归属，本仓库不重新发布它们的二进制与模型权重。
