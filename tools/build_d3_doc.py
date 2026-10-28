"""生成《Bluebird聊天室开发-3 10.24.docx》工作汇总（桌面）。

    py tools/build_d3_doc.py                 # 写到桌面
    py tools/build_d3_doc.py --out 某路径.docx

内容全部来自仓库里的实际产物：docs/test/D3.md、D3-gui-evidence.txt、docs/gui/*.png。
D4 写课程设计报告时可以复用这里的排版函数（标题、表格、代码块、插图、图注）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
FONT = "微软雅黑"
MONO = "Consolas"
ACCENT = RGBColor(0x0B, 0x57, 0xD0)
MUTED = RGBColor(0x6B, 0x72, 0x80)


# ---------------------------------------------------------------- 排版工具


def set_east_asian(obj, name: str = FONT) -> None:
    """python-docx 只设 font.name 管不到中文，必须同时写 w:eastAsia。"""
    rpr = obj.element.get_or_add_rPr() if hasattr(obj.element, "get_or_add_rPr") else None
    if rpr is None:
        return
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)


def style_document(doc: Document) -> None:
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "Caption"):
        try:
            style = doc.styles[name]
        except KeyError:
            continue
        style.font.name = FONT
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), FONT)

    doc.styles["Normal"].font.size = Pt(10.5)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.25
    doc.styles["Normal"].paragraph_format.space_after = Pt(4)

    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.2)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)


def _run(paragraph, text: str, size: float, bold: bool, color: RGBColor | None,
         mono: bool = False):
    run = paragraph.add_run(text)
    run.font.size = Pt(size)
    run.bold = bold
    if mono:
        run.font.name = MONO
        set_east_asian(run, MONO)
    else:
        set_east_asian(run)
    if color is not None:
        run.font.color.rgb = color
    return run


INLINE = re.compile(r"\*\*(?P<bold>.+?)\*\*|`(?P<code>[^`]+)`")


def add_runs(paragraph, text: str, size: float, *, bold: bool = False,
             color: RGBColor | None = None) -> None:
    """把 ``**加粗**`` 与 `` `代码` `` 拆成多个 run；表格和正文共用这一份。"""
    position = 0
    for match in INLINE.finditer(text):
        if match.start() > position:
            _run(paragraph, text[position:match.start()], size, bold, color)
        if match.group("bold") is not None:
            _run(paragraph, match.group("bold"), size, True, color)
        else:
            _run(paragraph, match.group("code"), size - 0.5, bold, color, mono=True)
        position = match.end()
    if position < len(text):
        _run(paragraph, text[position:], size, bold, color)


def para(doc: Document, text: str = "", *, size: float = 10.5, bold: bool = False,
         color: RGBColor | None = None, align=None, space_after: float = 4,
         indent: float = 0.0) -> object:
    """普通段落。文本里的 ``**xxx**`` 会变成加粗片段（docx 没有 markdown，得自己拆）。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    if indent:
        p.paragraph_format.left_indent = Cm(indent)
    if align is not None:
        p.alignment = align
    if text:
        add_runs(p, text, size, bold=bold, color=color)
    return p


def heading(doc: Document, text: str, level: int = 1) -> None:
    h = doc.add_heading(level=level)
    run = h.add_run(text)
    run.font.name = FONT
    set_east_asian(run)
    run.font.size = Pt({1: 16, 2: 13, 3: 11.5}.get(level, 11))
    run.font.color.rgb = ACCENT if level <= 2 else RGBColor(0x22, 0x22, 0x22)
    h.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    h.paragraph_format.space_after = Pt(4)


def bullet(doc: Document, text: str, size: float = 10.5) -> None:
    p = doc.add_paragraph(style="List Bullet")
    run = p.add_run(text)
    run.font.size = Pt(size)
    set_east_asian(run)


def code_block(doc: Document, text: str) -> None:
    for line in text.strip("\n").splitlines():
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.left_indent = Cm(0.4)
        pPr = p._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), "F4F5F7")
        pPr.append(shd)
        run = p.add_run(line if line else " ")
        run.font.name = MONO
        run.font.size = Pt(9)
        set_east_asian(run, MONO)
    para(doc, "", space_after=2)


def table(doc: Document, headers: list[str], rows: list[list[str]],
          widths: list[float] | None = None, size: float = 9.5) -> None:
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    # Word/LibreOffice 默认会按内容自动伸缩，必须显式固定布局才认我们给的列宽
    t.autofit = False
    tbl_pr = t._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)
    if widths:
        total = OxmlElement("w:tblW")
        total.set(qn("w:type"), "dxa")
        total.set(qn("w:w"), str(int(sum(widths) * 567)))     # 1 cm ≈ 567 twips
        tbl_pr.append(total)

    header = t.rows[0].cells
    for index, text in enumerate(headers):
        header[index].text = ""
        run = header[index].paragraphs[0].add_run(text)
        run.bold = True
        run.font.size = Pt(size)
        set_east_asian(run)
        fill = OxmlElement("w:shd")
        fill.set(qn("w:val"), "clear")
        fill.set(qn("w:fill"), "E8F0FE")
        header[index]._tc.get_or_add_tcPr().append(fill)
    for row in rows:
        cells = t.add_row().cells
        for index, text in enumerate(row):
            cells[index].text = ""
            add_runs(cells[index].paragraphs[0], str(text), size)
    if widths:
        for row in t.rows:
            for index, width in enumerate(widths):
                row.cells[index].width = Cm(width)
        for index, width in enumerate(widths):
            t.columns[index].width = Cm(width)
    para(doc, "", space_after=2)


def figure(doc: Document, path: Path, caption: str, width: float = 15.0) -> None:
    if not path.exists():
        para(doc, f"（缺图：{path}）", color=MUTED)
        return
    doc.add_picture(str(path), width=Cm(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    para(doc, caption, size=9, color=MUTED, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=8)


# ---------------------------------------------------------------- 正文


def git_head() -> str:
    """把当前提交号写进文档，免得文档说「已完成」但对不上是哪一版代码。"""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "（未取到）"
    except (OSError, subprocess.SubprocessError):
        return "（未取到）"


#: D3 的两个里程碑提交（写死而不是取 HEAD：文档要稳定，不能因为改个脚本就变样）
COMMITS = "1bab7bb　D3 界面与健壮性（主体）　｜　4e37d2d　换网复验与收尾"


def build(out_path: Path) -> None:
    doc = Document()
    style_document(doc)

    # ---------------- 封面 ----------------
    para(doc, "", space_after=40)
    para(doc, "计算机网络技术课程设计 · Bluebird 局域网聊天室",
         size=10.5, color=MUTED, align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, "D3 工作汇总", size=30, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)
    para(doc, "健壮性与图形界面（A20—A24）", size=15, color=ACCENT,
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=26)
    para(doc, "2026-10-24　·　v1.0", size=10.5, color=MUTED,
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=40)

    table(doc, ["项目", "内容"], [
        ["今日主题", "把「能用」变成「好用」：图形界面、在线列表、私聊窗、颜色设置、断线提示"],
        ["代码提交", COMMITS],
        ["测试", "93 passed（昨日 74；新增 3 条空房自退）"],
        ["界面自动走查", "34/34 全通过（真 tkinter 窗口 + 真服务端）"],
        ["换网复验", "T13：手机热点 172.20.10.5 上 11/11（走真实网卡，非回环）"],
        ["旧判据回归", "D1 13/13、D2 20/20、D1-CLI 7/7、D2-CLI 9/9、健壮性 13/13，全部未回归"],
        ["验收用例", "T01—T12 全绿；T13 的换 IP 部分已验证，只剩「第二台机器」现场走一遍"],
    ], widths=[3.0, 13.2])

    doc.add_page_break()

    # ---------------- 一 ----------------
    heading(doc, "一　今天要证明的三件事", 1)
    para(doc, "四天清单给 D3 的门禁是一句话：「拔网线 45 s 显离线、GUI 三端可用」。"
              "把人话翻成可执行的判据，今天其实只证明三件事：")
    table(doc, ["#", "要证明的", "判据", "结果"], [
        ["1", "界面不是画出来的，是数据的显示器",
         "三端同时在线的列表、消息、颜色都和网络层一致", "34 项走查全绿"],
        ["2", "断线是「看得见」的，不是「猜得到」的",
         "杀掉服务端 → 红点 + 横幅 + 输入框禁用 → 一键回登录窗", "A24 四项全过"],
        ["3", "加一个界面没有把原有功能弄坏",
         "D1/D2 的协议层与 CLI 判据一条不变", "13/13、20/20、9/9、7/7"],
    ], widths=[0.8, 4.6, 6.8, 4.0])
    para(doc, "第一条尤其重要：如果界面自己维护一套「谁在线」，那么服务端说 bob 走了、"
              "界面还以为他在——这类 bug 在演示时最难解释。今天所有界面状态都只由网络事件驱动。")

    # ---------------- 二 ----------------
    heading(doc, "二　总体思路：先拆网络层，再画界面", 1)
    para(doc, "如果直接把命令行客户端的代码复制一份给 tkinter，就会出现两套心跳、"
              "两套断线判定、两套错误文案。所以今天的第一步不是画窗口，而是把"
              "「连接 + 协议 + 心跳 + 断线」抽成一层，让两个界面共用。")
    code_block(doc, """
        ┌──────────────┐   on_event((kind, payload))   ┌──────────────┐
        │ client/net.py│ ────────────────────────────► │  console CLI │
        │ 后台线程 +    │                               └──────────────┘
        │ asyncio 循环  │    queue.Queue                ┌──────────────┐
        │              │ ──► root.after(50) 轮询 ────► │ tkinter GUI  │
        └──────────────┘                               └──────────────┘
             ▲
             └── send_group / send_private / set_color / logout
                 （任意线程可调，内部 call_soon_threadsafe 送回网络线程）
    """)
    para(doc, "线程边界只有一条规则：**控件只在主线程碰**。网络线程把一切都变成事件，"
              "GUI 用 root.after(50) 把事件取出来再更新界面。这条规则违反一次，"
              "在 Windows 上就是「随机崩」，而且崩的位置和真正的原因往往隔得很远。")

    # ---------------- 三 ----------------
    heading(doc, "三　每一步做了什么、用了什么知识点", 1)
    para(doc, "下面 12 步按实际施工顺序排，每步都给出「做了什么 / 用了什么 / 怎么算过」。")

    steps = [
        ("第 1 步　拆出网络层 client/net.py",
         "把连接、登录、心跳、断线判定、事件分发从 CLI 里搬到一个独立文件；"
         "对外只暴露 start/stop 与四个发送方法，结果统一从 on_event 回调出去。",
         "asyncio 事件循环、线程与 GIL、call_soon_threadsafe、生产者—消费者模型、事件驱动",
         "同一份代码既能跑命令行也能跑界面；pytest 里用假 writer 就能测。"),
        ("第 2 步　命令行客户端改成纯渲染层",
         "cli.py 不再自己收发，只做两件事：把键盘输入变成 send_*，把事件打印成中文。",
         "分层重构、回归测试保底（改完必须让 D1/D2 的 16 项 CLI 判据继续全绿）",
         "D1-CLI 7/7、D2-CLI 9/9，一条没变。"),
        ("第 3 步　界面公共零件 client/ui.py",
         "字体、协议四色到屏幕色值的映射、只读消息框、贴底规则、字节计数集中在一处。",
         "设计令牌（把颜色字体集中管理）、UTF-8 字节与字符的区别、关注点分离",
         "单测 6 条：`你好 😀` = 11 字节、四色都够深、缩放系数不低于 1。"),
        ("第 4 步　登录窗",
         "昵称 + 服务器 IP:端口 + 「进入聊天室」；昵称非法、端口非数字都在原地用红字提示。",
         "tkinter 几何管理（pack）、输入校验、把「连接中」这种中间态画出来",
         "启动即出现登录窗；填错不弹窗、不崩，只在原地提示。"),
        ("第 5 步　四区主窗",
         "顶栏（状态点/服务器/在线人数/⚙ 设置）、消息区、右侧 140 px 在线列表、底部输入区。",
         "grid 的权重与跨行跨列、可缩放布局（1600×900 与 800×600 都不乱）、界面分区的信息层级",
         "窗口拉大拉小，四区比例正常，列表不变形。"),
        ("第 6 步　消息渲染与贴底滚动",
         "`[昵称]：内容` 格式，按发送者颜色着色；系统消息居中灰字；新消息贴底，"
         "用户上翻历史时不抢滚动条。",
         "Text 组件的 tag 机制、只读（state=disabled）、yview 判断是否贴底、只追加不重排",
         "T06、T07 全绿（连发 30 条仍贴底；上翻时不跳）。"),
        ("第 7 步　在线列表与私聊窗",
         "全量名单重建 + 增量增删；昵称按各自颜色显示；双击开独立 Toplevel 私聊窗。",
         "Listbox 与 itemconfig、Toplevel 生命周期、视图与数据分离、私聊不在主窗出现的约束",
         "T11 全绿：双击开窗、私聊只进小窗、主聊天室搜不到私聊内容。"),
        ("第 8 步　设置面板与颜色广播",
         "四个单选（黑/绿/蓝/红）+ 昵称只读；改色立即广播，所有端列表与后续消息都变色。",
         "观察者模式（服务端广播 → 各客户端更新本地映射）、StringVar 与单选组、"
         "「历史不回溯变色」的不可变渲染",
         "T09 全绿：改绿后两端都变；别人改蓝这边也跟着变。"),
        ("第 9 步　断线横幅与「重新连接」",
         "断线时状态点变红、黄色横幅出现、输入框禁用；点按钮回到登录窗，昵称预填。",
         "状态机（已连接 → 已断线）、优雅降级、v2 的取舍：零持久化 ⇒ 断线即回登录",
         "A24 四项全绿；断线原因显示为中文（网络中断 / 服务端关闭 / 超时）。"),
        ("第 10 步　DPI 感知",
         "声明 DPI 感知，并按系统缩放同时放大字体与像素常量。",
         "坐标空间：逻辑像素 vs 物理像素、Windows 位图缩放、tk scaling、"
         "为什么 150% 屏上不声明就会又糊又截图对不齐",
         "150% 屏上字清晰；窗口尺寸与相对比例不变。"),
        ("第 11 步　自动化走查工具",
         "开真窗口、真服务端、真的第二个用户，逐条断言 34 项；`--shots` 顺便截五张图。",
         "测试替身（假 writer / 真 socket 两种）、驱动 GUI 的 root.update() 轮询、"
         "Win32 窗口矩形 + GDI 截图、进程树管理",
         "34/34，连跑多次稳定；截图存 docs/gui/。"),
        ("第 12 步　收尾：单测、回归、文档",
         "补界面公共零件单测；把 D1/D2 的所有判据重跑一遍；写 D3.md 与本文。",
         "回归测试、变异检查（改坏一处必须变红）、证据可复现",
         "93 passed；13/13、20/20、9/9、7/7 全绿。"),
    ]
    for title, what, knowledge, judge in steps:
        heading(doc, title, 2)
        para(doc, f"**做了什么**　{what}")
        para(doc, f"**用了什么**　{knowledge}")
        para(doc, f"**怎么算过**　{judge}")

    # ---------------- 四 ----------------
    heading(doc, "四　知识点清单", 1)
    table(doc, ["知识点", "落在哪", "一句话解释"], [
        ["asyncio 事件循环", "client/net.py", "一个线程跑一个循环，所有网络等待都是挂起而不是阻塞"],
        ["线程与回调", "client/net.py", "网络线程不碰控件；跨线程用 call_soon_threadsafe 排队"],
        ["生产者—消费者", "client/net.py + gui.py", "网络线程生产事件，主线程 50 ms 消费一次"],
        ["tkinter 几何管理", "gui.py / chatui.py", "pack 做线性排列，grid 做四区；两者参数不能混用"],
        ["Text 与 tag", "ui.py / chatui.py", "只读消息框 + 颜色标签；贴底靠 yview 判断"],
        ["Toplevel 生命周期", "dialogs.py", "私聊窗独立存在，关闭要同步清理字典，否则会「幽灵窗口」"],
        ["状态机", "gui.py", "已连接 / 已断线两态，断线后所有输入路径都要关掉"],
        ["UTF-8 字节 vs 字符", "ui.py", "协议限长 2000 字节：汉字 3 字节、emoji 4 字节"],
        ["DPI 与坐标空间", "gui.py", "逻辑像素和物理像素差缩放倍数；声明感知后才一致"],
        ["进程树", "tools/procutil.py", "Windows 的 venv 启动器会留下真解释器，必须按树杀"],
        ["测试替身", "tests/test_client.py", "假 writer 把 15 s 心跳压到毫秒级，专测时序路径"],
        ["变异检查", "全过程", "手动改坏一处，对应用例必须变红，否则就是假测试"],
    ], widths=[3.6, 4.2, 9.2])

    # ---------------- 五 ----------------
    heading(doc, "五　今天踩的四个坑", 1)
    table(doc, ["现象", "根因", "解法"], [
        ["「杀掉服务端」后客户端毫无反应，重跑脚本又连上了上一轮的服务端",
         "Windows 上 .venv\\Scripts\\python.exe 是个启动器，会再起一个真解释器；"
         "terminate() 只杀了启动器，真进程还占着端口",
         "统一用 taskkill /T /F 杀整棵进程树（tools/procutil.py）"],
        ["150% 屏上字发虚，自动截图永远歪 1.5 倍",
         "进程没声明 DPI 感知，窗口被当位图拉伸；Tk 给逻辑像素、截图收物理像素",
         "SetProcessDpiAwareness(1) + tk scaling + 像素常量过 px()"],
        ["登录窗一创建就抛 bad option \"-sticky\"",
         "sticky 是 grid 的选项，pack 没有",
         "两种布局别混用参数；pack 用 anchor/fill"],
        ["界面「突然不动了」，但进程还活着",
         "事件处理函数抛异常后，root.after 没有被重新排上，事件泵就此停摆",
         "_poll 里 try/except 兜底 + 打 traceback + 界面提示"],
    ], widths=[5.6, 6.0, 5.4])

    # ---------------- 六 ----------------
    heading(doc, "六　界面成果", 1)
    gui = ROOT / "docs" / "gui"
    figure(doc, gui / "D3-login.png", "图 1　登录窗：昵称 + 服务器地址，失败时红字提示", 10.5)
    figure(doc, gui / "D3-main.png", "图 2　四区主窗：顶栏 / 消息区 / 在线列表 / 输入区", 15.0)
    figure(doc, gui / "D3-private.png", "图 3　私聊窗：标题「与 bob 私聊」，主聊天室看不到这些内容", 10.5)
    figure(doc, gui / "D3-settings.png", "图 4　设置面板：四色单选，昵称不可在此修改", 9.5)
    figure(doc, gui / "D3-disconnected.png",
           "图 5　断线：红点 + 黄色横幅 + 输入框禁用，「重新连接」回登录窗", 15.0)

    # ---------------- 七 ----------------
    heading(doc, "七　怎么验证今天的成果", 1)
    para(doc, "验证分三层：一条命令的自动验收（最快）、手工三端演示（最直观）、"
              "断线演示（D3 的门禁）。")
    para(doc, "**最省事的启动方式**：双击仓库根目录（桌面上也放了一份）的「Bluebird 聊天室」快捷方式——"
              "8888 上没有服务端就先在后台起一个（日志写 logs/server.log），随后直接弹出登录窗；"
              "再双击一次只多开一个客户端窗口，不会起第二个服务端。关掉所有窗口后，"
              "服务端在最后一个客户端离开 2 分钟后自动退出（`--idle-exit 120`），"
              "不会留下看不见的后台进程占着端口。")
    para(doc, "从 GitHub 下载的仓库里**没有**这个快捷方式：`.lnk` 存的是绝对路径，换台机器必然失效，"
              "所以只能在本机生成。下载后先双击一次 `创建桌面快捷方式.bat` 即可；"
              "不想做快捷方式的话，直接双击 `启动聊天室.bat` 也能开聊——这两个脚本都不需要装任何依赖。")

    heading(doc, "7.1　一条命令：自动验收", 2)
    code_block(doc, """
cd D:\\Bluebird
.\\.venv\\Scripts\\python.exe -m pytest                  # 期望 93 passed
.\\.venv\\Scripts\\python.exe tools\\verify_d3_gui.py     # 期望 PASS 34 / FAIL 0，约 20 秒
""")
    para(doc, "走查脚本会自己起服务端（占一个空闲端口），开一个真的 tkinter 窗口当 alice，"
              "再开一个无界面的网络客户端当 bob，按《结果示例》第 5 章的用例逐条断言，"
              "输出同时写到 docs/test/D3-gui-evidence.txt。")

    heading(doc, "7.2　手工三端演示（5 分钟）", 2)
    table(doc, ["步骤", "操作", "应该看到"], [
        ["1", "起服务端：python -m server.server",
         "日志出现 listening on 0.0.0.0:8888"],
        ["2", "开三个客户端：python -m client.gui --name alice / bob / carol",
         "三个登录窗；各自填昵称进主窗，顶栏在线人数依次 1→2→3"],
        ["3", "alice 发「你好 😀 世界」", "三端都出现 [alice]：你好 😀 世界，颜色一致"],
        ["4", "bob 在右侧列表双击 alice", "弹出「与 alice 私聊」窗口；主聊天室不出现私聊内容"],
        ["5", "私聊窗里发一句", "只有 alice 的私聊窗收到；carol 两端都没有"],
        ["6", "alice 点 ⚙ 设置 → 换绿色", "三端的列表里 alice 变绿；之后再发的消息也是绿色"],
        ["7", "连发 30 条", "最新一条始终贴在底部；往上滚后再来新消息，视图不跳"],
        ["8", "关掉 carol", "另外两端的列表 1 s 内少一人，并出现「carol 退出了」"],
    ], widths=[1.2, 7.4, 7.4])

    heading(doc, "7.3　断线演示（D3 的门禁）", 2)
    para(doc, "两种做法，效果一样：")
    bullet(doc, "① 直接关掉服务端的终端窗口（等价于杀进程）；")
    bullet(doc, "② 拔网线 / 关 Wi-Fi（45 s 内服务端判定超时，其余端看到该用户离线）。")
    para(doc, "期望现象：客户端顶栏圆点由绿变红、出现黄色横幅「连接已断开：…」、"
              "输入框变灰不能再输入；点「重新连接」回到登录窗，昵称还预填着。"
              "这一步对应四天清单的 A23／A24 与《结果示例》的 T12。")

    heading(doc, "7.4　13 条验收用例的当前状态", 2)
    table(doc, ["用例", "内容", "状态"], [
        ["T01", "登录成功，顶栏人数 1", "✅ D3"],
        ["T02", "昵称重复，留在登录窗并提示", "✅ D3"],
        ["T03", "中文消息两端一致", "✅ D3"],
        ["T04", "英文消息两端一致", "✅ D3"],
        ["T05", "emoji 与日文按字节原样传输", "✅ D3"],
        ["T06", "连发 20 条，最新一条在底部", "✅ D3（走查里连发 30 条）"],
        ["T07", "上翻历史时新消息不打断阅读", "✅ D3"],
        ["T08", "格式 [昵称]：内容，全角冒号", "✅ D3"],
        ["T09", "改色后两端都变", "✅ D3"],
        ["T10", "关掉重开，颜色回到黑色", "✅ D3（零持久化，天然满足）"],
        ["T11", "私聊只进对方的小窗", "✅ D3"],
        ["T12", "给离线的人发私聊：提示不在线", "✅ D3"],
        ["T13", "换网段仍能连上", "🟡 换 IP 已验（172.20.10.5），缺第二台机器"],
    ], widths=[1.6, 7.4, 7.2])

    heading(doc, "7.5　换网复验（T13）", 2)
    para(doc, "D3 当天现场换了网络（手机热点 172.20.10.5/28），正好把 T13 能在一台机器上做的"
              "那一半补掉。一条命令：`python tools/verify_d3_net.py`，11 项全过。")
    table(doc, ["判据", "结果"], [
        ["取到的是局域网地址，不是回环 / 169.254 自动私有地址", "✅ 172.20.10.5"],
        ["客户端用局域网 IP 登录（不是 127.0.0.1）", "✅ code=0"],
        ["**服务端看到的对端地址是同网段的局域网 IP**", "✅ peer=('172.20.10.5', …)"],
        ["服务端监听 0.0.0.0，不是只绑回环", "✅ [('0.0.0.0', …)]"],
        ["三端在线、群聊、私聊隔离、改色广播全经真实网卡", "✅ 4 项"],
        ["同一个服务端，127.0.0.1 也照样能连", "✅ 两种地址都行"],
        ["真实入口 python -m server.server 经局域网 IP 可登录", "✅"],
    ], widths=[11.0, 5.2])
    para(doc, "**这证明了什么**：代码里没有写死 127.0.0.1，换个网段只要改登录窗里的地址，"
              "不用动一行代码。界面也重跑了同样 34 项（`--host 172.20.10.5`），"
              "截图里顶栏显示的服务器地址就是局域网 IP。")
    para(doc, "**还差什么**：同一台机器连自己的局域网 IP 走的是本机协议栈，"
              "服务端看到的对端地址确实是 WLAN 的 IP（不是回环），但没经过交换机/AP。"
              "现场让同学用手机热点连进来跑一次客户端，T13 才算完整。")
    para(doc, "另外确认了 Windows 防火墙对 python.exe 已有入站放行规则（Private/Public），"
              "换到「公用网络」不会被静默拦掉。没有这条规则时，别人连不进来会表现为"
              "「连接超时」而不是「被拒绝」——演示前值得先看一眼。")

    heading(doc, "7.6　结果不对时怎么排查", 2)
    para(doc, "沿用 D1 定下的三档定位法，先分清是「没发」「发了没解析」还是「解析了没渲染」：")
    table(doc, ["现象", "先看哪里", "常见原因"], [
        ["对方完全没反应", "服务端日志有没有 recv 这一帧", "客户端没发出去（看发送失败提示）／被限流 2002"],
        ["服务端收到了，别人没收到", "抓包（tools/tap.py）或服务端 warning", "广播时写失败被断开、或对端不读数据"],
        ["别人收到了，界面没显示", "走查脚本的事件流（seen 列表）", "事件没进队列／_poll 抛异常（看控制台 traceback）"],
    ], widths=[4.4, 6.2, 5.4])

    # ---------------- 八 ----------------
    heading(doc, "八　遗留与明日计划", 1)
    table(doc, ["项", "说明", "归到"], [
        ["T13 的第二台机器", "换 IP 已在 §7.5 验完；还缺真机对真机走一趟网络", "D4 现场"],
        ["50 并发压测", "bench.py 与 P95 数据，界面不参与压测，只验服务端", "D4 A27"],
        ["12/13 条用例表", "今天已跑完 T01—T12，D4 需要整理成表并补截图", "D4 A26"],
        ["报告与答辩", "D3 的截图（图 1—图 5）可直接进报告的「实现与测试」", "D4 A29—A31"],
        ["历史消息", "零持久化，新进来的人看到空聊天区——这是需求决定⑥的直接后果，写进局限性", "报告"],
    ], widths=[3.0, 9.4, 3.8])

    para(doc, "", space_after=10)
    para(doc, "一句话总结：今天没有加新功能，而是把已经能用的东西变成了「能演示、能讲清楚」的东西——"
              "界面是数据的显示器，断线是看得见的状态，而所有旧判据一条没退。",
         bold=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    print(f"saved -> {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="build_d3_doc")
    default = Path.home() / "Desktop" / "Bluebird聊天室开发-3 10.24.docx"
    parser.add_argument("--out", type=Path, default=default)
    args = parser.parse_args()
    build(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
