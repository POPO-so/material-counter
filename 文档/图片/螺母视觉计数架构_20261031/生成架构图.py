"""生成本次问答文档的离线 HTML 图稿和 SVG 图片；仅使用 Python 标准库。"""
from html import escape
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
PAPER, INK, MUTED, ACCENT = '#f5f5f5', '#2d3142', '#4f5d75', '#eb6c36'
FONT = "'Geist','Microsoft YaHei','Noto Sans CJK SC',sans-serif"
MONO = "'Geist Mono','Consolas','Microsoft YaHei',monospace"


def text(x, y, value, size=16, color=INK, anchor='middle', weight=400):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'font-family="{FONT}" text-anchor="{anchor}" font-weight="{weight}">'
            f'{escape(value)}</text>')


def node(x, y, w, h, title, sub='', kind='', focal=False):
    color = ACCENT if focal else INK
    fill = '#fdefe8' if focal else ('#e9ebef' if kind == 'DDR' else '#ffffff')
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{color}" stroke-width="1.2"/>'
    s += text(x+16, y+24, kind, 12, MUTED, 'start', 600)
    s += text(x+w/2, y+52, title, 20, INK, weight=600)
    if sub:
        s += text(x+w/2, y+80, sub, 12, MUTED)
    return s


def arrow(path, label='', x=0, y=0, dashed=False, accent=False):
    color = ACCENT if accent else MUTED
    dash = ' stroke-dasharray="8,4"' if dashed else ''
    marker = 'arrow-accent' if accent else 'arrow'
    s = f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.2"{dash} marker-end="url(#{marker})"/>'
    if label:
        w = ((len(label)*9+24+3)//4)*4
        s += f'<rect x="{x-w/2}" y="{y-20}" width="{w}" height="24" fill="{PAPER}"/>'
        s += text(x, y-4, label, 12, color)
    return s


def figure(slug, title, desc, edges, nodes, notes=(), legend='实线：数据或状态流；虚线：控制、恢复或重复扫描；所有架构均为建议'):
    defs = ''.join(f'<marker id="{name}" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="{color}"/></marker>'
                   for name, color in [('arrow', MUTED), ('arrow-accent', ACCENT), ('arrow-link', '#2e5aa8')])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" role="img" aria-labelledby="{slug}-title {slug}-desc">'
           f'<title id="{slug}-title">{escape(title)}</title><desc id="{slug}-desc">{escape(desc)}</desc>'
           f'<defs>{defs}</defs><rect width="1280" height="720" fill="{PAPER}"/>'
           + text(48, 56, title, 28, INK, 'start', 600)
           + text(48, 88, desc, 16, MUTED, 'start')
           + ''.join(edges)+''.join(nodes)+''.join(notes)
           + '<line x1="48" y1="656" x2="1232" y2="656" stroke="#bfc0c0" stroke-width="0.8"/>'
           + text(48, 688, legend, 12, MUTED, 'start')+'</svg>')
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f'<title>{escape(title)}</title><style>body{{margin:0;background:{PAPER};color:{INK}}}svg{{display:block;width:100%;height:auto;max-width:1280px;margin:auto}}</style>'
            f'<body>{svg}</body></html>')
    html_path = ROOT / f'{slug}.html'
    html_path.write_text(html, encoding='utf-8')
    # HTML 是可编辑源；按图稿原样提取 SVG，不引入在线资源。
    exported = re.search(r'<svg\b.*?</svg>', html_path.read_text(encoding='utf-8'), re.S).group(0)
    (ROOT / f'{slug}.svg').write_text('<?xml version="1.0" encoding="UTF-8"?>\n'+exported, encoding='utf-8')
    print(f'{slug}: HTML + SVG')


def main():
    figure('01-推荐架构', '01 / USB 相机到 HDMI 的推荐架构',
           '保留 USB 输入；检测、跨帧计数和显示分工；DDR 帧按所有权共享。',
           [arrow('M264 208 H384', 'USB', 320, 196),
            arrow('M624 208 H944', 'GRAY8', 784, 196),
            arrow('M1064 256 V352'),
            arrow('M944 408 H896'), arrow('M704 408 H624'),
            arrow('M264 408 H384', 'POSITION', 324, 396, True),
            arrow('M504 464 V528'),
            arrow('M1104 256 V288 Q1104 296 1096 296 H688 Q680 296 680 304 V400 a8 8 0 0 0 0 16 V544 Q680 552 672 552 H624', dashed=True),
            arrow('M624 592 H944', 'RGB VIDEO', 784, 580)],
           [node(64,160,200,96,'AT-OV9281','USB2.0 / MJPEG','外部'),
            node(384,160,240,96,'采集与灰度解码','V4L2 / native decoder','PS'),
            node(944,160,240,96,'灰度帧池','frame_id / ownership','DDR'),
            node(944,352,240,112,'预处理与有界 CCL','MM2S → pixels → objects','PL'),
            node(704,352,192,112,'特征包','S2MM → DDR','PL / DDR'),
            node(384,352,240,112,'关联与计数账本','U / G / D + identity','PS', True),
            node(64,352,200,112,'编码器与同步','接口与曝光对时待验证','外部 / PL'),
            node(384,528,240,96,'VDMA 与 Overlay','命令 + 灰度帧 → RGB','PL'),
            node(944,528,240,96,'HDMI 显示输出','CDC / timing / TMDS','PL / 外部')],
           [text(760,320,'显示读取灰度帧；不回传整幅检测掩膜',12,MUTED),
            text(784,624,'像素域与 HDMI 域经 FIFO 跨时钟；显示独立扫描',12,MUTED)])

    figure('02-帧处理流程','02 / 一帧图像的处理流程',
           '像素流尽量留在 PL；PS 消费帧级目标包；异常观测不能静默当作有效。',
           [arrow('M256 208 H384'), arrow('M576 208 H704'), arrow('M896 208 H1024'),
            arrow('M1120 256 V384'), arrow('M1024 432 H896','VALID',960,420),
            arrow('M704 432 H576'), arrow('M384 432 H256'),
            arrow('M1152 480 V536','INVALID',1200,516,True)],
           [node(64,160,192,96,'曝光与采集','相机内部延迟需实测','外部 / PS'),
            node(384,160,192,96,'解码 / 提取 Y','MJPEG / YUYV','PS'),
            node(704,160,192,96,'灰度与帧解析','DDR → MM2S','DDR / PL'),
            node(1024,160,192,96,'预处理 / CCL','目标 PL；过渡 PL RLE','PL'),
            node(1024,384,192,96,'目标包校验','容量 / 帧号 / CRC','PL / PS'),
            node(704,384,192,96,'轨迹与事件','一对一关联 / 去重','PS',True),
            node(384,384,192,96,'同帧画面合成','image n + result n','PL'),
            node(64,384,192,96,'HDMI 扫描','无新图时重复完整帧','PL / 外部'),
            node(1024,536,192,96,'异常与观测缺口','保持质量标志','PS')],
           [text(464,576,'目标：PL CCL；过渡：PL RLE → PS 原生 CCL；均需校验完整性。',16,MUTED)])

    figure('03-三类数据流','03 / 控制流、视频流与元数据流',
           '三种数据具有不同带宽、缓冲和生效边界，不能由一组控制寄存器承担。',
           [arrow('M256 232 H384','CONFIG',320,220,True),
            arrow('M576 232 H704','NEXT FRAME',640,220,True),
            arrow('M800 280 V400','OBJECTS',856,352,False,True),
            arrow('M704 448 H576'),
            arrow('M480 496 V516 Q480 524 488 524 H680 Q688 524 688 532 V540',dashed=True),
            arrow('M192 400 V344 Q192 336 200 336 H656 Q664 336 664 328 V264 Q664 256 672 256 H704','GRAY8',432,324),
            arrow('M128 496 V504 Q128 512 136 512 H472 a8 8 0 0 1 16 0 H904 Q912 512 912 520 V560 a8 8 0 0 0 0 16 V596 Q912 604 920 604 H928',dashed=True),
            arrow('M784 568 H928','COMMANDS',856,556,True)],
           [node(64,184,192,96,'参数与控制','启动 / 阈值 / ROI','PS'),
            node(384,184,192,96,'影子寄存器','AXI4-Lite / generation','PL'),
            node(704,184,192,96,'像素处理','AXI4-Stream','PL'),
            node(64,400,192,96,'灰度帧池','所有权 / frame_id','DDR'),
            node(384,400,192,96,'轨迹与计数','消费特征 / 发布命令','PS'),
            node(704,400,192,96,'特征缓冲','有界队列 / DMA','DDR',True),
            node(592,444+96,192,96,'绘图命令','ID / 框 / 计数快照','PL'),
            node(928,444+96,288,96,'独立显示扫描','帧边界切换 / 过期标记','PL')],
           [text(1060,216,'视频：整帧或像素流',16,MUTED),
            text(1060,252,'元数据：目标包与命令',16,MUTED),
            text(1060,288,'控制：小数据、帧边界生效',16,MUTED)],
           '实线：视频或元数据（见标识）；虚线：控制、绘图命令或独立显示读取')

    figure('04-计数状态机','04 / 通行状态与一次计数',
           '这里展示已确认轨迹的核心状态；新轨迹确认、退休与墓碑记录详见正文。',
           [arrow('M256 264 H384','OBSERVED U',320,252),
            arrow('M576 264 H704','ENTER G',640,252),
            arrow('M896 264 H1024','REACH D',960,252,False,True),
            arrow('M768 208 V136 Q768 128 760 128 H488 Q480 128 480 136 V208','RETURN U',624,116,True),
            arrow('M768 320 V424','LOST',704,376),
            arrow('M832 424 V320',dashed=True),
            arrow('M704 480 H576','AMBIGUOUS',640,468),
            arrow('M1152 208 V152 Q1152 144 1160 144 H1224 Q1232 144 1232 152 V264 Q1232 272 1224 272 H1216',dashed=True)],
           [node(64,208,192,112,'已确认','尚未建立上游证据','CONFIRMED'),
            node(384,208,192,112,'已准备','在上游区域 U','ARMED'),
            node(704,208,192,112,'正在通行','进入计数带 G','PASSING'),
            node(1024,208,192,112,'已计数','提交唯一事件','COUNTED',True),
            node(704,424,192,112,'暂时丢失','仅唯一恢复才续接','LOST'),
            node(384,424,192,112,'身份不确定','报告异常，不强行补数','AMBIGUOUS')],
           [text(1112,360,'短倒退保持已计数',12,MUTED),
            text(824,568,'向上虚线：身份唯一恢复',12,MUTED),
            text(512,616,'双区域滞回 + 身份保持 + 已计数记录；单个 counted 标志不能保证物理身份不丢失。',16,MUTED)])

    actors=[(64,'相机 / USB'),(320,'PS 解码与计数'),(576,'DDR 帧池'),(832,'PL 检测'),(1088,'PL 显示')]
    nodes=[]
    edges=[]
    for x,name in actors:
        edges.append(f'<path d="M{x+64} 176 V624" stroke="#bfc0c0" fill="none" stroke-dasharray="4,4"/>')
        nodes.append(node(x,112,128,64,name,kind='').replace('font-size="20"','font-size="16"'))
    for path,label,x,y,dashed in [
        ('M128 216 H384','USB FRAME N',256,204,False),
        ('M384 264 H640','WRITE GRAY8',512,252,False),
        ('M384 312 H896','START N',640,300,True),
        ('M640 360 H896','PIXELS N',768,348,False),
        ('M896 408 H384','FEATURES N',640,396,False),
        ('M384 456 H440 Q448 456 448 464 V472 Q448 480 440 480 H384','TRACK / COUNT',552,464,False),
        ('M384 520 H1152','COMMANDS N',768,508,True),
        ('M1152 568 H640','READ FRAME N',896,556,False),
        ('M1152 600 H1216 Q1224 600 1224 608 V616 Q1224 624 1216 624 H1152','VBLANK',1064,612,True),
    ]:
        edges.append(arrow(path,label,x,y,dashed))
    figure('05-完整帧时序','05 / 从采集到同帧显示的概念时序',
           '向下表示时间推进，不按比例；采集、检测与显示可以流水并行。',
           edges,nodes,legend='帧 n 的图像和结果按 frame_id 配对；计数发生于 PS，实际 HDMI 连续输出发生于 PL。')


if __name__ == '__main__':
    main()
