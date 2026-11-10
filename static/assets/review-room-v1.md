# 收盘复盘摄影底图

底图：`review-room-v1.png`。2026-10-23 使用内置 ImageGen 生成，无需 API Key。原任务指定的 `E:\Other\codex\BIGA\output\douyin\assets\trading-room-style-reference.png` 不可访问，未使用原图。

生成提示词：

> Use case: photorealistic-natural. Project asset: reusable background photo for a Chinese A-share after-close daily review cover. Generate a premium photographic trading room with multiple abstract monitors clustered on the right, blue-orange dusk city skyline through a tall window, deep navy and near-black color palette with restrained warm gold light. Horizontal true 16:9 composition (1920x1080 or another exact 16:9 resolution). Left 55% mostly dark uncluttered negative space for software-rendered daily title. Detailed cinematic editorial photography, believable materials, subtle reflections, calm and serious. Absolutely NO text, numbers, letters, logos, stock codes, watermarks, QR codes or readable data anywhere, monitors show only abstract blurred chart-like light without legible values. No people. This is an illustrative scene, never real-time market evidence.

底图本身由图像工具决定尺寸。软件将它按比例裁切铺满 **1920×1080**，以确定性文字排版生成当天 SVG 封面，包含日期、最多两行标题和“示意画面，非实时行情”。页面可将 SVG 导出为相同尺寸的 PNG。每天复用底图，不自动调用付费图像 API。正文核验失败时不生成行情封面；底图缺失时只提供可复用提示词并注明未生成。
