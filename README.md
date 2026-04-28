# AutoNcm2Mp3

> 配置好网易云下载路径，下载完成后自动把 `.ncm` 转成 `.mp3` / `.flac`，
> 轻量、绿色、开箱即用。

## 特性

- **零打扰自动转换**：监控网易云下载目录，新下载的 `.ncm` 一落地就自动解码。
- **开机自启**：勾一下复选框，下次开机自动后台运行（写入 HKCU\Run，无需管理员）。
- **保留无损**：默认按文件原始格式输出（MP3 输出 `.mp3`，FLAC 输出 `.flac`），
  也可勾选 *强制转 MP3* 用 ffmpeg 把无损统一转成 MP3。
- **保留元数据与封面**：解出的歌曲带歌名 / 歌手 / 专辑 / 封面，
  在播放器与随身听上正常显示。
- **现代深色界面**：圆角卡片 + 扁平按钮，不打扰但好看；关掉窗口默认最小化到托盘。
- **批量补转**：一键扫描下载目录里历史遗留的 NCM。
- **CLI 模式**：可以脚本化批量处理，无需图形界面。

## 快速开始（开发者）

```powershell
# 1. 创建虚拟环境（可选）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动 GUI
python run.py
```

第一次启动会自动尝试识别网易云的下载目录，如果不准确，
在窗口里点 **选择…** 改成正确的目录即可，配置会持久化保存到
`%APPDATA%\AutoNcm2Mp3\config.json`。

## 推荐用法

1. **设置下载目录** → 选择你的网易云"下载目录"。
2. **开启「启用自动监控」** → 程序会监听新下载的 `.ncm`，下载完立刻转换。
3. **开启「开机自启」** → 开机后程序静默驻留托盘，无需手动启动。
4. **开启「启动后最小化到托盘」** → 平时不弹窗口，只在托盘后台跑。

完成上面四步后，下载网易云音乐 → 自动出 mp3/flac，全程不需要再打开 AutoNcm2Mp3。

## 自启动如何实现 / 如何卸载

程序在你勾选「开机自启」时把启动项写到当前用户的注册表项：

```
HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
名称: AutoNcm2Mp3
值:   "<完整 exe 路径>" --minimized
```

- **不需要**管理员权限，只影响当前 Windows 用户。
- 取消勾选 → 程序会立刻把这条注册表项删除。
- 也可以手动执行 `regedit` 把上面这条键值删掉。
- 如果换了 exe 的位置，下次启动 GUI 时会自动用新路径覆盖（不会留下死链）。

## 命令行用法

```powershell
# 转换单个文件，输出到同目录
python run.py --cli "D:\CloudMusic\xxx.ncm"

# 批量扫描目录、输出到指定文件夹、保留原 NCM
python run.py --cli "D:\CloudMusic" -o "D:\Music" --keep

# 把 FLAC 也强制转成 320k MP3 (需要 ffmpeg 在 PATH 中)
python run.py --cli "D:\CloudMusic" --force-mp3 --bitrate 320k
```

## 打包成单文件 exe

仓库里附带 `build.bat`：

```powershell
pip install pyinstaller
.\build.bat
```

打包结果在 `dist\AutoNcm2Mp3.exe`，双击即可运行，无需安装 Python。

> 如果你打开了 *强制转 MP3* 选项，需要把 `ffmpeg.exe` 放到与 exe 同级的
> `ffmpeg\ffmpeg.exe`，或者把 ffmpeg 加入系统 PATH。

## 项目结构

```
AutoNcm2mp3/
├── auto_ncm/
│   ├── ncm_decoder.py   # NCM 解码核心 (AES + RC4)
│   ├── converter.py     # 写文件 + 标签 + ffmpeg 转码
│   ├── config.py        # 配置持久化
│   ├── watcher.py       # 监控下载目录
│   ├── autostart.py     # Windows 开机自启 (注册表)
│   ├── resources.py     # 资源路径解析 (兼容打包/开发)
│   ├── gui.py           # Tkinter 现代深色界面
│   ├── tray.py          # 系统托盘 (pystray)
│   └── main.py          # 主入口
├── run.py               # 开发启动脚本
├── requirements.txt
├── logo.png             # 程序图标 (打包时自动转成 .ico)
└── build.bat            # PyInstaller 打包脚本
```

## NCM 解码原理

参考: [网易云音乐 ncm 编解码探究记录](https://www.jianshu.com/p/ec5977ef383a)。

简单说，NCM 文件是把原始的 MP3/FLAC 通过 RC4 流加密后，
再在文件头里把 RC4 主密钥用 AES-ECB 二次加密塞进去，
所以解码流程是：

1. 读 magic 头 `CTENFDAM` 校验格式；
2. 取出 RC4 密钥块 → XOR 0x64 → AES-ECB → 去掉 `neteasecloudmusic` 前缀；
3. 用 RC4-KSA 生成 256 字节 keybox；
4. 取出元数据块 → XOR 0x63 → base64 → AES-ECB → JSON；
5. 跳过 CRC 与 5 字节 gap 后再读封面块；
6. 剩余字节按 `out[i] = in[i] ^ keybox[(i+1) & 0xFF]` 解密即得真实音频。

## 法律与免责声明

本工具仅供你转换 **自己合法获得的、有版权使用授权的** 音频文件，
请勿用于侵犯版权的传播。当你的网易云会员到期后，相关音频的播放权也随之失效。

## License

MIT
