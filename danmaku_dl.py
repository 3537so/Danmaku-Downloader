#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║              弹幕下载器 - Danmaku Downloader                 ║
║    多平台 · 多源 · 多格式 — 一键解析，即贴即下               ║
║                      作者: 3537so                           ║
╚══════════════════════════════════════════════════════════════╝

用法:
    python danmaku_dl.py "视频链接"
    python danmaku_dl.py "视频链接" -o ./output
    python danmaku_dl.py "视频链接" -f xml json csv
    python danmaku_dl.py "番剧名" --source community
    python danmaku_dl.py "番剧名" --source community --select ALL

弹幕来源:
    ✅ direct    → 直接从平台抓取弹幕（B站/腾讯视频）
       - B站：BV/AV号视频、番剧、多P、短链接
       - 腾讯视频：v.qq.com 页面视频（分段弹幕接口）
    ✅ community → 通过社区弹幕聚合 API 搜索获取
       - 覆盖 B站/腾讯视频/爱奇艺/优酷/巴哈姆特 等多个平台
       - 需部署或使用 Danmu.Server 兼容服务
       - GitHub: https://github.com/u2sb/Danmu.Server

社区模式选片语法:
    ALL       → 下载全部
    A         → 下载大标题 A 的全部集数
    A2        → 下载大标题 A 的第 2 集
    A-C-E     → 下载大标题 A、C、E 的全部集数
    以上可自由组合写入，如 A1-A3-B-C2-ALL（ALL 优先）

输出格式:
    ✅ .xml   - Danmaku Anywhere / 弹弹Play 兼容格式
    ✅ .json  - 结构化数据，含弹幕源引用信息
    ✅ .csv   - 表格形式，可用 Excel 打开分析

依赖: pip install requests
"""

import argparse
import json
import csv
import os
import re
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse, parse_qs
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:
    print("❌ 缺少依赖库 requests，请先安装：")
    print("   pip install requests")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════════════

@dataclass
class Danmaku:
    """单条弹幕数据"""
    time: float       # 视频中出现时间（秒）
    mode: int         # 弹幕模式: 1=滚动 4=底部 5=顶部 6=逆向 7=高级 8=代码
    font_size: int    # 字号
    color: int        # 颜色（十进制 RGB）
    timestamp: int    # 发送时间戳（Unix）
    pool: int         # 弹幕池: 0=普通 1=字幕 2=特殊
    author: str       # 发送者标识
    danmaku_id: int   # 弹幕 ID
    text: str         # 弹幕文本内容

    MODE_NAMES = {
        1: "滚动", 4: "底部", 5: "顶部",
        6: "逆向", 7: "高级", 8: "代码"
    }

    def to_xml_d_attr(self) -> str:
        return (f"{self.time},{self.mode},{self.font_size},{self.color},"
                f"{self.timestamp},{self.pool},{self.author},{self.danmaku_id}")

    @property
    def color_hex(self) -> str:
        return f"#{self.color:06X}"

    @property
    def time_str(self) -> str:
        m, s = divmod(int(self.time), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

    @property
    def mode_name(self) -> str:
        return self.MODE_NAMES.get(self.mode, f"未知({self.mode})")


@dataclass
class VideoInfo:
    """视频信息"""
    platform: str         # 平台标识 (bilibili/tencent/community)
    title: str            # 视频标题
    video_id: str         # 视频 ID
    cid: int = 0          # 弹幕 cid
    source: str = ""      # 弹幕来源说明
    source_url: str = ""  # 弹幕来源 API / 原始链接
    part_index: int = 1
    part_name: str = ""
    duration: int = 0
    danmakus: List[Danmaku] = field(default_factory=list)

    @property
    def safe_title(self) -> str:
        safe = re.sub(r'[\\/:*?"<>|]', '_', self.title)
        return safe[:80]


# ══════════════════════════════════════════════════════════════
# 平台处理器基类
# ══════════════════════════════════════════════════════════════

class PlatformHandler(ABC):
    """平台处理器基类"""

    def __init__(self):
        self._session = requests.Session()
        self._session.trust_env = False

    @property
    @abstractmethod
    def platform_name(self) -> str: ...

    @abstractmethod
    def can_handle(self, url: str) -> bool: ...

    @abstractmethod
    def fetch_danmaku(self, url: str) -> List[VideoInfo]: ...

    def _make_headers(self, referer: str = "https://www.bilibili.com/") -> dict:
        return {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/125.0.0.0 Safari/537.36"),
            "Referer": referer,
            "Accept": "application/json, text/plain, */*",
        }


# ══════════════════════════════════════════════════════════════
# 1. B站 (Bilibili) — 直接抓取
# ══════════════════════════════════════════════════════════════

class BilibiliHandler(PlatformHandler):
    """B站弹幕处理器

    支持的 URL:
        - https://www.bilibili.com/video/BV1xx411c7mD
        - https://www.bilibili.com/video/av170001
        - https://b23.tv/xxxxx (短链接)
        - https://www.bilibili.com/bangumi/play/ss12345 (番剧)
        - https://www.bilibili.com/bangumi/play/ep123456 (剧集)

    弹幕源: B站官方 XML 弹幕接口
    """

    VIEW_API = "https://api.bilibili.com/x/web-interface/view"
    DM_API = "https://api.bilibili.com/x/v1/dm/list.so"
    BANGUMI_API = "https://api.bilibili.com/pgc/view/web/season"

    @property
    def platform_name(self) -> str:
        return "bilibili"

    def can_handle(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return "bilibili.com" in host or "b23.tv" in host

    def fetch_danmaku(self, url: str) -> List[VideoInfo]:
        print(f"\n📡 B站直连 | {url}")

        if "b23.tv" in url:
            url = self._resolve_short(url)

        parsed = urlparse(url)
        if "/bangumi/play/" in parsed.path:
            return self._fetch_bangumi(parsed)

        bvid, aid = self._extract_id(url)
        if not bvid and not aid:
            raise ValueError(f"无法提取 BV/AV 号: {url}")

        params = {"bvid": bvid} if bvid else {"aid": aid}
        resp = self._session.get(self.VIEW_API, params=params,
                                  headers=self._make_headers(), timeout=30)
        data = self._check_api(resp)["data"]

        title = data.get("title", "未知标题")
        pages = data.get("pages", [])
        if not pages:
            pages = [{"cid": data.get("cid", 0), "page": 1,
                       "part": title, "duration": data.get("duration", 0)}]

        print(f"   🎬 {title}" + (f" | {len(pages)}P" if len(pages) > 1 else ""))

        results = []
        for i, page in enumerate(pages):
            cid = page.get("cid", 0)
            part_name = page.get("part", "")
            pn = page.get("page", i + 1)

            if len(pages) > 1:
                print(f"   📥 P{pn}: {part_name} (cid={cid}) ...")
            else:
                print(f"   📥 下载弹幕 (cid={cid}) ...")

            danmakus = self._fetch_cid_danmaku(cid)

            vi = VideoInfo(
                platform="bilibili", title=title,
                video_id=bvid or str(aid), cid=cid,
                source=f"B站官方弹幕接口 (api.bilibili.com/x/v1/dm/list.so?oid={cid})",
                source_url=url,
                part_index=pn,
                part_name=part_name if len(pages) > 1 else "",
                duration=page.get("duration", 0),
                danmakus=danmakus,
            )
            results.append(vi)

        total = sum(len(r.danmakus) for r in results)
        print(f"   ✅ {total} 条弹幕")
        return results

    # ── 内部方法 ──

    def _resolve_short(self, url: str) -> str:
        print("   🔗 解析 b23.tv ...")
        r = self._session.get(url, headers=self._make_headers(),
                               allow_redirects=False, timeout=15)
        if r.status_code in (301, 302, 303, 307, 308):
            return r.headers.get("Location", url)
        r2 = self._session.get(url, headers=self._make_headers(), timeout=15)
        return r2.url

    def _extract_id(self, url: str):
        bv = re.search(r'BV([a-zA-Z0-9]{10})', url)
        av = re.search(r'av(\d+)', url, re.IGNORECASE)
        return ("BV" + bv.group(1)) if bv else None, int(av.group(1)) if av else None

    def _fetch_bangumi(self, parsed) -> List[VideoInfo]:
        ep_id = re.search(r'/ep(\d+)', parsed.path)
        ss_id = re.search(r'/ss(\d+)', parsed.path)
        params = {}
        if ep_id: params["ep_id"] = int(ep_id.group(1))
        if ss_id: params["season_id"] = int(ss_id.group(1))

        resp = self._session.get(self.BANGUMI_API, params=params,
                                  headers=self._make_headers(), timeout=30)
        data = self._check_api(resp).get("result", {})
        title = data.get("title", data.get("season_title", "未知番剧"))
        episodes = data.get("episodes", [])

        print(f"   📺 {title} | {len(episodes)} 集")
        results = []
        for ep in episodes:
            ecid = ep.get("cid", 0)
            ename = ep.get("long_title") or ep.get("share_copy", f"第{ep.get('title','?')}集")
            print(f"   📥 {ename} (cid={ecid}) ...")
            dm = self._fetch_cid_danmaku(ecid)
            results.append(VideoInfo(
                platform="bilibili", title=title,
                video_id=f"ep{ep.get('id','?')}", cid=ecid,
                source=f"B站番剧弹幕接口 (番剧 ep_id={ep.get('id')})",
                source_url=parsed.geturl(),
                part_index=ep.get("title", 1), part_name=ename,
                duration=ep.get("duration", 0) // 1000, danmakus=dm,
            ))
        return results

    def _fetch_cid_danmaku(self, cid: int) -> List[Danmaku]:
        resp = self._session.get(self.DM_API, params={"oid": cid},
                                  headers=self._make_headers(), timeout=60)
        resp.encoding = 'utf-8'
        if resp.status_code != 200:
            return []
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return []

        danmakus = []
        for d in root.findall('d'):
            parts = (d.get('p', '')).split(',')
            if len(parts) >= 8:
                try:
                    danmakus.append(Danmaku(
                        time=float(parts[0]), mode=int(parts[1]),
                        font_size=int(parts[2]), color=int(parts[3]),
                        timestamp=int(parts[4]), pool=int(parts[5]),
                        author=parts[6], danmaku_id=int(parts[7]),
                        text=d.text or "",
                    ))
                except (ValueError, IndexError):
                    pass
        return danmakus

    def _check_api(self, resp: requests.Response) -> dict:
        data = resp.json()
        if data.get("code", -1) != 0:
            raise RuntimeError(f"B站 API 错误 (code={data.get('code')}): {data.get('message','')}")
        return data


# ══════════════════════════════════════════════════════════════
# 2. 腾讯视频 — 直接抓取
# ══════════════════════════════════════════════════════════════

class TencentVideoHandler(PlatformHandler):
    """腾讯视频弹幕处理器

    支持的 URL:
        - https://v.qq.com/x/page/vid.html
        - https://v.qq.com/x/cover/xxx/vid.html

    弹幕源: dm.video.qq.com 分段弹幕接口
    参考: Greasyfork #524107 (bilibili、腾讯视频弹幕下载)
    """

    SEG_CONFIG_URL = ("https://pbaccess.video.qq.com/"
                       "trpc.barrage.custom_barrage.CustomBarrage/"
                       "GetDMStartUpConfig")
    SEG_DM_URL = "https://dm.video.qq.com/barrage/segment"
    PAGE_INFO_URL = "https://vv.video.qq.com/getinfo"

    SEGMENT_STEP_MS = 30000
    MAX_SEGMENTS = 240

    @property
    def platform_name(self) -> str:
        return "tencent"

    def can_handle(self, url: str) -> bool:
        return "v.qq.com" in urlparse(url).netloc.lower()

    def fetch_danmaku(self, url: str) -> List[VideoInfo]:
        print(f"\n📡 腾讯视频直连 | {url}")

        vid = self._extract_vid(url)
        if not vid:
            raise ValueError(f"无法提取 vid: {url}")
        print(f"   🎬 vid: {vid}")

        title = self._fetch_title(vid)
        print(f"   🎬 {title}")

        danmakus = self._fetch_all(vid)

        return [VideoInfo(
            platform="tencent", title=title, video_id=vid,
            source=f"腾讯视频分段弹幕接口 (dm.video.qq.com/barrage/segment/{vid}/...)",
            source_url=url, danmakus=danmakus,
        )]

    # ── vid 提取 ──

    def _extract_vid(self, url: str) -> Optional[str]:
        for pat in [r'/page/([a-zA-Z0-9]+)\.html',
                     r'/cover/[^/]+/([a-zA-Z0-9]+)\.html',
                     r'/([a-zA-Z0-9]{11})\.html']:
            m = re.search(pat, url)
            if m: return m.group(1)
        q = parse_qs(urlparse(url).query)
        return q.get('vid', [None])[0]

    def _fetch_title(self, vid: str) -> str:
        try:
            r = self._session.get(self.PAGE_INFO_URL, params={
                "vids": vid, "platform": "101001", "charge": "0",
                "otype": "json", "defn": "shd",
            }, headers=self._make_headers(referer="https://v.qq.com/"), timeout=15)
            m = re.search(r'\{.*\}', r.text, re.DOTALL)
            if m:
                vi = json.loads(m.group()).get("vl", {}).get("vi", [])
                if vi: return vi[0].get("ti", f"腾讯视频_{vid}")
        except Exception:
            pass
        return f"腾讯视频_{vid}"

    # ── 分段弹幕获取 ──

    def _fetch_all(self, vid: str) -> List[Danmaku]:
        segments = self._fetch_segments(vid)
        if not segments:
            print("   ⚠️ 自动生成分段 ...")
            segments = [f"t/v1/{s}/{s + self.SEGMENT_STEP_MS}"
                        for s in range(0, self.SEGMENT_STEP_MS * self.MAX_SEGMENTS,
                                       self.SEGMENT_STEP_MS)]

        print(f"   📑 {len(segments)} 个分段")
        all_dm = []
        empty_streak = 0
        for i, seg in enumerate(segments):
            batch = self._fetch_segment(vid, seg)
            if batch:
                empty_streak = 0
                all_dm.extend(batch)
                if i > 0 and i % 20 == 0:
                    print(f"   📥 {i}/{len(segments)} (已获取 {len(all_dm)} 条) ...")
            else:
                empty_streak += 1
                if empty_streak >= 10:
                    print(f"   📥 分段 {i}: 连续空段，提前结束")
                    break
            time.sleep(0.05)

        seen = set()
        unique = [d for d in all_dm if not (d.danmaku_id in seen or seen.add(d.danmaku_id))]
        unique.sort(key=lambda x: x.time)
        print(f"   ✅ {len(unique)} 条弹幕")
        return unique

    def _fetch_segments(self, vid: str) -> List[str]:
        try:
            r = self._session.post(self.SEG_CONFIG_URL,
                                    json={"vid": vid, "engine_version": "2.1.10"},
                                    headers={**self._make_headers(referer="https://v.qq.com/"),
                                             "Content-Type": "application/json"},
                                    timeout=15)
            data = r.json()
            if isinstance(data, dict) and data.get("ret", 0) != 0:
                return []
            items = [(int(v.get("segment_start", 0)), v["segment_name"])
                     for k, v in data.items()
                     if isinstance(v, dict) and "segment_name" in v]
            items.sort()
            return [n for _, n in items] if items else []
        except Exception:
            return []

    def _fetch_segment(self, vid: str, seg: str) -> List[Danmaku]:
        url = f"{self.SEG_DM_URL}/{vid}/{seg}"
        try:
            r = self._session.get(url, headers=self._make_headers(referer="https://v.qq.com/"),
                                   timeout=30)
            if not r.text or not r.text.strip():
                return []
            data = r.json()
            items = data if isinstance(data, list) else data.get("barrage_list", [])
            if not items:
                return []

            result = []
            for item in items:
                try:
                    t = int(item.get("time_offset", 0)) / 1000.0
                    pos, col = 3, 0xFFFFFF
                    style_str = item.get("content_style", "")
                    if style_str and style_str.strip():
                        try:
                            s = json.loads(style_str)
                            col = int(s.get("color", "ffffff").lstrip("#"), 16)
                            pos = int(s.get("position", 3))
                        except (json.JSONDecodeError, ValueError):
                            pass
                    result.append(Danmaku(
                        time=t, mode={1: 5, 2: 4, 3: 1}.get(pos, 1),
                        font_size=25, color=col,
                        timestamp=int(item.get("create_time", 0)),
                        pool=0, author=str(item.get("vuid", "")),
                        danmaku_id=int(item.get("id", 0)),
                        text=str(item.get("content", "")),
                    ))
                except (ValueError, TypeError):
                    pass
            return result
        except requests.exceptions.Timeout:
            return []
        except Exception:
            return []


# ══════════════════════════════════════════════════════════════
# 3. 社区弹幕聚合 API (Danmu.Server 兼容)
# ══════════════════════════════════════════════════════════════

def _idx_to_alpha(n: int) -> str:
    """索引转大写字母标签: 0→A, 1→B, ..., 25→Z, 26→AA, 27→AB, ..."""
    result = ""
    n += 1
    while n > 0:
        n -= 1
        result = chr(ord('A') + n % 26) + result
        n //= 26
    return result


def _parse_selection(raw: str, title_count: int, ep_counts: List[int]) -> set:
    """解析社区模式选片字符串，返回 {(title_idx, ep_idx), ...}
    
    语法:
        ALL              → 全部
        A                → 大标题 A 的全部集
        A2               → 大标题 A 的第 2 集
        A-C-E            → 大标题 A、C、E 的全部集
        多段用 - 分隔，可混合：A1-B-C3
    """
    raw = raw.strip()
    if not raw:
        return set()

    # ALL 优先
    if raw.upper() == "ALL":
        result = set()
        for ti in range(title_count):
            for ei in range(ep_counts[ti]):
                result.add((ti, ei))
        return result

    # 同时检查每段中是否含 ALL
    tokens = [t.strip() for t in raw.upper().split('-') if t.strip()]
    for tok in tokens:
        if tok == "ALL":
            result = set()
            for ti in range(title_count):
                for ei in range(ep_counts[ti]):
                    result.add((ti, ei))
            return result

    # 构建标题名 → 索引 映射
    label_to_ti = {}
    for ti in range(title_count):
        label_to_ti[_idx_to_alpha(ti)] = ti

    result = set()
    for token in tokens:
        # 分离字母和数字: "A2" → ("A", 2), "AB" → ("AB", -1)
        m = re.match(r'^([A-Z]+)(\d*)$', token)
        if not m:
            continue
        alpha_part = m.group(1)
        num_part = m.group(2)

        ti = label_to_ti.get(alpha_part)
        if ti is None or ti >= title_count:
            continue

        if num_part:
            ei = int(num_part) - 1  # 用户从 1 开始编号
            if 0 <= ei < ep_counts[ti]:
                result.add((ti, ei))
        else:
            # 大标题全部集
            for ei in range(ep_counts[ti]):
                result.add((ti, ei))

    return result


class CommunityDanmakuHandler:
    """社区弹幕聚合 API 处理器

    兼容 Danmu.Server 生态 (GitHub: u2sb/Danmu.Server) 的 API v2 接口。
    通过搜索番剧名获取弹幕，覆盖 B站/腾讯视频/爱奇艺/优酷/巴哈姆特等平台。

    默认 API 列表 (来自通用字幕弹幕助手 Pro):
        danmu.let.gs, 2019102.xyz, paejay.asia,
        dm.stardm.us.kg, yun115.sbs, 47.108.249.117

    也可自行部署: https://github.com/u2sb/Danmu.Server
    """

    DEFAULT_API_BASES = [
        "https://danmu.let.gs/87654321",
        "https://www.2019102.xyz/87654321",
        "https://paejay.asia/87654321",
        "https://dm.stardm.us.kg/87654321",
        "https://www.yun115.sbs/87654321",
        "http://47.108.249.117:10011/87654321",
    ]

    def __init__(self, api_base: str = "", timeout: int = 15):
        self._session = requests.Session()
        self._session.trust_env = False
        self._timeout = timeout
        self._bases = [api_base] if api_base else self.DEFAULT_API_BASES
        self._active_base = ""

    @property
    def platform_name(self) -> str:
        return "community"

    def _make_headers(self) -> dict:
        return {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"),
            "Accept": "application/json",
        }

    def _api_get(self, path: str) -> dict:
        """尝试所有 API 基础 URL，返回第一个成功的结果"""
        errors = []
        # 优先使用上次成功的 API；否则轮询所有
        bases_to_try = []
        if self._active_base:
            bases_to_try.append(self._active_base)
            bases_to_try.extend(b for b in self._bases if b != self._active_base)
        else:
            bases_to_try = list(self._bases)

        for base in bases_to_try:
            url = base.rstrip('/') + '/' + path.lstrip('/')
            try:
                r = self._session.get(url, headers=self._make_headers(),
                                       timeout=self._timeout)
                if r.status_code == 200 and r.text.strip():
                    data = r.json()
                    if data and (not isinstance(data, dict) or len(data) > 0):
                        self._active_base = base
                        return data
                    else:
                        errors.append(f"{base}: 返回空数据")
                else:
                    errors.append(f"{base}: HTTP {r.status_code}")
            except requests.exceptions.Timeout:
                errors.append(f"{base}: 连接超时")
            except requests.exceptions.ConnectionError:
                errors.append(f"{base}: 无法连接")
            except Exception as e:
                errors.append(f"{base}: {type(e).__name__}")

        # 汇总失败信息
        print(f"\n   ⚠️ 以下社区 API 均不可用:")
        for err in errors[:6]:
            print(f"      {err}")
        print(f"\n   💡 提示:")
        print(f"      • 社区 API 可能暂时下线或网络不通")
        print(f"      • 可自行部署 Danmu.Server: https://github.com/u2sb/Danmu.Server")
        print(f"      • 部署后用 --api-base https://你的地址/TOKEN 指定")

        raise RuntimeError("社区 API 暂不可用，请稍后重试或自行部署")

    def search(self, keyword: str) -> List[dict]:
        """搜索番剧/剧集 (返回 anime 列表，含 episodes 字段)"""
        print(f"   🔍 搜索: {keyword}")
        data = self._api_get(f"api/v2/search/episodes?anime={requests.utils.quote(keyword)}")
        results = data if isinstance(data, list) else data.get("animes", [])
        print(f"   📋 找到 {len(results)} 个结果")
        return results

    def get_danmaku(self, comment_id) -> dict:
        """获取某集的弹幕数据"""
        return self._api_get(
            f"api/v2/comment/{comment_id}?withRelated=true&chConvert=0"
        )

    def fetch_danmaku(self, keyword: str, select: str = "",
                      episode: int = 0) -> List[VideoInfo]:
        """搜索番剧并获取弹幕（交互选片）

        Args:
            keyword: 番剧名称
            select: 选片字符串 (ALL/A/A2/A-C-E)，空则交互输入
            episode: 指定集数（旧版参数，选片模式下忽略）
        """
        print(f"\n📡 社区弹幕 API | 搜索: {keyword}")
        if self._active_base:
            print(f"   🌐 API: {self._active_base}")

        results = self.search(keyword)
        if not results:
            raise ValueError(f"未找到 '{keyword}' 的弹幕数据")

        # ── 收集所有标题和剧集 ──
        titles = []          # [(anime_title, [(ep_title, comment_id), ...]), ...]
        for anime in results:
            an_title = anime.get("animeTitle") or anime.get("title", "未知")
            episodes = anime.get("episodes", [])
            if not episodes:
                continue
            ep_list = []
            for ep in episodes:
                cid = ep.get("episodeId") or ep.get("commentId") or ep.get("id")
                if not cid:
                    continue
                ep_title = ep.get("episodeTitle") or ep.get("title", "")
                ep_list.append((ep_title, cid))
            if ep_list:
                titles.append((an_title, ep_list))

        if not titles:
            raise ValueError("搜索到的结果中没有可用剧集")

        # ── 构建标签并展示 ──
        title_labels = [_idx_to_alpha(i) for i in range(len(titles))]
        ep_counts = [len(eps) for _, eps in titles]

        print(f"\n   {'='*55}")
        print(f"   📺 搜索到 {len(titles)} 个大标题，共 {sum(ep_counts)} 集")
        print(f"   {'='*55}")

        for ti, (an_title, eps) in enumerate(titles):
            label = title_labels[ti]
            print(f"\n   [{label}] {an_title} ({len(eps)} 集)")
            for ei, (ep_title, cid) in enumerate(eps):
                print(f"       {label}{ei + 1}  {ep_title}")

        # ── 选片交互 ──
        print()
        print(f"   {'='*55}")
        print(f"   📌 选片指引:")
        print(f"      ALL          → 下载全部 {sum(ep_counts)} 集")
        print(f"      {title_labels[0]}            → 下载 [{title_labels[0]}] 的全部 {ep_counts[0]} 集")
        if ep_counts[0] > 0:
            print(f"      {title_labels[0]}1           → 仅下载 [{title_labels[0]}] 的第 1 集")
        if len(titles) >= 3:
            print(f"      {title_labels[0]}-{title_labels[1]}-{title_labels[2]}  → 下载 [{title_labels[0]}] [{title_labels[1]}] [{title_labels[2]}] 全部")
        print(f"      以上可自由组合，用 - 分隔，如 A1-B-C3")
        print(f"   {'='*55}")

        if select:
            choice = select
            print(f"\n   📌 命令行指定: {choice}")
        else:
            print()
            choice = input("   📌 请输入要下载的编号 (默认 ALL): ").strip()
            if not choice:
                choice = "ALL"

        selected = _parse_selection(choice, len(titles), ep_counts)
        if not selected:
            print("   ⚠️ 选择为空，退出")
            return []

        count = len(selected)
        print(f"\n   🎯 已选择 {count} 集，开始下载...")

        # ── 下载选中的弹幕 ──
        video_list = []
        sorted_sel = sorted(selected, key=lambda x: (x[0], x[1]))

        for idx, (ti, ei) in enumerate(sorted_sel):
            an_title, eps = titles[ti]
            ep_title, comment_id = eps[ei]
            label = f"{title_labels[ti]}{ei + 1}"

            print(f"   📥 [{idx+1}/{count}] {label} {ep_title} (id={comment_id}) ...")

            try:
                dm_data = self.get_danmaku(comment_id)
            except Exception as e:
                print(f"   ⚠️ 获取失败: {e}")
                continue

            danmakus = self._parse_danmaku(dm_data)
            video_list.append(VideoInfo(
                platform="community", title=f"{an_title} - {ep_title}",
                video_id=str(comment_id),
                source=f"社区弹幕聚合 API ({self._active_base or 'auto'})",
                source_url=f"{self._active_base}/api/v2/comment/{comment_id}",
                part_name=ep_title, danmakus=danmakus,
            ))

        total = sum(len(v.danmakus) for v in video_list)
        print(f"   ✅ 共获取 {total} 条弹幕 ({len(video_list)} 个视频)")
        return video_list

    def _parse_danmaku(self, data: dict) -> List[Danmaku]:
        """解析社区 API 返回的弹幕数据

        格式: {"count": N, "comments": [
            {"cid": id, "p": "time,mode,color,[source]", "m": "弹幕文本"},
            ...
        ]}
        """
        items = (data if isinstance(data, list)
                 else data.get("comments", data.get("data", [])))
        if not isinstance(items, list):
            return []

        danmakus = []
        for item in items:
            try:
                text = str(item.get("m", item.get("content", item.get("text", ""))))
                if not text:
                    continue

                # p 字段格式: "time,mode,color,[source]" → e.g. "0.00,1,16777215,[tencent]"
                p_str = item.get("p", "")
                time_val = 0.0
                mode = 1
                color = 0xFFFFFF

                if p_str:
                    parts = p_str.split(',')
                    if len(parts) >= 1:
                        try:
                            time_val = float(parts[0])
                        except ValueError:
                            pass
                    if len(parts) >= 2:
                        try:
                            mode = int(parts[1])
                        except ValueError:
                            pass
                    if len(parts) >= 3:
                        try:
                            color = int(parts[2])
                        except ValueError:
                            pass

                danmakus.append(Danmaku(
                    time=time_val,
                    mode=mode,
                    font_size=25,
                    color=color,
                    timestamp=0,
                    pool=0,
                    author="",
                    danmaku_id=int(item.get("cid", 0)),
                    text=text,
                ))
            except (ValueError, TypeError):
                continue

        return danmakus


# ══════════════════════════════════════════════════════════════
# 平台调度器
# ══════════════════════════════════════════════════════════════

class DanmakuDownloader:
    """弹幕下载调度器"""

    def __init__(self, api_base: str = ""):
        self._direct_handlers: List[PlatformHandler] = [
            BilibiliHandler(),
            TencentVideoHandler(),
        ]
        self._community = CommunityDanmakuHandler(api_base=api_base) if api_base else None

    def detect_direct(self, url: str) -> Optional[PlatformHandler]:
        for h in self._direct_handlers:
            if h.can_handle(url):
                return h
        return None

    def fetch(self, url: str, source: str = "auto",
              select: str = "", episode: int = 0,
              api_base: str = "") -> List[VideoInfo]:
        """获取弹幕"""
        if source == "community":
            ch = CommunityDanmakuHandler(api_base=api_base) if api_base else (
                self._community or CommunityDanmakuHandler())
            return ch.fetch_danmaku(url, select=select, episode=episode)

        if source == "direct":
            handler = self.detect_direct(url)
            if handler:
                return handler.fetch_danmaku(url)
            raise ValueError(f"不支持直连的平台链接，请用 --source community 试试")

        # auto: 先尝试直连，再尝试社区
        handler = self.detect_direct(url)
        if handler:
            return handler.fetch_danmaku(url)

        print("   ℹ️ 未识别为直连平台，尝试社区 API ...")
        ch = CommunityDanmakuHandler(api_base=api_base)
        return ch.fetch_danmaku(url, select=select, episode=episode)


# ══════════════════════════════════════════════════════════════
# 导出器
# ══════════════════════════════════════════════════════════════

class Exporter:
    """弹幕导出器 - XML / JSON / CSV"""

    @staticmethod
    def export_xml(video: VideoInfo, filepath: str) -> str:
        root = ET.Element("i")
        # 弹幕源引用注释
        source_comment = (
            f" 平台: {video.platform} | 标题: {video.title} | "
            f"视频ID: {video.video_id} | 弹幕源: {video.source} | "
            f"原始链接: {video.source_url} | 弹幕数: {len(video.danmakus)} | "
            f"导出工具: Danmaku Downloader "
        )
        root.append(ET.Comment(source_comment))

        for dm in video.danmakus:
            ET.SubElement(root, "d", p=dm.to_xml_d_attr()).text = dm.text

        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        tree.write(filepath, encoding="utf-8", xml_declaration=True)
        return filepath

    @staticmethod
    def export_json(video: VideoInfo, filepath: str) -> str:
        data = {
            "meta": {
                "platform": video.platform,
                "title": video.title,
                "video_id": video.video_id,
                "cid": video.cid,
                "part_index": video.part_index,
                "part_name": video.part_name,
                "duration": video.duration,
                "total_danmaku": len(video.danmakus),
                "source": video.source,
                "source_url": video.source_url,
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "tool": "Danmaku Downloader",
            },
            "danmakus": [
                {
                    "time": dm.time, "time_str": dm.time_str,
                    "mode": dm.mode, "mode_name": dm.mode_name,
                    "font_size": dm.font_size,
                    "color": dm.color, "color_hex": dm.color_hex,
                    "timestamp": dm.timestamp,
                    "pool": dm.pool, "author": dm.author,
                    "danmaku_id": dm.danmaku_id, "text": dm.text,
                }
                for dm in video.danmakus
            ],
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return filepath

    @staticmethod
    def export_csv(video: VideoInfo, filepath: str) -> str:
        with open(filepath, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            # 弹幕源引用行
            writer.writerow([f"# 弹幕来源: {video.source}"])
            writer.writerow([f"# 原始链接: {video.source_url}"])
            writer.writerow([f"# 平台: {video.platform} | 标题: {video.title}"])
            writer.writerow([])
            writer.writerow([
                "弹幕ID", "时间(秒)", "时间", "模式", "模式名称",
                "字号", "颜色(HEX)", "弹幕池", "发送者",
                "发送时间戳", "弹幕内容",
            ])
            for dm in video.danmakus:
                writer.writerow([
                    dm.danmaku_id, dm.time, dm.time_str,
                    dm.mode, dm.mode_name, dm.font_size,
                    dm.color_hex, dm.pool, dm.author,
                    dm.timestamp, dm.text,
                ])
        return filepath


# ══════════════════════════════════════════════════════════════
# 命令行界面
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="弹幕下载器 - 多平台多源弹幕获取，导出 XML/JSON/CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 直接粘贴链接（自动识别平台）
  python danmaku_dl.py "https://www.bilibili.com/video/BV1GJ411x7h7"
  python danmaku_dl.py "https://v.qq.com/x/page/u30468anxzn.html"

  # 指定输出目录和格式
  python danmaku_dl.py "BV链接" -o ./弹幕 -f xml

  # 通过社区 API 搜索番剧弹幕
  python danmaku_dl.py "鬼灭之刃" --source community
  python danmaku_dl.py "进击的巨人" --source community --ep 5

  # 使用自定义 API（自部署 Danmu.Server）
  python danmaku_dl.py "番剧名" --source community --select ALL

弹幕来源说明:
  direct    → 直接从 B站/腾讯视频 官方接口抓取（默认）
  community → 通过 Danmu.Server 社区聚合 API 搜索获取（交互选片）
  auto      → 先尝试 direct，失败则降级到 community（默认）
        """,
    )

    parser.add_argument("url", nargs='?',
                        help="视频链接或番剧名称")
    parser.add_argument("-o", "--output", default=".",
                        help="输出目录 (默认: 当前目录)")
    parser.add_argument("-f", "--format", nargs='+',
                        default=["xml", "json", "csv"],
                        choices=["xml", "json", "csv"],
                        help="输出格式 (默认: xml json csv)")
    parser.add_argument("-s", "--source", default="auto",
                        choices=["auto", "direct", "community"],
                        help="弹幕来源 (默认: auto)")
    parser.add_argument("--select", default="",
                        help="社区模式选片 (ALL/A/A2/A-C-E)，留空则交互输入")
    parser.add_argument("--api-base", default="",
                        help="社区 API 地址 (例: https://danmu.let.gs/TOKEN)")
    parser.add_argument("--ep", type=int, default=0,
                        help="指定剧集数 (直接模式)")
    parser.add_argument("--no-multipart", action="store_true",
                        help="多P视频只下载第一P")

    args = parser.parse_args()

    # 有无命令行参数的统一标识
    has_url = bool(args.url)

    # 交互模式
    url = args.url
    if not url:
        print("╔══════════════════════════════════════════════════════╗")
        print("║       弹幕下载器  Danmaku Downloader                 ║")
        print("║   支持 B站/腾讯视频 + 社区弹幕聚合 API               ║")
        print("║                作者：3537so                          ║")
        print("╚══════════════════════════════════════════════════════╝")
        print()
        url = input("🔗 请输入视频链接: ").strip()
        if not url:
            print("❌ 未输入链接，退出。")
            input("\n按任意键退出...")
            sys.exit(0)

        # ── 模式选择 ──
        print()
        print("📌 选择弹幕来源:")
        print("   [1] 自动识别（B站/腾讯直连）")
        print("   [2] 社区搜索（按番剧名搜索弹幕）")
        choice = input("   请选择 (默认 1): ").strip()
        if choice == "2":
            args.source = "community"

        # ── 格式选择 ──
        print()
        print("📄 选择输出格式:")
        print("   [1] XML  (Danmaku Anywhere 兼容)")
        print("   [2] JSON (结构化数据)")
        print("   [3] CSV  (Excel 可打开)")
        print("   [4] 全部 (XML + JSON + CSV，推荐)")
        fmt_choice = input("   请选择 (默认 4): ").strip()
        fmt_map = {
            "1": ["xml"], "2": ["json"], "3": ["csv"],
            "4": ["xml", "json", "csv"], "": ["xml", "json", "csv"],
        }
        args.format = fmt_map.get(fmt_choice, ["xml", "json", "csv"])
        print()

    output_dir = os.path.abspath(args.output)
    os.makedirs(output_dir, exist_ok=True)

    downloader = DanmakuDownloader(api_base=args.api_base)

    try:
        video_list = downloader.fetch(
            url, source=args.source,
            select=args.select, episode=args.ep, api_base=args.api_base,
        )

        if not video_list:
            print("❌ 未获取到任何弹幕数据")
            if not has_url:
                input("\n按任意键退出...")
            sys.exit(1)

        if args.no_multipart and len(video_list) > 1:
            print(f"\n📌 --no-multipart 模式，仅处理第1P")
            video_list = video_list[:1]

        print(f"\n📦 导出到: {output_dir}")
        exported = []

        for video in video_list:
            if len(video_list) > 1:
                prefix = f"{video.platform}_{video.video_id}_P{video.part_index}"
            else:
                prefix = f"{video.platform}_{video.video_id}"

            if "xml" in args.format:
                fp = os.path.join(output_dir, f"{prefix}.xml")
                Exporter.export_xml(video, fp)
                exported.append(fp)
                print(f"   📄 {os.path.basename(fp)} ({len(video.danmakus)} 条)")

            if "json" in args.format:
                fp = os.path.join(output_dir, f"{prefix}.json")
                Exporter.export_json(video, fp)
                exported.append(fp)

            if "csv" in args.format:
                fp = os.path.join(output_dir, f"{prefix}.csv")
                Exporter.export_csv(video, fp)
                exported.append(fp)

        total_dm = sum(len(v.danmakus) for v in video_list)
        print(f"\n{'='*55}")
        print(f"✅ 完成！{len(video_list)} 个视频, {total_dm} 条弹幕, {len(exported)} 个文件")
        print(f"📁 {output_dir}")
        print(f"{'='*55}")

    except ValueError as e:
        print(f"\n❌ 错误: {e}")
        if not has_url:
            input("感谢使用@3537so\n按任意键退出...")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"\n❌ 网络请求失败: {e}")
        if not has_url:
            input("感谢使用@3537so\n按任意键退出...")
        sys.exit(1)
    except RuntimeError as e:
        print(f"\n❌ 运行错误: {e}")
        if not has_url:
            input("感谢使用@3537so\n按任意键退出...")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {type(e).__name__}: {e}")
        if not has_url:
            input("感谢使用@3537so\n按任意键退出...")
        sys.exit(1)

    # 非命令行参数模式（无预填 URL），按任意键退出
    if not has_url:
        input("感谢使用@3537so\n按任意键退出...")


if __name__ == "__main__":
    main()
