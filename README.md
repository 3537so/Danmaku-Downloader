# 弹幕下载器 — Danmaku Downloader

> 多平台 · 多源 · 多格式 — 一键解析，即贴即下

一个 Python 命令行工具，输入视频链接或番剧名称，自动解析并下载弹幕（XML / JSON / CSV）。

**支持平台**：暂无

---

## 快速开始

```bash
# 安装依赖
pip install requests

# 直接贴链接（自动识别平台）
python danmaku_dl.py "https://www.bilibili.com/video/BV1GJ411x7h7"

# 社区模式：按番剧名搜索弹幕
python danmaku_dl.py "鬼灭之刃" --source community

# 指定输出目录和格式
python danmaku_dl.py "https://v.qq.com/x/page/u30468anxzn.html" -o ./弹幕 -f xml
```

---

## 输出格式

| 格式 | 说明 |
|------|------|
| `.xml` | Danmaku Anywhere / 弹弹Play 兼容 |
| `.json` | 结构化数据，含弹幕源引用 |
| `.csv` | 表格形式，可用 Excel 打开分析 |

---

## 社区模式选片语法

搜索番剧后自动展示剧集列表，按编号选择：

```
ALL          → 下载全部
A            → 下载大标题 A 的全部集数
A2           → 下载大标题 A 的第 2 集
A-C-E        → 下载大标题 A、C、E 的全部集数
A1-A3-B-C2   → 自由组合，用 - 分隔
```

大标题用字母 A-Z（超 26 用 AA、AB...），集数从 1 开始。

---

## 交互模式

不带参数运行即可进入交互菜单：

```bash
python danmaku_dl.py
```

通过菜单选择弹幕来源和输出格式。

---

## 完整参数

```
python danmaku_dl.py "链接或番剧名" [参数]

  -o, --output       输出目录（默认: 当前目录）
  -f, --format       输出格式: xml json csv（默认: 全部）
  -s, --source       弹幕来源: auto / direct / community
  --select           社区模式选片: ALL / A / A2 / A-C-E
  --api-base          自定义社区 API 地址
  --no-multipart      多P视频只下载第1P
```

---

## 参考来源

- 油猴脚本 [#524107](https://greasyfork.org/zh-CN/scripts/524107) — B站/Tencent 弹幕接口
- 油猴脚本 [#565481](https://greasyfork.org/zh-CN/scripts/565481) — 通用弹幕聚合 & 社区 API
- [Danmu.Server](https://github.com/u2sb/Danmu.Server) — 弹幕聚合服务

---

## 作者

**3537so**

## 许可证

MIT License
