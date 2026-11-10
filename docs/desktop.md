# Windows桌面版

双击 `Zhangshi.exe` 打开独立桌面窗口。无需安装Python，不需要启动浏览器或本地网页服务。界面用本机WebView2绘制，业务请求通过进程内Python桥接直接调用分析服务，不监听HTTP端口。免费行情仍需要联网。

## 两种交付

- 安装版：`release/Zhangshi-Setup-0.6.0-x64.exe`。中文安装向导、桌面/开始菜单快捷方式、Windows卸载入口，默认按当前用户安装，无需管理员权限。
- 便携版：`dist/Zhangshi/Zhangshi.exe`。必须保留整个Zhangshi文件夹；不能只拷贝一个exe。`portable.flag` 表示使用旁边的data目录。

桌面版包含原项目的行情、历史缓存、策略版本、回测与复盘记录作为首次安装种子。安装时若用户数据目录已有active.json，则不会覆盖原数据。软件卸载保留用户数据。

## 数据位置

- 便携版：程序旁边的data文件夹。
- 安装版：`%LOCALAPPDATA%\Zhangshi\data`。
- 源码启动：继续使用项目data文件夹。

窗口底部「打开数据目录」可定位数据。行情、冻结输入、策略和复盘记录都在这里，定期备份整个目录。桌面窗口的自选与界面存储保存在data/desktop-profile；旧浏览器的localStorage自选不会自动迁移。

## 功能与运行

保留三个短线模式、板块雷达、强势股池、K线、策略设计、指定股票回测、数据源配置和抖音复盘。CSV/JSON/SVG/ZIP等导出通过Windows保存对话框；封面在软件内预览。重复启动会尝试回到已有窗口。关闭软件后停止后台调度；运行中任务退出前会询问是否中断。

BaoStock和AKShare随程序一起打包，用独立隐藏子进程执行数据请求，保留超时限制。未包含个人同花顺或券商凭据，iFinD仍需要另行安装官方SDK及权限。

支持Windows 10/11 x64。界面依赖微软WebView2 Runtime，本机已检测到该运行时；其他电脑缺少时请通过微软官方安装。当前项目未配置商业代码签名，Windows可能将安装包标记为未知发布者。

## 构建与检查

```powershell
python -m venv .desktop-env
.desktop-env\Scripts\python.exe -m pip install -r requirements-desktop.txt
.desktop-env\Scripts\python.exe scripts/build_desktop.py
.desktop-tools\InnoSetup\ISCC.exe installer.iss
```

桌面诊断不依赖浏览器访问：

```powershell
dist\Zhangshi\Zhangshi.exe --self-test docs\validation-desktop-exe.json
```

实现依据：[pywebview窗口与桥接](https://pywebview.flowrl.com/api/)、[pywebview Windows打包](https://pywebview.flowrl.com/guide/freezing.html)、[PyInstaller打包参数](https://pyinstaller.org/en/stable/usage.html)。
