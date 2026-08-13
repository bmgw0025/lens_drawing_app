# -*- coding: utf-8 -*-
"""Lens Drawing Tool v3.5"""
import sys,os,math,io
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42
from matplotlib.figure import Figure
from matplotlib.patches import Polygon, Rectangle
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib import font_manager
import matplotlib.pyplot as plt
from geometry import build_profile
from config import auto_chamfer,auto_CA,auto_N
from datetime import datetime
from batch_import import CementedLensData, SingleLensData


def _cjk_font_properties():
    for family in ("Microsoft YaHei", "SimHei", "SimSun"):
        try:
            font_manager.findfont(family, fallback_to_default=False)
            return font_manager.FontProperties(family=family)
        except ValueError:
            continue
    return font_manager.FontProperties()


_CJK_FONT = _cjk_font_properties()

# ════════════════════════════════════════════════════════════════════════════
# Lane-based 标注布局管理器 —— 解决胶合页多片标注重叠问题
# ════════════════════════════════════════════════════════════════════════════

class AnnotationSlot:
    """一个待布局的标注槽位"""
    __slots__ = ('direction', 'anchor_y_center', 'anchor_y_half_span',
                 'priority', 'draw_fn', 'slot_id', 'assigned_offset', 'attach_x',
                 'offset_scale', 'preferred_offset_J', 'assigned_distance',
                 'assigned_x')
    _counter = 0

    def __init__(self, direction, anchor_y_center, anchor_y_half_span,
                 priority, draw_fn, slot_id=None, attach_x=None,
                 offset_scale=None, preferred_offset_J=None):
        """
        direction: "left" | "right" | "top" | "bottom"
        anchor_y_center: 标注锚点在数据坐标系的 Y 中心
        anchor_y_half_span: 标注在 Y 方向的半跨度（用于碰撞检测）
        priority: 越小越靠近镜片（优先占据内侧 lane）
        draw_fn: callable(offset_J) -> None  实际绘制函数，接收计算后的 offset_J
        slot_id: 可选标识，不提供则自动编号
        attach_x: 标注引线起点的 X 坐标
        offset_scale: draw_fn 中 offset_J 对应的实际长度；默认使用管理器 J
        preferred_offset_J: 相对组装体外边界的首选偏移；默认使用管理器基础偏移
        """
        self.direction = direction
        self.anchor_y_center = anchor_y_center
        self.anchor_y_half_span = anchor_y_half_span
        self.priority = priority
        self.draw_fn = draw_fn
        if slot_id is not None:
            self.slot_id = slot_id
        else:
            AnnotationSlot._counter += 1
            self.slot_id = AnnotationSlot._counter
        self.assigned_offset = None  # 布局后填入
        self.attach_x = attach_x
        self.offset_scale = offset_scale
        self.preferred_offset_J = preferred_offset_J
        self.assigned_distance = None
        self.assigned_x = None


class SideAnnotationManager:
    """侧边标注布局管理器：自动分配 lane 层级避免重叠

    使用方式：
      1. 创建管理器，传入 J 和基础偏移
      2. register() 注册所有标注
      3. layout() 计算各槽位偏移
      4. draw() 执行全部绘制
    """

    def __init__(self, J, base_offset_J, lane_spacing_J=3.0, boundary_x=None):
        """
        J: 当前缩放因子
        base_offset_J: 基础偏移（最近的 lane 到镜片边缘的距离，单位 J）
        lane_spacing_J: 相邻 lane 之间的间距，单位 J
        boundary_x: 组装体在该侧的全局外边界；未提供时从 attach_x 推导
        """
        self.J = J
        self.base_offset_J = base_offset_J
        self.lane_spacing_J = lane_spacing_J
        self.boundary_x = boundary_x
        self.slots = []  # List[AnnotationSlot]

    def register(self, direction, anchor_y_center, anchor_y_half_span,
                 priority, draw_fn, slot_id=None, attach_x=None,
                 offset_scale=None, preferred_offset_J=None):
        """注册一个标注请求"""
        slot = AnnotationSlot(direction, anchor_y_center, anchor_y_half_span,
                              priority, draw_fn, slot_id, attach_x,
                              offset_scale, preferred_offset_J)
        self.slots.append(slot)
        return slot

    def _y_overlap(self, s1, s2):
        """判断两个标注在 Y 方向是否重叠（留 10% 裕量）"""
        margin = max(s1.anchor_y_half_span, s2.anchor_y_half_span) * 0.1
        top1 = s1.anchor_y_center + s1.anchor_y_half_span + margin
        bot1 = s1.anchor_y_center - s1.anchor_y_half_span - margin
        top2 = s2.anchor_y_center + s2.anchor_y_half_span + margin
        bot2 = s2.anchor_y_center - s2.anchor_y_half_span - margin
        return not (top1 <= bot2 or top2 <= bot1)

    def layout(self):
        """为每个 slot 计算 offset_J，返回 {slot_id: offset_J}"""
        # 按 direction 分组
        by_dir = {}
        for s in self.slots:
            by_dir.setdefault(s.direction, []).append(s)

        result = {}
        for direction, group in by_dir.items():
            # 排序：priority 升序（越小越内侧），同 priority 按 span 升序（小跨度在内侧）
            group.sort(key=lambda s: (s.priority, s.anchor_y_half_span, str(s.slot_id)))
            assigned = []

            attach_xs = [s.attach_x for s in group if s.attach_x is not None]
            if self.boundary_x is not None:
                boundary_x = self.boundary_x
            elif attach_xs:
                boundary_x = min(attach_xs) if direction == "left" else max(attach_xs)
            else:
                boundary_x = 0.0

            lane_spacing = self.lane_spacing_J * self.J

            for slot in group:
                overlapping = [a for a in assigned if self._y_overlap(slot, a)]

                preferred = (slot.preferred_offset_J
                             if slot.preferred_offset_J is not None
                             else self.base_offset_J)
                distance = preferred * self.J
                while True:
                    blockers = [a for a in overlapping
                                if abs(distance - a.assigned_distance) < lane_spacing - 1e-12]
                    if not blockers:
                        break
                    distance = max(a.assigned_distance + lane_spacing for a in blockers)

                attach_x = boundary_x if slot.attach_x is None else slot.attach_x
                target_x = (boundary_x - distance
                            if direction == "left"
                            else boundary_x + distance)
                scale = slot.offset_scale if slot.offset_scale is not None else self.J
                if scale <= 0:
                    raise ValueError("标注布局缩放因子必须大于 0")

                slot.assigned_distance = distance
                slot.assigned_x = target_x
                slot.assigned_offset = abs(target_x - attach_x) / scale

                assigned.append(slot)
                result[slot.slot_id] = slot.assigned_offset

        return result

    def draw(self):
        """执行布局并绘制所有标注"""
        offsets = self.layout()
        for slot in self.slots:
            slot.draw_fn(slot.assigned_offset)
        return offsets


# 画带实心三角箭头的引线，箭头尖端在 (x2,y2)，杆从 (x1,y1) 出发
# hs: 箭头大小（三角形边长）
def _arrow(ax,x1,y1,x2,y2,color="black",lw=0.8,hs=5):
    dx,dy=x2-x1,y2-y1; L=math.sqrt(dx*dx+dy*dy)
    if L<1e-9: return
    ux,uy=dx/L,dy/L; px,py=-uy,ux
    tip=[x2,y2]; b1=[x2-hs*ux+hs*0.4*px,y2-hs*uy+hs*0.4*py]; b2=[x2-hs*ux-hs*0.4*px,y2-hs*uy-hs*0.4*py]
    ax.plot([x1,x2],[y1,y2],color=color,lw=lw,zorder=5)
    ax.fill([tip[0],b1[0],b2[0]],[tip[1],b1[1],b2[1]],color=color,zorder=6)

# 矢高计算：s = |R| - sqrt(|R|^2 - (CA/2)^2)，当 CA/2 >= |R| 时 s = |R|
def _sagitta(R,ca):
    ra=abs(R)
    if ra<1e-9: return 0
    y=ca/2.0
    return ra if y>=ra else ra-math.sqrt(ra*ra-y*y)

# 计算边缘厚度 ET，根据镜片类型使用不同公式
# 双凸: ET = CT - s1 - s2  双凹: ET = CT + s1 + s2
# 弯月(R1<0,R2<0): ET = CT + s1 - s2  弯月(R1>0,R2>0): ET = CT + s2 - s1
# R=0 (plano): sagitta=0, 归入双凸/双凹大类
def _calc_et(T,R1,R2,AD1,AD2):
    s1=_sagitta(R1,AD1)
    s2=_sagitta(R2,AD2)
    if R1>=0 and R2<=0: return T-s1-s2
    elif R1<=0 and R2>=0: return T+s1+s2
    elif R1<0 and R2<0: return T+s1-s2
    elif R1>0 and R2>0: return T+s2-s1
    return T


def _dimension_text_with_leader(ax, line_start_x, text_x, y, text,
                                font_size, J, ha="left", direction="right",
                                leader_id=None, fontproperties=None):
    """Draw dimension text and extend its leader beyond the rendered text BBox."""
    artist = ax.text(
        text_x, y + font_size * 0.01, text,
        ha=ha, va="bottom", fontsize=font_size, color="black", zorder=7,
        fontproperties=fontproperties,
    )
    if not getattr(ax, "_dimension_renderer_ready", False):
        canvas = FigureCanvasAgg(ax.figure)
        canvas.draw()
        ax._dimension_canvas = canvas
        ax._dimension_renderer = canvas.get_renderer()
        ax._dimension_renderer_ready = True
    renderer = ax._dimension_renderer
    bbox = artist.get_window_extent(renderer=renderer)
    x0 = ax.transData.inverted().transform((bbox.x0, bbox.y0))[0]
    x1 = ax.transData.inverted().transform((bbox.x1, bbox.y1))[0]
    pad = max(J * 0.35, abs(x1 - x0) * 0.08)
    candidate = x1 + pad if direction == "right" else x0 - pad
    groups = getattr(ax, "_dimension_leader_groups", {})
    group = groups.setdefault(direction, {"target": candidate, "lines": []})
    if direction == "right":
        group["target"] = max(group["target"], candidate)
    else:
        group["target"] = min(group["target"], candidate)
    line_end = group["target"]
    for previous in group["lines"]:
        xdata = list(previous.get_xdata())
        xdata[-1] = line_end
        previous.set_xdata(xdata)
    line, = ax.plot([line_start_x, line_end], [y, y], "k-", lw=0.8, zorder=5)
    group["lines"].append(line)
    groups[direction] = group
    ax._dimension_leader_groups = groups
    if leader_id:
        line.set_gid(f"dimension-leader:{leader_id}")
        artist.set_gid(f"dimension-text:{leader_id}")
    return artist, line

# CT（中心厚度）：从左顶点(0,0)和右顶点(T,0)画垂直线向下延伸，在 offset_J 位置画水平尺寸线
# 箭头分别指向两顶点，文字右对齐标注在右侧，文字底部距水平线 0.01*字号
def _ann_ct(ax,T,J,offset_J,t_tol,font_size,arrow_scale):
    y_ext=-offset_J*J
    text_str=f"{T:.2f}\u00b1{t_tol:.2f}"
    text_x=T+J*2.5
    ax.plot([0,0],[0,y_ext],"k-",lw=0.8,zorder=5)
    ax.plot([T,T],[0,y_ext],"k-",lw=0.8,zorder=5)
    ax.plot([0,T],[y_ext,y_ext],"k-",lw=0.8,zorder=5)
    _dimension_text_with_leader(
        ax, T, text_x, y_ext, text_str, font_size, J,
        ha="left", direction="right", leader_id="ct",
    )
    # 左侧向外延伸0.5J引线
    ax.plot([-J*0.5,0],[y_ext,y_ext],"k-",lw=0.8,zorder=5)
    aw=J*0.6*arrow_scale
    _arrow(ax,-aw,y_ext,0,y_ext,hs=aw)
    _arrow(ax,T+aw,y_ext,T,y_ext,hs=aw)

# ET（边缘厚度）：从左垂直边缘点 C 和右垂直边缘点 F 向上画垂直线，在 offset_J 位置画水平尺寸线
# 文字居中于 C 和 F 中点，底部距水平线 0.01*字号
def _ann_et(ax,C,F,ET,J,offset_J,font_size,arrow_scale,text_x=None):
    y_ext=J*offset_J
    text_str=f"({ET:.2f})"
    text_x=text_x if text_x is not None else (C[0]+F[0])/2.0
    ax.plot([C[0],C[0]],[C[1],y_ext],"k-",lw=0.8,zorder=5)
    ax.plot([F[0],F[0]],[F[1],y_ext],"k-",lw=0.8,zorder=5)
    ax.plot([C[0],F[0]],[y_ext,y_ext],"k-",lw=0.8,zorder=5)
    _dimension_text_with_leader(
        ax, F[0], text_x, y_ext, text_str, font_size, J,
        ha="center", direction="right", leader_id="et",
    )
    # 左侧向外延伸0.5J引线
    ax.plot([C[0],C[0]-J*0.5],[y_ext,y_ext],"k-",lw=0.8,zorder=5)
    aw=J*0.6*arrow_scale
    _arrow(ax,C[0]-aw,y_ext,C[0],y_ext,hs=aw)
    _arrow(ax,F[0]+aw,y_ext,F[0],y_ext,hs=aw)

# Diameter（直径）：从右端点 F 向右水平画线，上下边缘各画一条，垂直封口形成标注框
# 文字使用 LaTeX 堆叠公差格式，上公差和下公差独立设置，垂直旋转90°书写
def _ann_diameter(ax,F_x,MD,J,offset_J,font_size,arrow_scale,dia_tol_upper=0.010,dia_tol_lower=0.025):
    x_ext=F_x+offset_J*J
    ax.plot([F_x,x_ext],[MD/2,MD/2],"k-",lw=0.8,zorder=5)
    ax.plot([F_x,x_ext],[-MD/2,-MD/2],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext],[MD/2,-MD/2],"k-",lw=0.8,zorder=5)
    # 上下延伸0.5J引线 + 向右短横线
    ax.plot([x_ext,x_ext],[MD/2,MD/2+J*0.5],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext],[-MD/2,-MD/2-J*0.5],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext+J*0.4],[MD/2,MD/2],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext+J*0.4],[-MD/2,-MD/2],"k-",lw=0.8,zorder=5)
    aw=J*0.6*arrow_scale
    _arrow(ax,x_ext,MD/2+aw,x_ext,MD/2,hs=aw)
    _arrow(ax,x_ext,-MD/2-aw,x_ext,-MD/2,hs=aw)
    tx=x_ext+J*0.35
    ax.text(tx,0,rf"$\varnothing{MD:.2f}^{{-{dia_tol_upper:.3f}}}_{{-{dia_tol_lower:.3f}}}$",ha="left",va="center",fontsize=font_size,color="black",rotation=90,zorder=7)

# 左侧直径标注：从B点向左画引线，文字在左侧
def _ann_diameter_left(ax,B_x,MD,J,offset_J,font_size,arrow_scale,dia_tol_upper=0.010,dia_tol_lower=0.025):
    x_ext=B_x-offset_J*J
    ax.plot([B_x,x_ext],[MD/2,MD/2],"k-",lw=0.8,zorder=5)
    ax.plot([B_x,x_ext],[-MD/2,-MD/2],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext],[MD/2,-MD/2],"k-",lw=0.8,zorder=5)
    # 上下延伸0.5J引线 + 向左短横线
    ax.plot([x_ext,x_ext],[MD/2,MD/2+J*0.5],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext],[-MD/2,-MD/2-J*0.5],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext-J*0.4,x_ext],[MD/2,MD/2],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext-J*0.4,x_ext],[-MD/2,-MD/2],"k-",lw=0.8,zorder=5)
    aw=J*0.6*arrow_scale
    _arrow(ax,x_ext,MD/2+aw,x_ext,MD/2,hs=aw)
    _arrow(ax,x_ext,-MD/2-aw,x_ext,-MD/2,hs=aw)
    tx=x_ext-J*0.35
    ax.text(tx,0,rf"$\varnothing{MD:.2f}^{{-{dia_tol_upper:.3f}}}_{{-{dia_tol_lower:.3f}}}$",ha="right",va="center",fontsize=font_size,color="black",rotation=90,zorder=7)

# Sag1（左矢高）：正确箭头方向 + 完整内侧连线（最终版）
def _ann_sag1(ax,A,B,Sag1,MD,J,offset_J,font_size,arrow_scale,need_tol,sag_tol=0.02):
    Bp=(B[0],-B[1])
    y_ext=-offset_J*J
    val_str=f"{Sag1:.2f}\u00b1{sag_tol:.2f}" if need_tol else f"({Sag1:.2f})"
    text_x=A[0]-J*2.5
    ax.plot([A[0],A[0]],[0,y_ext],"k-",lw=0.8,zorder=5)
    ax.plot([Bp[0],Bp[0]],[-Bp[1],y_ext],"k-",lw=0.8,zorder=5)

    # 托线从A[0]延伸到text_x，覆盖文字下方
    _dimension_text_with_leader(
        ax, A[0], text_x, y_ext, val_str, font_size, J,
        ha="right", direction="left", leader_id="sag1",
    )

    # 右侧向外延伸0.5J引线（A[0]右侧，无文字侧）
    ax.plot([A[0],A[0]+J*0.5],[y_ext,y_ext],"k-",lw=0.8,zorder=5)

    aw=J*0.6*arrow_scale

    # 箭头指向A和Bp
    _arrow(ax, A[0]+aw, y_ext, A[0], y_ext, hs=aw)
    _arrow(ax, Bp[0]-aw, y_ext, Bp[0], y_ext, hs=aw)

    # 箭头之间的水平连线
    ax.plot([A[0], Bp[0]], [y_ext, y_ext], "k-", lw=0.8, zorder=5)


# Sag2（右矢高）：文字标注在镜片右侧（E' 点右侧），从 D 点和 E' 点（下方对称点）画垂直线
# 在 offset_J 位置画水平尺寸线，箭头分别指向 D 和 E'，若 need_tol 为 True 则显示 ±0.02 公差
def _ann_sag2(ax,D,E,Sag2,MD,J,offset_J,font_size,arrow_scale,need_tol,text_x=None,sag_tol=0.02):
    Ep=(E[0],-E[1])
    y_ext=-offset_J*J
    val_str=f"{Sag2:.2f}\u00b1{sag_tol:.2f}" if need_tol else f"({Sag2:.2f})"
    text_x=text_x if text_x is not None else Ep[0]+J*2.5
    ax.plot([D[0],D[0]],[0,y_ext],"k-",lw=0.8,zorder=5)
    ax.plot([Ep[0],Ep[0]],[-Ep[1],y_ext],"k-",lw=0.8,zorder=5)
    # 托线从D[0]延伸到text_x，覆盖文字下方
    _dimension_text_with_leader(
        ax, D[0], text_x, y_ext, val_str, font_size, J,
        ha="left", direction="right", leader_id="sag2",
    )
    # 左侧向外延伸0.5J引线（D[0]左侧）
    ax.plot([D[0]-J*0.5,D[0]],[y_ext,y_ext],"k-",lw=0.8,zorder=5)
    aw=J*0.6*arrow_scale
    _arrow(ax,D[0]-aw,y_ext,D[0],y_ext,hs=aw)
    _arrow(ax,Ep[0]+aw,y_ext,Ep[0],y_ext,hs=aw)
    # D到Ep之间的水平连线
    ax.plot([D[0], Ep[0]], [y_ext, y_ext], "k-", lw=0.8, zorder=5)

# CA1（左口径）：从左垂直边缘 B 点向左画水平引线，上下边缘各一条，垂直封口
# 口径使用 AD1/2 作为半高（非 MD/2），文字垂直书写在延伸线左侧
def _ann_ad1(ax,Bp,Bn,AD1,J,ad_offset_J,font_size,arrow_scale):
    y_half=AD1/2.0
    x_ext=Bp[0]-ad_offset_J*J
    ax.plot([Bp[0],x_ext],[y_half,y_half],"k-",lw=0.8,zorder=5)
    ax.plot([Bn[0],x_ext],[-y_half,-y_half],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext],[y_half,-y_half],"k-",lw=0.8,zorder=5)
    # 上下延伸0.5J引线 + 向左短横线
    ax.plot([x_ext,x_ext],[y_half,y_half+J*0.5],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext],[-y_half,-y_half-J*0.5],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext-J*0.4,x_ext],[y_half,y_half],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext-J*0.4,x_ext],[-y_half,-y_half],"k-",lw=0.8,zorder=5)
    aw=J*0.6*arrow_scale
    _arrow(ax,x_ext,y_half+aw,x_ext,y_half,hs=aw)
    _arrow(ax,x_ext,-y_half-aw,x_ext,-y_half,hs=aw)
    ax.text(x_ext-J*0.35,0,f"({AD1:.2f})",ha="right",va="center",fontsize=font_size,color="black",rotation=90,zorder=7)


# CA2（右口径）：从右垂直边缘 E 点向右画水平引线，上下边缘各一条，垂直封口
# 逻辑同 CA1，方向相反
def _ann_ad2(ax,Ep,En,AD2,J,ad_offset_J,font_size,arrow_scale):
    y_half=AD2/2.0
    x_ext=Ep[0]+ad_offset_J*J
    ax.plot([Ep[0],x_ext],[y_half,y_half],"k-",lw=0.8,zorder=5)
    ax.plot([En[0],x_ext],[-y_half,-y_half],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext],[y_half,-y_half],"k-",lw=0.8,zorder=5)
    # 上下延伸0.5J引线 + 向右短横线
    ax.plot([x_ext,x_ext],[y_half,y_half+J*0.5],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext],[-y_half,-y_half-J*0.5],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext+J*0.4],[y_half,y_half],"k-",lw=0.8,zorder=5)
    ax.plot([x_ext,x_ext+J*0.4],[-y_half,-y_half],"k-",lw=0.8,zorder=5)
    aw=J*0.6*arrow_scale
    _arrow(ax,x_ext,y_half+aw,x_ext,y_half,hs=aw)
    _arrow(ax,x_ext,-y_half-aw,x_ext,-y_half,hs=aw)
    ax.text(x_ext+J*0.35,0,f"({AD2:.2f})",ha="left",va="center",fontsize=font_size,color="black",rotation=90,zorder=7)


# R1（曲率半径-左表面）：计算左圆弧中点（ArcA 到 ArcB 的弧长中点），箭头尖端精确落在圆弧中点
# r_offset_J 控制箭头尾部与标注起点的距离，文字位于箭头中部上方 0.08*字号 处
# 计算圆弧中点坐标：给定圆心 (cx,cy)、半径 r、弧两端点 p1/p2，返回弧长中点坐标
def _arc_midpoint(cx,cy,r,p1,p2):
    import math
    a1=math.degrees(math.atan2(p1[1]-cy,p1[0]-cx))
    a2=math.degrees(math.atan2(p2[1]-cy,p2[0]-cx))
    delta=a2-a1
    while delta>180: delta-=360
    while delta<-180: delta+=360
    am=a1+delta/2
    return cx+r*math.cos(math.radians(am)), cy+r*math.sin(math.radians(am))

def _ann_r1(ax,B,R1,J,font_size,arrow_scale,r_offset_J,ArcA=None,ArcB=None):
    if ArcA and ArcB and abs(R1) >= 1e-9:
        mx,my=_arc_midpoint(R1,0.0,abs(R1),ArcA,ArcB)
    else:
        mx,my=B[0]/2.0,B[1]/2.0
    aw=J*0.6*arrow_scale
    start_x=mx-J*r_offset_J-aw
    _arrow(ax,start_x,my,mx,my,hs=aw)
    if abs(R1) < 1e-9:
        r1_text = "PLANO"
    else:
        r1_text = f"R{R1:.3f}" if R1 >= 0 else f"-R{abs(R1):.3f}"
    ax.text((start_x+mx)/2.0,my+font_size*0.02,r1_text,ha="center",va="bottom",fontsize=font_size,color="black",zorder=7)

# R2（曲率半径-右表面）：计算右圆弧中点（E 到 D 的弧长中点，使用 cx2 作为圆心）
# r_offset_J 控制箭头尾部与标注起点的距离，文字位于箭头中部上方 0.08*字号 处
def _ann_r2(ax,E,D,R2,J,font_size,arrow_scale,r_offset_J,cx2=None):
    if cx2 is not None and abs(R2) >= 1e-9:
        mx,my=_arc_midpoint(cx2,0.0,abs(R2),E,D)
    else:
        mx,my=(D[0]+E[0])/2.0,E[1]/2.0
    aw=J*0.6*arrow_scale
    start_x=mx+J*r_offset_J+aw
    _arrow(ax,start_x,my,mx,my,hs=aw)
    if abs(R2) < 1e-9:
        r2_text = "PLANO"
    else:
        r2_text = f"R{R2:.3f}" if R2 >= 0 else f"-R{abs(R2):.3f}"
    ax.text((start_x+mx)/2.0,my+font_size*0.02,r2_text,ha="center",va="bottom",fontsize=font_size,color="black",zorder=7)

# 左倒角标注：箭头指向倒角起点 (cx,cy)，文字在左上方水平托线上
def _ann_chamfer_left(ax,cx,cy,value,J,font_size,arrow_scale):
    s=J*1.2; aw=J*0.6*arrow_scale; text_str=f"C{value:.1f}"
    tx=cx-s; ty=cy+s; edx=cx; edy=cy
    _arrow(ax,tx,ty,edx,edy,hs=aw)
    ll=J*2.5
    ax.plot([tx-ll,tx],[ty,ty],"k-",lw=0.8,zorder=5)
    ax.text(tx-ll/2,ty+font_size*0.01,text_str,ha="center",va="bottom",fontsize=font_size,color="black",zorder=7)

# 右倒角标注：箭头指向倒角起点 (fx,fy)，文字在右上方水平托线上
def _ann_chamfer_right(ax,fx,fy,value,J,font_size,arrow_scale):
    s=J*1.2; aw=J*0.6*arrow_scale; text_str=f"C{value:.1f}"
    tx=fx+s; ty=fy+s; edx=fx; edy=fy
    _arrow(ax,tx,ty,edx,edy,hs=aw)
    ll=J*2.5
    ax.plot([tx,tx+ll],[ty,ty],"k-",lw=0.8,zorder=5)
    ax.text(tx+ll/2,ty+font_size*0.01,text_str,ha="center",va="bottom",fontsize=font_size,color="black",zorder=7)

# Spraying（喷漆标注）：SP四边形虚线 + 向下右转折引线标注
# SP.B: (B[0]-0.2J, B[1])，仅B≠C时存在
# SP.C: ①B=C时(C[0], C[1]+0.2J) ②B≠C时(C[0]-0.2J, C[1]+0.2J)
# SP.F: ①E=F时(F[0], F[1]+0.2J) ②E≠F时(F[0]+0.2J, C[1]+0.2J)
# SP.E: (E[0]+0.2J, E[1])，仅E≠F时存在
# 顺序连接：SP.B→SP.C→SP.F→SP.E（跳过不存在的点）
def _calc_spray_points(B, C, E, F, dy_off):
    """计算单片喷漆线的四个角点（正Y轴）。
    返回 (points, bc_exists, fe_exists)，其中 points = [SP.B, SP.C, SP.F, SP.E]（None表示不存在）
    """
    bc_exists = abs(B[1]-C[1]) > 1e-9
    fe_exists = abs(F[1]-E[1]) > 1e-9
    sp_b = (B[0]-dy_off, B[1]) if bc_exists else None
    if not bc_exists:
        sp_c = (C[0], C[1]+dy_off)
    else:
        sp_c = (C[0]-dy_off, C[1]+dy_off)
    if not fe_exists:
        sp_f = (F[0], F[1]+dy_off)
    else:
        sp_f = (F[0]+dy_off, F[1]+dy_off)
    sp_e = (E[0]+dy_off, E[1]) if fe_exists else None
    return [sp_b, sp_c, sp_f, sp_e], bc_exists, fe_exists

# 转折引线：向下右转折，spraying文字在水平段上方
def _ann_spraying(ax,B,C,E,F,MD,J,spray_gap_J,font_size,arrow_scale):
    dy_off=0.2*J
    aw=J*0.6*arrow_scale
    sp_pts, bc_exists, fe_exists = _calc_spray_points(B, C, E, F, dy_off)
    sp_b, sp_c, sp_f, sp_e = sp_pts
    # ---- 负Y轴 SP 四点（镜像） ----
    ms_b = (B[0]-dy_off, -B[1]) if bc_exists else None
    ms_c = (sp_c[0], -sp_c[1])
    ms_f = (sp_f[0], -sp_f[1])
    ms_e = (E[0]+dy_off, -E[1]) if fe_exists else None
    # ---- 绘制虚线：正Y轴 SP.B→SP.C→SP.F→SP.E ----
    segs = [sp_b, sp_c, sp_f, sp_e]
    neg_segs = [ms_b, ms_c, ms_f, ms_e]
    for i in range(len(segs)-1):
        p0 = segs[i]; p1 = segs[i+1]
        if p0 is not None and p1 is not None:
            ax.plot([p0[0],p1[0]],[p0[1],p1[1]],"--",lw=0.5,zorder=3,color="gray")
        n0 = neg_segs[i]; n1 = neg_segs[i+1]
        if n0 is not None and n1 is not None:
            ax.plot([n0[0],n1[0]],[n0[1],n1[1]],"--",lw=0.5,zorder=3,color="gray")
    # ---- 转折引线（向下右转折）：箭头尖端指向负Y轴 SP.C'-SP.F' 水平段中点 ----
    mid_x = (ms_c[0]+ms_f[0])/2.0
    mid_y = ms_c[1]
    arr_sx = mid_x+J*1.2
    arr_sy = mid_y-J*1.2
    _arrow(ax, arr_sx, arr_sy, mid_x, mid_y, hs=aw)
    ax.plot([arr_sx, arr_sx+5.0*J], [arr_sy, arr_sy], "k-", lw=0.7, zorder=5)
    ax.text(arr_sx+2.5*J, arr_sy+font_size*0.01, "spraying",
            ha="center", va="bottom", fontsize=font_size-2, color="black", zorder=7)

# 中心光轴标注：连接AD的水平横线，两端延伸3J，左侧"⨁S1"，右侧"S2⨁"
# "⨁"为"S1"/"S2"的两倍大小
def _ann_optical_axis(ax,A,D,J,font_size,show_s1_symbol=True,show_s2_symbol=True):
    # AD 连线（光轴线）
    ax.plot([A[0],D[0]],[0,0],"k-",lw=0.8,zorder=10)
    # 两端延伸 3J 作为托线
    left_x=A[0]-J*3.0; right_x=D[0]+J*3.0
    ax.plot([left_x,A[0]],[0,0],"k-",lw=0.8,zorder=10)
    ax.plot([D[0],right_x],[0,0],"k-",lw=0.8,zorder=10)
    # 文字基线统一在光轴线上方 J*0.15 处
    text_y=J*0.15
    big_fs=font_size*1.5
    # 左侧 "⨁S1"：⨁ 往左 0.75J，S1 在 ⨁ 右侧 0.5J
    big_x_left = left_x + J*0.75
    if show_s1_symbol:
        ax.text(big_x_left, text_y, "⨁", ha="center", va="bottom", fontsize=big_fs, color="black", zorder=12)
    ax.text(big_x_left + J*0.65, text_y, "S1", ha="left", va="bottom", fontsize=font_size, color="black", zorder=12)
    # 右侧 "S2⨁"：⨁ 往右 0.75J，S2 在 ⨁ 左侧
    big_x_right = right_x - J*0.75
    ax.text(big_x_right - J*0.65, text_y, "S2", ha="right", va="bottom", fontsize=font_size, color="black", zorder=12)
    if show_s2_symbol:
        ax.text(big_x_right, text_y, "⨁", ha="center", va="bottom", fontsize=big_fs, color="black", zorder=12)

# 主绘图函数：构建透镜剖面轮廓，绘制镜像填充，调用各标注函数完成全部尺寸标注
def draw_lens(ax,T,R1,R2,MD,AD1,AD2,
              J_mult,ct_offset_J,et_offset_J,sag_offset_J,dia_offset_J,ad_offset_J,spray_gap_J,
              chamfer_left,chamfer_right,t_tol,sag_tol,font_size,arrow_scale,r_offset_J=0.8,
              dia_tol_upper=0.010,dia_tol_lower=0.025):
    ax.clear(); J=MD*J_mult
    ax._dimension_renderer_ready = False
    ax._dimension_leader_groups = {}
    profile,pts=build_profile(T,R1,R2,MD,AD1,AD2)
    A,B,C,D,E,F=pts["A"],pts["B"],pts["C"],pts["D"],pts["E"],pts["F"]
    xa,ya=[],[]
    for seg in profile:
        if seg[0] in("arc","line"): xa.extend(seg[1]); ya.extend(seg[2])
    xa.extend([-v for v in xa]); ya.extend([-v for v in ya])
    xm=J*5
    ax.set_xlim(min(xa)-xm-J*8,max(xa)+xm+J*16)
    ax.set_ylim(min(ya)-J*14,max(ya)+J*12)
    ax.set_aspect("equal"); ax.axis("off")
    ux,uy=[],[]
    for seg in profile:
        if seg[0]=="arc": ux.extend(seg[1]); uy.extend(seg[2])
        elif seg[0]=="line": ux.extend(seg[1]); uy.extend(seg[2])
    lx=list(reversed(ux)); ly=[-v for v in reversed(uy)]
    px=ux+lx+[ux[0]]; py=uy+ly+[uy[0]]
    ax.add_patch(Polygon(list(zip(px,py)),closed=True,facecolor="#E8E8E8",edgecolor="none",hatch="//",linewidth=0.4,zorder=1))
    lw=1.2
    for seg in profile:
        if seg[0]=="arc":
            ax.plot(seg[1],seg[2],"k-",lw=lw,zorder=3)
            ax.plot(seg[1],[-v for v in seg[2]],"k-",lw=lw,zorder=3)
        elif seg[0]=="line":
            ax.plot(seg[1],seg[2],"k-",lw=lw,zorder=3)
            ax.plot(seg[1],[-v for v in seg[2]],"k-",lw=lw,zorder=3)
    ax.plot(A[0],A[1],"k.",markersize=4,zorder=4)
    ax.plot(D[0],D[1],"k.",markersize=4,zorder=4)
    _ann_ct(ax,T,J,ct_offset_J,t_tol,font_size,arrow_scale)
    ET=_calc_et(T,R1,R2,AD1,AD2)
    ct_text_x=T+J*2.5
    _ann_et(ax,C,F,ET,J,et_offset_J,font_size,arrow_scale,text_x=ct_text_x)

    # ── Lane-based 标注布局：直径 + AD（自动避免重叠） ──
    mgr_left = SideAnnotationManager(J, dia_offset_J, lane_spacing_J=3.0,
                                     boundary_x=min(ux))
    mgr_right = SideAnnotationManager(J, dia_offset_J, lane_spacing_J=3.0,
                                      boundary_x=max(ux))

    # 右侧直径标注（F点向右）
    mgr_right.register(
        direction="right",
        anchor_y_center=0.0,
        anchor_y_half_span=MD/2.0,
        priority=1,  # 直径标注在外侧
        draw_fn=lambda offset_J, _ax=ax, _Fx=F[0], _MD=MD, _J=J, _fs=font_size, _as=arrow_scale, _dtu=dia_tol_upper, _dtd=dia_tol_lower:
            _ann_diameter(_ax, _Fx, _MD, _J, offset_J, _fs, _as, _dtu, _dtd),
        slot_id="dia_R",
        attach_x=F[0],
        offset_scale=J,
        preferred_offset_J=dia_offset_J
    )

    Sag1=abs(B[0]-A[0]); ad1=(R1<0 and abs(AD1-MD)>0.01)
    Sag2=abs(E[0]-D[0]); ad2=(R2>0 and abs(AD2-MD)>0.01)

    # 左侧 AD1 标注
    if R1<0 and ad1:
        mgr_left.register(
            direction="left",
            anchor_y_center=0.0,
            anchor_y_half_span=AD1/2.0,
            priority=0,  # AD标注在内侧
            draw_fn=lambda offset_J, _ax=ax, _Bp=(B[0],MD/2), _Bn=(B[0],-MD/2), _AD1=AD1, _J=J, _fs=font_size, _as=arrow_scale:
                _ann_ad1(_ax, _Bp, _Bn, _AD1, _J, offset_J, _fs, _as),
            slot_id="ad1_L",
            attach_x=B[0],
            offset_scale=J,
            preferred_offset_J=ad_offset_J
        )

    # 右侧 AD2 标注
    if R2>0 and ad2:
        mgr_right.register(
            direction="right",
            anchor_y_center=0.0,
            anchor_y_half_span=AD2/2.0,
            priority=0,  # AD标注在内侧
            draw_fn=lambda offset_J, _ax=ax, _Ep=(E[0],MD/2), _En=(E[0],-MD/2), _AD2=AD2, _J=J, _fs=font_size, _as=arrow_scale:
                _ann_ad2(_ax, _Ep, _En, _AD2, _J, offset_J, _fs, _as),
            slot_id="ad2_R",
            attach_x=E[0],
            offset_scale=J,
            preferred_offset_J=ad_offset_J
        )

    # 统一布局并绘制左右侧标注
    mgr_left.draw()
    mgr_right.draw()

    # 矢高标注（下方，不参与侧边 Lane 管理）
    # Sag1 抬高到与 spraying 文字同一水平（offset 减少 1.5J）
    sag1_offset_J = max(3.0, sag_offset_J - 1.5)
    if R1<0:
        _ann_sag1(ax,A,B,Sag1,MD,J,sag1_offset_J,font_size,arrow_scale,Sag1>=0.15 and ad1,sag_tol)
    if R2>0:
        _ann_sag2(ax,D,E,Sag2,MD,J,sag_offset_J,font_size,arrow_scale,Sag2>=0.15 and ad2,text_x=ct_text_x,sag_tol=sag_tol)

    cx2=float(T)+R2; _ann_r1(ax,B,R1,J,font_size,arrow_scale,r_offset_J,ArcA=A,ArcB=B)
    _ann_r2(ax,E,D,R2,J,font_size,arrow_scale,r_offset_J,cx2=cx2)
    _ann_chamfer_left(ax,B[0],MD/2,chamfer_left,J,font_size,arrow_scale)
    _ann_chamfer_right(ax,E[0],MD/2,chamfer_right,J,font_size,arrow_scale)
    _ann_spraying(ax,B,C,E,F,MD,J,spray_gap_J,font_size,arrow_scale)
    _ann_optical_axis(ax,A,D,J,font_size)

# ── 单页 Figure 构建函数（从 export_pdf 提取，供批量多页复用） ──────────
def _build_single_page_figure(T,R1,R2,MD,AD1,AD2,
               J_mult,ct_offset_J,et_offset_J,sag_offset_J,dia_offset_J,ad_offset_J,spray_gap_J,
               chamfer_left,chamfer_right,t_tol,sag_tol,font_size,arrow_scale,r_offset_J,
               dia_tol_upper=0.010,dia_tol_lower=0.025,
               proc_params=None,settings=None,ca1=None,ca2=None,
               page_no=None,total_pages=None,hide_partname=False):
    from matplotlib.figure import Figure
    from matplotlib.patches import Polygon
    import matplotlib.pyplot as plt

    # ====================== 计算自动变量 ======================
    _ca_ratio = settings.get("ca_ratio", 0.94) if settings else 0.94
    CA1=ca1 if ca1 is not None else auto_CA(AD1, _ca_ratio)
    CA2=ca2 if ca2 is not None else auto_CA(AD2, _ca_ratio)
    today=datetime.now().strftime("%Y.%m.%d")
    if proc_params is None: proc_params={}
    if settings is None: settings={}
    def s(canonical_key, default, legacy_key=None):
        if canonical_key in proc_params:
            return proc_params[canonical_key]
        if legacy_key and legacy_key in proc_params:
            return proc_params[legacy_key]
        return settings.get(canonical_key, default)
    # N: mode-based (auto_N or manual)
    N_mode = s("proc_N_mode", "auto", "N_mode")
    if N_mode == "manual":
        N_val = s("proc_N_manual", "1.5", "N_manual")
    else:
        N_val = auto_N(MD)
    # 各变量取值
    var_c  = s("proc_c_single", "60″")
    var_b  = s("proc_surface_defect", "60/40", "proc_b")
    var_sig= s("proc_signature", "l.y.h", "signature")
    var_pn = "" if hide_partname else proc_params.get("part_name","singlelen")
    var_pno= proc_params.get("part_no","100.2.00888")
    var_gn = proc_params.get("glass_name","H-K9L")
    var_vnd= s("proc_vendor", "CDGM")
    var_rnk= s("proc_ranking","01")
    var_molding = s("proc_molding", "Molding")
    # 镀膜波段
    w_s1_1=proc_params.get("coat_s1_wave1","420-680")
    w_s1_2=proc_params.get("coat_s1_wave2","680-850")
    w_s2_1=proc_params.get("coat_s2_wave1","420-680")
    w_s2_2=proc_params.get("coat_s2_wave2","680-850")
    # 反射率
    r_s1_1=proc_params.get("coat_s1_ravg1","0.4")
    r_s1_2=proc_params.get("coat_s1_ravg2","0.8")
    r_s2_1=proc_params.get("coat_s2_ravg1","0.4")
    r_s2_2=proc_params.get("coat_s2_ravg2","0.8")
    # 角度
    a_s1_1=proc_params.get("coat_s1_angle1","0-15")
    a_s1_2=proc_params.get("coat_s1_angle2","0-15")
    a_s2_1=proc_params.get("coat_s2_angle1","0-15")
    a_s2_2=proc_params.get("coat_s2_angle2","0-15")

    # ====================== 页面初始化 ======================
    fig = Figure(figsize=(11.69, 8.27), dpi=300)  # 11.69*100=1169, 8.27*100=827, closest integer pixels to A4 ratio

    # ---- 页面坐标系 axes（毫米，用于图框/文字/表格）----
    ax_page = fig.add_axes([0, 0, 1, 1])
    ax_page.set_xlim(0, 297)
    ax_page.set_ylim(0, 210)
    ax_page.set_aspect('equal')
    ax_page.axis('off')

    # ---- 绘制外框 ----
    margin_l, margin_r, margin_t, margin_b = 10, 5, 5, 5
    frame_x0, frame_x1 = margin_l, 297 - margin_r
    frame_y0, frame_y1 = margin_b, 210 - margin_t
    ax_page.plot([frame_x0, frame_x1, frame_x1, frame_x0, frame_x0],
                 [frame_y0, frame_y0, frame_y1, frame_y1, frame_y0], "k-", lw=1.2)

    # ---- 左侧文字说明区（左上角） ----
    is_cemented_single = proc_params.get("is_cemented_single", False) if proc_params else False
    tx_x, tx_y = 18, 195
    line_h = 3.0
    fs = 7.5
    def _tag_field_region(field_id, x0, y0, x1, y1):
        region = Rectangle(
            (x0, y0), x1-x0, y1-y0,
            facecolor="none", edgecolor="none", zorder=10,
        )
        region._field_id = field_id
        region._field_region = True
        ax_page.add_patch(region)
        return region

    def _tx(dx, dy, text, bold=False, f=fs):
        w = "bold" if bold else "normal"
        return ax_page.text(tx_x+dx, tx_y+dy, text, ha="left", va="bottom", fontsize=f, color="black", fontweight=w)
    def _tx_pair(dx, dy, label, value, field_id=None, field_width=38):
        _tx(dx, dy, label, f=fs)
        value_x = tx_x + dx + 42
        value_y = tx_y + dy
        t = ax_page.text(value_x, value_y, value, ha="left", va="bottom", fontsize=fs, color="black")
        if field_id:
            t._field_id = field_id
            _tag_field_region(
                field_id,
                value_x - 0.8, value_y - 0.65,
                value_x + field_width, value_y + line_h - 0.35,
            )

    if is_cemented_single:
        # 胶合单片页：无Spraying段，序号重编
        _tx(0, 0, "1.Material", bold=True)
        _tx_pair(4, -line_h, "Vendor/Brand:", var_vnd, field_id="vendor", field_width=36)
        _tx_pair(4, -line_h*2, "Ranking:", var_rnk, field_id="ranking", field_width=52)
        _tx_pair(4, -line_h*3, "Scribe&Break/Molding:", var_molding, field_id="molding", field_width=44)
        _tx(0, -line_h*4.5, "2.Sample accuracy", bold=True)
        _tx_pair(4, -line_h*5.5, "ΔR:", "A")
        _tx(0, -line_h*6.5, "3.Processing", bold=True)
        _tx_pair(4, -line_h*7.5, "Chamfer:", f"{chamfer_left:.1f}", field_id="chamfer", field_width=18)
        _tx_pair(4, -line_h*8.5, "Chipping:", "0.2")
        _tx(4+42, -line_h*9.5, "Clear Aperture", f=fs)  # label only
        t_ca1 = _tx(4+42+28, -line_h*9.5, f"S1 φ{CA1:.2f}", f=fs)
        t_ca1._field_id = "ca1"
        t_ca1._field_value = f"{CA1:.2f}"
        _tag_field_region("ca1", 91.2, tx_y-line_h*9.5-0.65, 112.5, tx_y-line_h*8.5-0.35)
        t_ca2 = _tx(4+42+52, -line_h*9.5, f"S2 φ{CA2:.2f}", f=fs)
        t_ca2._field_id = "ca2"
        t_ca2._field_value = f"{CA2:.2f}"
        _tag_field_region("ca2", 115.2, tx_y-line_h*9.5-0.65, 136.5, tx_y-line_h*8.5-0.35)
        _tx(0, -line_h*11, "4.The rest", bold=True)
        _tx(4, -line_h*12, "roughness", f=fs)
        tri_x, tri_y = tx_x+10, tx_y-line_h*14; tri_w = 3; tri_h = tri_w * 1.732
    else:
        # 独立单片页：完整5段
        _tx(0, 0, "1.Material", bold=True)
        _tx_pair(4, -line_h, "Vendor/Brand:", var_vnd, field_id="vendor", field_width=36)
        _tx_pair(4, -line_h*2, "Ranking:", var_rnk, field_id="ranking", field_width=52)
        _tx_pair(4, -line_h*3, "Scribe&Break/Molding:", var_molding, field_id="molding", field_width=44)
        _tx(0, -line_h*4.5, "2.Sample accuracy", bold=True)
        _tx_pair(4, -line_h*5.5, "ΔR:", "A")
        _tx(0, -line_h*6.5, "3.Processing", bold=True)
        _tx_pair(4, -line_h*7.5, "Chamfer:", f"{chamfer_left:.1f}", field_id="chamfer", field_width=18)
        _tx_pair(4, -line_h*8.5, "Chipping:", "0.2")
        _tx(4+42, -line_h*9.5, "Clear Aperture", f=fs)  # label only
        t_ca1 = _tx(4+42+28, -line_h*9.5, f"S1 φ{CA1:.2f}", f=fs)
        t_ca1._field_id = "ca1"
        t_ca1._field_value = f"{CA1:.2f}"
        _tag_field_region("ca1", 91.2, tx_y-line_h*9.5-0.65, 112.5, tx_y-line_h*8.5-0.35)
        t_ca2 = _tx(4+42+52, -line_h*9.5, f"S2 φ{CA2:.2f}", f=fs)
        t_ca2._field_id = "ca2"
        t_ca2._field_value = f"{CA2:.2f}"
        _tag_field_region("ca2", 115.2, tx_y-line_h*9.5-0.65, 136.5, tx_y-line_h*8.5-0.35)
        _tx(0, -line_h*11, "4.Spraying", bold=True)
        _tx_pair(4, -line_h*12, "Ink Brand&Model:", "GT-7II")
        _tx_pair(4, -line_h*13, "Ink Proportion:", "8: 1: 9(Paint: Curing agent: Diluent)")
        _tx_pair(4, -line_h*14, "Thickness:", "3~5um")
        _tx_pair(4, -line_h*15, "Spraying position:", "Arrow indication The dashed line")
        _tx_pair(4, -line_h*16, "Dimensions:", "According to the drawing")
        _tx_pair(4, -line_h*17, "Ink over spray/Light Leakage:", "0.1")
        _tx(0, -line_h*18.5, "5.The rest", bold=True)
        _tx(4, -line_h*19.5, "roughness", f=fs)
        tri_x, tri_y = tx_x+10, tx_y-line_h*21.5; tri_w = 3; tri_h = tri_w * 1.732
    # 粗糙度符号（标准工程符号：等边倒三角+水平线+斜线+0.01在水平线上方居中）
    if is_cemented_single:
        tri_x, tri_y = tx_x+10, tx_y-line_h*14
    else:
        tri_x, tri_y = tx_x+10, tx_y-line_h*21.5
    tri_w = 3       # 三角形底边半宽（缩小一倍）
    tri_h = tri_w * 1.732  # 等边三角形高
    # 倒三角形（顶点向下，底边水平在上）
    ax_page.plot([tri_x-tri_w, tri_x+tri_w, tri_x, tri_x-tri_w],
                 [tri_y, tri_y, tri_y-tri_h, tri_y], "k-", lw=0.8)
    # 三角形上边水平线（与三角形上边重合）
    ax_page.plot([tri_x-tri_w, tri_x+tri_w], [tri_y, tri_y], "k-", lw=0.8)
    # 延伸斜线：与三角形右边共线（斜率=tri_h/tri_w=1.732），向右上延伸一个边长(2*tri_w)
    ax_page.plot([tri_x+tri_w, tri_x+tri_w*2], [tri_y, tri_y+tri_h], "k-", lw=0.8)
    # 0.01数值在水平线上方居中，确保不与roughness文字重叠
    ax_page.text(tri_x, tri_y+1.0, "0.01", ha="center", va="bottom", fontsize=fs, color="black")

    # ---- 底部大表格 ----
    # 表格区域：x=[18, 287], y=[10, 45]
    tbl_x0, tbl_y0 = 18, 10
    tbl_x1, tbl_y1 = 287, 45
    tbl_w = tbl_x1 - tbl_x0
    # 列宽定义（共12列），总和 = 269
    cols = [22, 22, 18, 28, 20, 20, 22, 20, 22, 28, 28, 19]
    cum = [tbl_x0]
    for c in cols:
        cum.append(cum[-1] + c)
    # 行高：6行（4数据 + 2表头），每行约5.833mm，总高35mm
    # 删除row7和row8后，保持底部位置不变
    row_h = 35.0 / 6
    rows_y = [tbl_y0 + i * row_h for i in range(7)]  # 7条横线 = 6行区域

    # 绘制外框和横线
    for i, y in enumerate(rows_y):
        if i == 1:
            # rows_y[1] 在 PartNo/PartName 列(cum[9]~cum[11])不画（合并row1和row0）
            # 同时在 Surface 列(cum[2]~cum[3])不画（合并S2的两个单元格）
            ax_page.plot([tbl_x0, cum[2]], [y, y], "k-", lw=0.6)
            ax_page.plot([cum[3], cum[9]], [y, y], "k-", lw=0.6)
            ax_page.plot([cum[11], tbl_x1], [y, y], "k-", lw=0.6)
        elif i == 3:
            # rows_y[3] 在 Project~Version 列(cum[6]~cum[12])不画（合并row3和row2，Drafting行）
            # 同时在 Surface 列(cum[2]~cum[3])不画（合并S1：row3+row2）
            ax_page.plot([tbl_x0, cum[2]], [y, y], "k-", lw=0.6)
            ax_page.plot([cum[3], cum[6]], [y, y], "k-", lw=0.6)
            ax_page.plot([cum[12], tbl_x1], [y, y], "k-", lw=0.6)
        elif i == 5:
            # rows_y[5] 在 Special technical requirement 列(cum[0]~cum[2])不画
            # 在 Project~Version 列(cum[6]~cum[12])不画（合并row5和row4，header行）
            ax_page.plot([cum[2], cum[6]], [y, y], "k-", lw=0.6)
        else:
            ax_page.plot([tbl_x0, tbl_x1], [y, y], "k-", lw=0.6)

    # 绘制竖线（分段，避免在合并单元格中出现多余竖线）
    # 数据行区域竖线（tbl_y0 ~ rows_y[3]）：所有列边界
    for x in cum:
        ax_page.plot([x, x], [tbl_y0, rows_y[3]], "k-", lw=0.6)
    # row3(C行)竖线：Special tech req两列有独立内容(C和60°)，画cum[1]；Coating Position有S1数据，画cum[2]；右侧列保留内部竖线
    for x in [cum[0], cum[1], cum[2], cum[3], cum[4], cum[5], cum[6], cum[7], cum[8], cum[9], cum[10], cum[11], cum[12]]:
        ax_page.plot([x, x], [rows_y[3], rows_y[4]], "k-", lw=0.6)
    # row2(N行)竖线：Special tech req两列合并，不画cum[1]；surface列S1合并，保留边界cum[2]；右侧列保留内部竖线
    for x in [cum[0], cum[2], cum[3], cum[4], cum[5], cum[6], cum[7], cum[8], cum[9], cum[10], cum[11], cum[12]]:
        ax_page.plot([x, x], [rows_y[2], rows_y[3]], "k-", lw=0.6)
    # row1(ΔN行)竖线：Special tech req两列合并，不画cum[1]；surface列S2合并，保留边界cum[2]；cum9-cum10与row0合并，只画边界cum[9],cum[11]
    for x in [cum[0], cum[2], cum[3], cum[4], cum[5], cum[6], cum[7], cum[8], cum[9], cum[11], cum[12]]:
        ax_page.plot([x, x], [rows_y[1], rows_y[2]], "k-", lw=0.6)
    # row0(B行)竖线：Special tech req两列合并，不画cum[1]；surface列S2合并，保留边界cum[2]；cum9-cum10与row1合并，只画边界cum[9],cum[11]
    for x in [cum[0], cum[2], cum[3], cum[4], cum[5], cum[6], cum[7], cum[8], cum[9], cum[11], cum[12]]:
        ax_page.plot([x, x], [rows_y[0], rows_y[1]], "k-", lw=0.6)
    # 子标题行竖线（rows_y[4] ~ rows_y[5]）：除 cum[1] 外，右侧列保留内部竖线
    for x in [cum[0], cum[2], cum[3], cum[4], cum[5], cum[6], cum[7], cum[8], cum[9], cum[10], cum[11], cum[12]]:
        ax_page.plot([x, x], [rows_y[4], rows_y[5]], "k-", lw=0.6)
    # 合并标题行竖线（rows_y[5] ~ rows_y[6]）：Special tech req只画边界，右侧列保留内部竖线
    for x in [cum[0], cum[2], cum[6], cum[7], cum[8], cum[9], cum[10], cum[11], cum[12]]:
        ax_page.plot([x, x], [rows_y[5], rows_y[6]], "k-", lw=0.6)

    # 表头合并标题行
    merge_y = (rows_y[5] + rows_y[6]) / 2
    header_y = (rows_y[4] + rows_y[6]) / 2
    ax_page.text((cum[0]+cum[2])/2, header_y, "Special technical requirement", ha="center", va="center", fontsize=6.5, color="black")
    ax_page.text((cum[2]+cum[6])/2, merge_y, "Coating Position\u2295", ha="center", va="center", fontsize=6.5, color="black")
    ax_page.text((cum[6]+cum[7])/2, header_y, "Project", ha="center", va="center", fontsize=6.5, color="black")
    ax_page.text((cum[7]+cum[8])/2, header_y, "Signature", ha="center", va="center", fontsize=6.5, color="black")
    ax_page.text((cum[8]+cum[9])/2, header_y, "Date", ha="center", va="center", fontsize=6.5, color="black")
    ax_page.text((cum[9]+cum[10])/2, header_y, "Part No.", ha="center", va="center", fontsize=6.5, color="black")
    ax_page.text((cum[10]+cum[11])/2, header_y, "Part Name", ha="center", va="center", fontsize=6.5, color="black")
    ax_page.text((cum[11]+cum[12])/2, header_y, "Version", ha="center", va="center", fontsize=6.5, color="black")

    # 子标题行（row4: [rows_y[4], rows_y[5]]）
    sub_y = (rows_y[4] + rows_y[5]) / 2
    sub_hdrs = [("Surface",(cum[2]+cum[3])/2),("Wavelength(nm)",(cum[3]+cum[4])/2),
                ("Ravg(%)",(cum[4]+cum[5])/2),("Angle(°)",(cum[5]+cum[6])/2)]
    for txt, x in sub_hdrs:
        ax_page.text(x, sub_y, txt, ha="center", va="center", fontsize=6, color="black")

    # 数据行（从 rows_y[5] 往下到 rows_y[0]）
    def _cell(row_idx, col_idx, text, ha="center", f=6.5, field_id=None):
        x = (cum[col_idx] + cum[col_idx+1]) / 2
        y = rows_y[row_idx] + row_h/2 - 0.5
        t = ax_page.text(x, y, text, ha=ha, va="center", fontsize=f, color="black")
        if field_id:
            t._field_id = field_id
            _tag_field_region(
                field_id,
                cum[col_idx], rows_y[row_idx],
                cum[col_idx+1], rows_y[row_idx+1],
            )
    def _cellL(row_idx, col_idx, text, f=6.5):
        x = cum[col_idx] + 1.5
        y = rows_y[row_idx] + row_h/2 - 0.5
        ax_page.text(x, y, text, ha="left", va="center", fontsize=f, color="black")
    def _cell_merge(row_idx, col_idx, text, f=6.5, field_id=None):
        """合并单元格文本：垂直居中于row_idx和row_idx+1两个行区域之间"""
        x = (cum[col_idx] + cum[col_idx+1]) / 2
        y = (rows_y[row_idx] + rows_y[row_idx+2]) / 2 - 0.5
        t = ax_page.text(x, y, text, ha="center", va="center", fontsize=f, color="black", linespacing=0.85)
        if field_id:
            t._field_id = field_id
            _tag_field_region(
                field_id,
                cum[col_idx], rows_y[row_idx],
                cum[col_idx+1], rows_y[row_idx+2],
            )

    # Coating Preset 判断
    coat_preset = (proc_params or {}).get("coat_preset", settings.get("coat_preset", "Custom") if settings else "Custom")
    has_outer_s1 = proc_params.get("has_outer_s1", True) if proc_params else True
    has_outer_s2 = proc_params.get("has_outer_s2", True) if proc_params else True
    sapphire_s1 = bool(proc_params.get("sapphire_s1", False)) if proc_params else False
    sapphire_s2 = bool(proc_params.get("sapphire_s2", False)) if proc_params else False

    def _coating_region(row_start, row_end):
        rx0, ry0 = cum[3], rows_y[row_start]
        rx1, ry1 = cum[6], rows_y[row_end]
        return rx0, ry0, rx1, ry1

    def _tag_coating_selector(row_start, row_end, field_id, value, surface_key):
        rx0, ry0, rx1, ry1 = _coating_region(row_start, row_end)
        region = Rectangle(
            (rx0, ry0), rx1-rx0, ry1-ry0,
            facecolor="none", edgecolor="none", zorder=5,
        )
        region._field_id = field_id
        region._field_value = value
        region._field_kind = "select"
        region._field_options = [
            {"value": "", "label": "空白"},
            {"value": "蓝宝石膜", "label": "蓝宝石膜"},
        ]
        region._surface_key = surface_key
        ax_page.add_patch(region)

    def _merge_coating_value(row_start, row_end, text, fontproperties=None):
        rx0, ry0, rx1, ry1 = _coating_region(row_start, row_end)
        ax_page.add_patch(Rectangle(
            (rx0, ry0), rx1-rx0, ry1-ry0,
            facecolor="white", edgecolor="none", zorder=3,
        ))
        ax_page.plot(
            [rx0, rx1, rx1, rx0, rx0], [ry0, ry0, ry1, ry1, ry0],
            "k-", lw=0.6, zorder=3,
        )
        if text:
            ax_page.text(
                (rx0+rx1)/2, (ry0+ry1)/2, text,
                ha="center", va="center", fontsize=6.5, color="black", zorder=4,
                fontproperties=fontproperties,
            )

    # 若使用Preset，先画合并单元格覆盖内部小线（只合并Wavelength/Ravg/Angle，保留左侧Surface列）
    # 无论是否有镀膜，都画合并单元格（保持视觉一致），仅在需要镀膜时填充文字
    if coat_preset != "Custom":
        # S1
        _merge_coating_value(2, 4, coat_preset if has_outer_s1 else "")
        # S2
        _merge_coating_value(0, 2, coat_preset if has_outer_s2 else "")

    if sapphire_s1:
        _merge_coating_value(2, 4, "蓝宝石膜", _CJK_FONT)
    if sapphire_s2:
        _merge_coating_value(0, 2, "蓝宝石膜", _CJK_FONT)

    # 胶合内表面的膜层直接在预览表格中选择，不占用侧栏空间。
    if is_cemented_single and not has_outer_s1:
        _tag_coating_selector(
            2, 4, "sapphire_s1",
            "蓝宝石膜" if sapphire_s1 else "",
            proc_params.get("sapphire_s1_key", ""),
        )
    if is_cemented_single and not has_outer_s2:
        _tag_coating_selector(
            0, 2, "sapphire_s2",
            "蓝宝石膜" if sapphire_s2 else "",
            proc_params.get("sapphire_s2_key", ""),
        )

    # row3 (C) - S1第一组镀膜数据
    _cell(3, 0, "C", f=6.5)
    _cell(3, 1, var_c, f=6.5, field_id="c_val")
    _cell_merge(2, 2, "S1")  # 合并S1单元格
    if not sapphire_s1 and (coat_preset == "Custom" or not has_outer_s1):
        _cell(3, 3, w_s1_1)
        _cell(3, 4, r_s1_1)
        _cell(3, 5, a_s1_1)
    # Project/Signature/Date/Part No/Part Name/Version 的 Row3 和 Row2 合并
    _cell_merge(2, 6, "Drafting")
    _cell_merge(2, 7, var_sig, field_id="signature")
    _cell_merge(2, 8, today)
    _cell_merge(2, 9, var_pno)
    _cell_merge(2,10, var_pn)
    _cell_merge(2,11, "1.0")
    # row2 (N) - S1第二组镀膜数据
    _cell(2, 0, "N", f=6.5)
    _cell(2, 1, str(N_val), f=6.5, field_id="n_val")
    if not sapphire_s1 and (coat_preset == "Custom" or not has_outer_s1):
        _cell(2, 3, w_s1_2)
        _cell(2, 4, r_s1_2)
        _cell(2, 5, a_s1_2)
    # row1 (ΔN) - S2第一组镀膜数据
    _cell(1, 0, "ΔN", f=6.5)
    _cell(1, 1, str(s("proc_DN", "0.3", "DN")), f=6.5, field_id="dn_val")
    _cell_merge(0, 2, "S2")  # 合并S2单元格
    if not sapphire_s2 and (coat_preset == "Custom" or not has_outer_s2):
        _cell(1, 3, w_s2_1)
        _cell(1, 4, r_s2_1)
        _cell(1, 5, a_s2_1)
    _cell(1, 6, "Checked")
    _cell(1,11, "Page No.")
    # row0 (B) - S2第二组镀膜数据
    _cell(0, 0, "B", f=6.5)
    _cell(0, 1, var_b, f=6.5, field_id="b_val")
    if not sapphire_s2 and (coat_preset == "Custom" or not has_outer_s2):
        _cell(0, 3, w_s2_2)
        _cell(0, 4, r_s2_2)
        _cell(0, 5, a_s2_2)
    _cell(0, 6, "Approved")
    _page_str = f"{page_no}/{total_pages}" if page_no is not None and total_pages is not None else "1/1"
    _cell(0,11, _page_str)
    # Part No/Part Name 的 Row1 和 Row0 合并显示 Material/H-K9L
    _cell_merge(0, 9, "Material")
    _cell_merge(0,10, var_gn)

    # ---- 镜片图 axes（放置在页面中央，给四周标注留空间） ----
    # 页面坐标：x=[70, 280], y=[55, 185]（单位mm，从底部算起）
    ax_lens = fig.add_axes([90/297, 62/210, 210/297, 130/210])
    J = MD * J_mult
    profile, pts = build_profile(T, R1, R2, MD, AD1, AD2)
    A, B, C, D, E, F = pts["A"], pts["B"], pts["C"], pts["D"], pts["E"], pts["F"]

    xa, ya = [], []
    for seg in profile:
        if seg[0] in ("arc", "line"):
            xa.extend(seg[1])
            ya.extend(seg[2])
    xa.extend([-v for v in xa])
    ya.extend([-v for v in ya])
    xm = J * 4
    # 放大显示：扩大xlim/ylim范围，确保所有标注都在范围内
    x_min = min(xa) - xm - J * 5  # 左侧留空间给AD1、Sag1
    x_max = max(xa) + xm + J * 8  # 右侧留空间给AD2、直径、ET
    y_min = min(ya) - J * 7.0     # 下方留空间给CT、Sag标注
    y_max = max(ya) + J * 6.0     # 上方留空间给ET、倒角标注
    ax_lens.set_xlim(x_min, x_max)
    ax_lens.set_ylim(y_min, y_max)
    ax_lens.set_aspect("equal")
    ax_lens.axis("off")
    # 关闭axes裁剪，让所有标注线都能完整显示
    ax_lens.set_clip_on(False)

    # 绘制镜片填充
    ux, uy = [], []
    for seg in profile:
        if seg[0] == "arc":
            ux.extend(seg[1]); uy.extend(seg[2])
        elif seg[0] == "line":
            ux.extend(seg[1]); uy.extend(seg[2])
    lx = list(reversed(ux))
    ly = [-v for v in reversed(uy)]
    px = ux + lx + [ux[0]]
    py = uy + ly + [uy[0]]
    ax_lens.add_patch(Polygon(list(zip(px, py)), closed=True, facecolor="#E8E8E8", edgecolor="none", hatch="//", linewidth=0.4, zorder=1))
    # 轮廓线
    lw = 1.2
    for seg in profile:
        if seg[0] == "arc":
            ax_lens.plot(seg[1], seg[2], "k-", lw=lw, zorder=3)
            ax_lens.plot(seg[1], [-v for v in seg[2]], "k-", lw=lw, zorder=3)
        elif seg[0] == "line":
            ax_lens.plot(seg[1], seg[2], "k-", lw=lw, zorder=3)
            ax_lens.plot(seg[1], [-v for v in seg[2]], "k-", lw=lw, zorder=3)
    ax_lens.plot(A[0], A[1], "k.", markersize=4, zorder=4)
    ax_lens.plot(D[0], D[1], "k.", markersize=4, zorder=4)

    # 标注
    _ann_ct(ax_lens, T, J, ct_offset_J, t_tol, font_size, arrow_scale)
    ET = _calc_et(T, R1, R2, AD1, AD2)
    ct_text_x = T + J * 2.5
    _ann_et(ax_lens, C, F, ET, J, et_offset_J, font_size, arrow_scale, text_x=ct_text_x)

    # ── Lane-based 标注布局：直径 + AD（自动避免重叠） ──
    mgr_left = SideAnnotationManager(J, dia_offset_J, lane_spacing_J=3.0,
                                     boundary_x=min(ux))
    mgr_right = SideAnnotationManager(J, dia_offset_J, lane_spacing_J=3.0,
                                      boundary_x=max(ux))

    # 右侧直径标注
    mgr_right.register(
        direction="right",
        anchor_y_center=0.0,
        anchor_y_half_span=MD/2.0,
        priority=1,  # 直径标注在外侧
        draw_fn=lambda offset_J, _ax=ax_lens, _Fx=F[0], _MD=MD, _J=J, _fs=font_size, _as=arrow_scale, _dtu=dia_tol_upper, _dtd=dia_tol_lower:
            _ann_diameter(_ax, _Fx, _MD, _J, offset_J, _fs, _as, _dtu, _dtd),
        slot_id="dia_R",
        attach_x=F[0],
        offset_scale=J,
        preferred_offset_J=dia_offset_J
    )

    Sag1 = abs(B[0] - A[0]); ad1 = (R1 < 0 and abs(AD1 - MD) > 0.01)
    Sag2 = abs(E[0] - D[0]); ad2 = (R2 > 0 and abs(AD2 - MD) > 0.01)

    # 左侧 AD1 标注
    if R1 < 0 and ad1:
        mgr_left.register(
            direction="left",
            anchor_y_center=0.0,
            anchor_y_half_span=AD1/2.0,
            priority=0,  # AD标注在内侧
            draw_fn=lambda offset_J, _ax=ax_lens, _Bp=(B[0],MD/2), _Bn=(B[0],-MD/2), _AD1=AD1, _J=J, _fs=font_size, _as=arrow_scale:
                _ann_ad1(_ax, _Bp, _Bn, _AD1, _J, offset_J, _fs, _as),
            slot_id="ad1_L",
            attach_x=B[0],
            offset_scale=J,
            preferred_offset_J=ad_offset_J
        )

    # 右侧 AD2 标注
    if R2 > 0 and ad2:
        mgr_right.register(
            direction="right",
            anchor_y_center=0.0,
            anchor_y_half_span=AD2/2.0,
            priority=0,  # AD标注在内侧
            draw_fn=lambda offset_J, _ax=ax_lens, _Ep=(E[0],MD/2), _En=(E[0],-MD/2), _AD2=AD2, _J=J, _fs=font_size, _as=arrow_scale:
                _ann_ad2(_ax, _Ep, _En, _AD2, _J, offset_J, _fs, _as),
            slot_id="ad2_R",
            attach_x=E[0],
            offset_scale=J,
            preferred_offset_J=ad_offset_J
        )

    # 统一布局并绘制左右侧标注
    mgr_left.draw()
    mgr_right.draw()

    # 矢高标注（下方，不参与侧边 Lane 管理）
    # Sag1 抬高到与 spraying 文字同一水平（offset 减少 1.5J）
    sag1_offset_J = max(3.0, sag_offset_J - 1.5)
    if R1 < 0:
        _ann_sag1(ax_lens, A, B, Sag1, MD, J, sag1_offset_J, font_size, arrow_scale, Sag1 >= 0.15 and ad1, sag_tol)
    if R2 > 0:
        _ann_sag2(ax_lens, D, E, Sag2, MD, J, sag_offset_J, font_size, arrow_scale, Sag2 >= 0.15 and ad2, text_x=ct_text_x, sag_tol=sag_tol)

    cx2 = float(T) + R2
    _ann_r1(ax_lens, B, R1, J, font_size, arrow_scale, r_offset_J, ArcA=A, ArcB=B)
    _ann_r2(ax_lens, E, D, R2, J, font_size, arrow_scale, r_offset_J, cx2=cx2)
    _ann_chamfer_left(ax_lens, B[0], MD/2, chamfer_left, J, font_size, arrow_scale)
    _ann_chamfer_right(ax_lens, E[0], MD/2, chamfer_right, J, font_size, arrow_scale)
    # 喷漆线：胶合单片页不标注
    is_cemented_single = proc_params.get("is_cemented_single", False) if proc_params else False
    if not is_cemented_single:
        _ann_spraying(ax_lens, B, C, E, F, MD, J, spray_gap_J, font_size, arrow_scale)
    # 光轴⨁标注：胶合单片页只有最外侧曲面标注⨁
    has_outer_s1 = proc_params.get("has_outer_s1", True) if proc_params else True
    has_outer_s2 = proc_params.get("has_outer_s2", True) if proc_params else True
    _ann_optical_axis(ax_lens, A, D, J, font_size, show_s1_symbol=has_outer_s1, show_s2_symbol=has_outer_s2)

    # tight_layout 与覆盖整个 figure 的 axes 不兼容，禁用
    # fig.tight_layout(pad=0)
    return fig


# ── PDF 导出函数（单片，兼容旧接口） ──────────────────────────────
def export_pdf(T,R1,R2,MD,AD1,AD2,
               J_mult,ct_offset_J,et_offset_J,sag_offset_J,dia_offset_J,ad_offset_J,spray_gap_J,
               chamfer_left,chamfer_right,t_tol,sag_tol,font_size,arrow_scale,r_offset_J,output_path,
               dia_tol_upper=0.010,dia_tol_lower=0.025,
               proc_params=None,settings=None,ca1=None,ca2=None):
    from matplotlib.backends.backend_pdf import PdfPages
    from config import validate

    errors = validate(T, R1, R2, MD, AD1, AD2)
    if errors:
        raise ValueError("; ".join(errors))

    fig = _build_single_page_figure(T,R1,R2,MD,AD1,AD2,
               J_mult,ct_offset_J,et_offset_J,sag_offset_J,dia_offset_J,ad_offset_J,spray_gap_J,
               chamfer_left,chamfer_right,t_tol,sag_tol,font_size,arrow_scale,r_offset_J,
               dia_tol_upper,dia_tol_lower,
               proc_params,settings,ca1,ca2)
    with PdfPages(output_path) as pdf:
        pdf.savefig(fig)
        d=pdf.infodict()
        d["Title"]="Lens Drawing"
        d["Subject"]=f"Lens T={T} R1={R1} R2={R2} MD={MD}"
    plt.close(fig)

# ══════════════════════════════════════════════════════════════════════
#  胶合镜片绘制 & 批量导出
# ══════════════════════════════════════════════════════════════════════

# ── 胶合镜片组装图预览（GUI 用） ──────────────────────────────────
def draw_cemented_assembly(ax, lenses_data,
              J_mult,ct_offset_J,et_offset_J,sag_offset_J,dia_offset_J,ad_offset_J,spray_gap_J,
              chamfer_left,chamfer_right,t_tol,sag_tol,font_size,arrow_scale,r_offset_J=0.8,
              dia_tol_upper=0.010,dia_tol_lower=0.025,
              dia_tol_nonpos_upper=0.05,dia_tol_nonpos_lower=0.10,
              cemented_ref_lens=2,no_curvature=True):
    """绘制胶合镜片整体组装图
    规则：
    - 不标注倒角
    - 中心厚度合并标注（总和），置于右侧
    - 喷漆线标注到胶合页
    - 第一个曲面(R1)和最后一个曲面需矢高/CA标注
    - 相同MD只标注一个直径
    - 光轴⨁仅在最外侧标注
    - 直径公差：定位镜片用定位公差，其余用非定位公差
    """
    ax.clear()
    ax._dimension_renderer_ready = False
    ax._dimension_leader_groups = {}
    max_MD=max(l.MD for l in lenses_data)
    J=max_MD*J_mult

    # 计算每片 x 偏移
    offsets=[0.0]
    for lens in lenses_data[:-1]:
        offsets.append(offsets[-1]+lens.T)

    colors=["#E8E8E8","#D8E8D8","#E8D8E8"]
    hatches=["//","\\\\","xx"]
    all_x,all_y=[],[]

    # 为每片存储轮廓数据
    all_profiles=[]; all_pts=[]

    for i,lens in enumerate(lenses_data):
        x0=offsets[i]
        profile,pts=build_profile(lens.T,lens.R_left,lens.R_right,lens.MD,lens.AD_left,lens.AD_right)
        all_profiles.append(profile); all_pts.append(pts)
        A,B,C,D,E,F=pts["A"],pts["B"],pts["C"],pts["D"],pts["E"],pts["F"]

        ux,uy=[],[]
        for seg in profile:
            if seg[0] in("arc","line"): ux.extend(seg[1]); uy.extend(seg[2])
        ux_o=[x+x0 for x in ux]
        all_x.extend(ux_o); all_y.extend(uy)

        # 填充多边形
        lx=list(reversed(ux_o)); ly=[-v for v in reversed(uy)]
        px=ux_o+lx+[ux_o[0]]; py=uy+ly+[uy[0]]
        ax.add_patch(Polygon(list(zip(px,py)),closed=True,facecolor=colors[i%3],
                             edgecolor="none",hatch=hatches[i%3],linewidth=0.4,zorder=1))
        # 轮廓线
        lw=1.2
        for seg in profile:
            if seg[0]=="arc":
                xs=[x+x0 for x in seg[1]]
                ax.plot(xs,seg[2],"k-",lw=lw,zorder=3)
                ax.plot(xs,[-v for v in seg[2]],"k-",lw=lw,zorder=3)
            elif seg[0]=="line":
                xs=[x+x0 for x in seg[1]]
                ax.plot(xs,seg[2],"k-",lw=lw,zorder=3)
                ax.plot(xs,[-v for v in seg[2]],"k-",lw=lw,zorder=3)
        # 顶点
        ax.plot(A[0]+x0,A[1],"k.",markersize=4,zorder=4)
        ax.plot(D[0]+x0,D[1],"k.",markersize=4,zorder=4)

    total_T=sum(l.T for l in lenses_data)
    total_t_tol=sum(t_tol for _ in lenses_data)
    xm=J*5
    x_limits=(min(all_x)-xm-J*8,max(all_x)+xm+J*16)
    y_limits=(min(all_y)-J*14,max(all_y)+J*12)
    ax.set_xlim(*x_limits)
    ax.set_ylim(*y_limits)
    ax.set_aspect("equal")

    # ── 厚度标注（合并为总和，置于右侧） ──
    y_ext=-J*ct_offset_J
    ax.plot([0,0],[0,y_ext],"k-",lw=0.8,zorder=5)
    ax.plot([total_T,total_T],[0,y_ext],"k-",lw=0.8,zorder=5)
    ax.plot([0,total_T],[y_ext,y_ext],"k-",lw=0.8,zorder=5)
    aw=J*0.6*arrow_scale
    _arrow(ax,-aw,y_ext,0,y_ext,hs=aw)
    _arrow(ax,total_T+aw,y_ext,total_T,y_ext,hs=aw)
    text_x=total_T+J*2.5
    text_str=f"{total_T:.2f}\u00b1{total_t_tol:.2f}"
    _dimension_text_with_leader(
        ax, total_T, text_x, y_ext, text_str, font_size, J,
        ha="left", direction="right", leader_id="assembly-ct",
    )

    # ── Lane-based 标注布局：直径 + AD（自动避免重叠） ──
    mgr_left = SideAnnotationManager(J, dia_offset_J, lane_spacing_J=3.0,
                                     boundary_x=min(all_x))
    mgr_right = SideAnnotationManager(J, dia_offset_J, lane_spacing_J=3.0,
                                      boundary_x=max(all_x))

    drawn_MDs=[]  # 已标注的MD值列表（记录MD和tol_upper/tol_lower）
    for i,lens in enumerate(lenses_data):
        x0=offsets[i]; lj=lens.MD*J_mult
        # 判断该片直径公差：定位镜片用定位公差，其余用非定位公差
        ref_index = int(cemented_ref_lens) - 1  # 0-based
        if i == ref_index:
            dtu,dtd=dia_tol_upper,dia_tol_lower
        else:
            dtu,dtd=dia_tol_nonpos_upper,dia_tol_nonpos_lower
        # 检查是否已有相同MD且相同公差的标注
        already_drawn=False
        for drawn_md,drawn_dtu,drawn_dtd in drawn_MDs:
            if abs(drawn_md-lens.MD)<0.01 and abs(drawn_dtu-dtu)<0.001 and abs(drawn_dtd-dtd)<0.001:
                already_drawn=True; break
        if already_drawn:
            continue
        drawn_MDs.append((lens.MD,dtu,dtd))

        _profile,_pts=all_profiles[i],all_pts[i]
        # 直径标注的 Y 跨度 = MD，中心 = 0
        dia_y_center = 0.0
        dia_y_half = lens.MD / 2.0

        if i==0:
            # 第一片：左侧直径标注
            B_x=_pts["B"][0]+x0
            mgr_left.register(
                direction="left",
                anchor_y_center=dia_y_center,
                anchor_y_half_span=dia_y_half,
                priority=1,  # 直径标注在外侧
                draw_fn=lambda offset_J, _ax=ax, _B_x=B_x, _MD=lens.MD, _lj=lj, _fs=font_size, _as=arrow_scale, _dtu=dtu, _dtd=dtd:
                    _ann_diameter_left(_ax, _B_x, _MD, _lj, offset_J, _fs, _as, _dtu, _dtd),
                slot_id=f"dia_L_{i}",
                attach_x=B_x,
                offset_scale=lj,
                preferred_offset_J=dia_offset_J
            )
        else:
            # 其余片：右侧直径标注
            F_x=_pts["F"][0]+x0
            mgr_right.register(
                direction="right",
                anchor_y_center=dia_y_center,
                anchor_y_half_span=dia_y_half,
                priority=1,  # 直径标注在外侧
                draw_fn=lambda offset_J, _ax=ax, _F_x=F_x, _MD=lens.MD, _lj=lj, _fs=font_size, _as=arrow_scale, _dtu=dtu, _dtd=dtd:
                    _ann_diameter(_ax, _F_x, _MD, _lj, offset_J, _fs, _as, _dtu, _dtd),
                slot_id=f"dia_R_{i}",
                attach_x=F_x,
                offset_scale=lj,
                preferred_offset_J=dia_offset_J
            )

    # ── AD 标注也纳入 Lane 管理 ──
    # 第一个曲面的 AD1（左侧）
    lens0=lenses_data[0]
    pt0=all_pts[0]
    A0,B0=pt0["A"],pt0["B"]
    A0_g=(A0[0]+offsets[0],A0[1])
    B0_g_abs=(B0[0]+offsets[0],B0[1])
    Sag1=abs(B0[0]-A0[0])
    ad1=(lens0.R_left<0 and abs(lens0.AD_left-lens0.MD)>0.01)

    if lens0.R_left<0 and ad1:
        ad1_y_center = 0.0
        ad1_y_half = lens0.AD_left / 2.0
        mgr_left.register(
            direction="left",
            anchor_y_center=ad1_y_center,
            anchor_y_half_span=ad1_y_half,
            priority=0,  # AD 小口径在内侧，MD 大口径在外侧
            draw_fn=lambda offset_J, _ax=ax, _Bp=(B0_g_abs[0],lens0.MD/2), _Bn=(B0_g_abs[0],-lens0.MD/2), _AD1=lens0.AD_left, _lj=J, _fs=font_size, _as=arrow_scale:
                _ann_ad1(_ax, _Bp, _Bn, _AD1, _lj, offset_J, _fs, _as),
            slot_id="ad1_L",
            attach_x=B0_g_abs[0],
            offset_scale=J,
            preferred_offset_J=ad_offset_J
        )

    # 最后一个曲面的 AD2（右侧）
    lensN=lenses_data[-1]
    ptN=all_pts[-1]
    DN,EN=ptN["D"],ptN["E"]
    DN_g=(DN[0]+offsets[-1],DN[1])
    EN_g_abs=(EN[0]+offsets[-1],EN[1])
    Sag2=abs(EN[0]-DN[0])
    ad2=(lensN.R_right>0 and abs(lensN.AD_right-lensN.MD)>0.01)

    if lensN.R_right>0 and ad2:
        ad2_y_center = 0.0
        ad2_y_half = lensN.AD_right / 2.0
        mgr_right.register(
            direction="right",
            anchor_y_center=ad2_y_center,
            anchor_y_half_span=ad2_y_half,
            priority=0,  # AD 小口径在内侧，MD 大口径在外侧
            draw_fn=lambda offset_J, _ax=ax, _Ep=(EN_g_abs[0],lensN.MD/2), _En=(EN_g_abs[0],-lensN.MD/2), _AD2=lensN.AD_right, _lj=J, _fs=font_size, _as=arrow_scale:
                _ann_ad2(_ax, _Ep, _En, _AD2, _lj, offset_J, _fs, _as),
            slot_id="ad2_R",
            attach_x=EN_g_abs[0],
            offset_scale=J,
            preferred_offset_J=ad_offset_J
        )

    # ── 统一布局并绘制左右侧标注 ──
    mgr_left.draw()
    mgr_right.draw()

    # ── 喷漆线标注（沿胶合组装体外轮廓绘制） ──
    dy_off = 0.2 * J
    n_lenses = len(lenses_data)
    aw = J * 0.6 * arrow_scale

    # 计算每片喷漆线框的边界（与单片_ann_spraying逻辑一致）
    bounds = []
    for i in range(n_lenses):
        xi = offsets[i]
        pts_i = all_pts[i]
        B, C, E, F = pts_i["B"], pts_i["C"], pts_i["E"], pts_i["F"]
        MD_i = lenses_data[i].MD

        bc = abs(B[1]-C[1]) > 1e-9
        fe = abs(F[1]-E[1]) > 1e-9

        # 左边界：有左侧倒角时B[0]-dy_off，否则B[0]（无倒角时B=C，用C[0]）
        left = xi + B[0] - (dy_off if bc else 0)
        # 右边界：有右侧倒角时E[0]+dy_off，否则E[0]（无倒角时E=F，用F[0]=E[0]）
        right = xi + E[0] + (dy_off if fe else 0)
        top = MD_i/2 + dy_off

        bounds.append((left, right, top, bc, fe, B[1], E[1]))

    # ── 构建顶部包络轮廓（正Y轴） ──
    # 收集所有顶部水平线段的关键X坐标
    x_coords = set()
    for left, right, top, bc, fe, by, ey in bounds:
        x_coords.add(left)
        x_coords.add(right)
    x_sorted = sorted(x_coords)

    upper_pts = []

    # 第一片左侧垂直边（最左侧，无前置片覆盖）
    first_left, first_right, first_top, first_bc, first_fe, first_by, first_ey = bounds[0]
    if first_bc:
        upper_pts.append((first_left, first_by))
    upper_pts.append((first_left, first_top))

    # 扫描每个X区间，取覆盖该区间所有顶部线段的最高高度作为外轮廓
    for k in range(len(x_sorted) - 1):
        x_mid = (x_sorted[k] + x_sorted[k+1]) / 2.0

        # 计算该区间内的最大 top
        max_top = None
        for left, right, top, bc, fe, by, ey in bounds:
            if left - 1e-9 <= x_mid <= right + 1e-9:
                if max_top is None or top > max_top:
                    max_top = top

        if max_top is None:
            continue

        x_start = x_sorted[k]
        x_end = x_sorted[k+1]

        if not upper_pts:
            upper_pts.append((x_start, max_top))
        else:
            last_x, last_y = upper_pts[-1]
            if abs(last_y - max_top) > 1e-9:
                # 高度变化：在 x_start 处垂直转折
                if abs(last_x - x_start) > 1e-9:
                    upper_pts.append((x_start, last_y))
                upper_pts.append((x_start, max_top))

        # 水平走到区间终点
        upper_pts.append((x_end, max_top))

    # 最后片右侧垂直边（仅当最后片确实是最右端点且有右侧倒角差时）
    last_left, last_right, last_top, last_bc, last_fe, last_by, last_ey = bounds[-1]
    if last_fe and upper_pts and abs(upper_pts[-1][0] - last_right) < 1e-9:
        upper_pts.append((last_right, last_ey))

    # 去重连续相同点
    upper_clean = []
    for p in upper_pts:
        if not upper_clean or abs(p[0]-upper_clean[-1][0])>1e-9 or abs(p[1]-upper_clean[-1][1])>1e-9:
            upper_clean.append(p)

    # 绘制上半部分虚线
    for i in range(len(upper_clean)-1):
        p0, p1 = upper_clean[i], upper_clean[i+1]
        ax.plot([p0[0],p1[0]],[p0[1],p1[1]],"--",lw=0.5,zorder=3,color="gray")

    # 下半部分（镜像）
    lower_clean = [(x, -y) for x, y in upper_clean]
    for i in range(len(lower_clean)-1):
        p0, p1 = lower_clean[i], lower_clean[i+1]
        ax.plot([p0[0],p1[0]],[p0[1],p1[1]],"--",lw=0.5,zorder=3,color="gray")

    # spraying 转折引线：找到下半部分最底部的水平段中点
    # lower_clean中Y坐标最小（最偏离0）的点构成底部水平段
    if len(lower_clean) >= 2:
        # 找到Y坐标最小的点（最偏离0）
        min_y = min(p[1] for p in lower_clean)
        # 找到所有Y坐标等于min_y的点
        bottom_points = [p for p in lower_clean if abs(p[1] - min_y) < 1e-9]
        if len(bottom_points) >= 2:
            mid_x = (bottom_points[0][0] + bottom_points[-1][0]) / 2.0
            mid_y = min_y
        else:
            mid_x = lower_clean[0][0]
            mid_y = lower_clean[0][1]
    else:
        mid_x = 0.0
        mid_y = -J
    arr_sx = mid_x + J*1.2
    arr_sy = mid_y - J*1.2
    _arrow(ax, arr_sx, arr_sy, mid_x, mid_y, hs=aw)
    ax.plot([arr_sx, arr_sx+5.0*J], [arr_sy, arr_sy], "k-", lw=0.7, zorder=5)
    ax.text(arr_sx+2.5*J, arr_sy+font_size*0.01, "spraying",
            ha="center", va="bottom", fontsize=font_size-2, color="black", zorder=7)

    # ── 第一个曲面(R1)矢高标注（AD已在Lane管理器中注册） ──
    # Sag1 抬高到与 spraying 文字同一水平（offset 减少 1.5J）
    sag1_offset_J = max(3.0, sag_offset_J - 1.5)
    if lens0.R_left<0:
        if Sag1>=0.15 and ad1:
            _ann_sag1(ax,A0_g,B0_g_abs,Sag1,lens0.MD,J,sag1_offset_J,font_size,arrow_scale,True,sag_tol)
        else:
            _ann_sag1(ax,A0_g,B0_g_abs,Sag1,lens0.MD,J,sag1_offset_J,font_size,arrow_scale,False,sag_tol)

    # ── 最后一个曲面矢高标注（AD已在Lane管理器中注册） ──
    if lensN.R_right>0:
        ct_text_x=total_T+J*2.5
        if Sag2>=0.15 and ad2:
            _ann_sag2(ax,DN_g,EN_g_abs,Sag2,lensN.MD,J,sag_offset_J,font_size,arrow_scale,True,text_x=ct_text_x,sag_tol=sag_tol)
        else:
            _ann_sag2(ax,DN_g,EN_g_abs,Sag2,lensN.MD,J,sag_offset_J,font_size,arrow_scale,False,text_x=ct_text_x,sag_tol=sag_tol)

    # ── 光轴（⨁仅在最外侧标注） ──
    ax.plot([-J*3,total_T+J*3],[0,0],"k-",lw=0.8,zorder=10)
    big_fs=font_size*1.5; text_y=J*0.15
    # 左侧：⨁ S1（最外侧第一个曲面，需要⨁）
    ax.text(-J*2.25,text_y,"⨁",ha="center",va="bottom",fontsize=big_fs,color="black",zorder=12)
    ax.text(-J*1.6,text_y,"S1",ha="left",va="bottom",fontsize=font_size,color="black",zorder=12)
    # 右侧：S2 ⨁（最外侧最后一个曲面，需要⨁）
    ax.text(total_T+J*2.25-J*0.65,text_y,"S2",ha="right",va="bottom",fontsize=font_size,color="black",zorder=12)
    ax.text(total_T+J*2.25,text_y,"⨁",ha="center",va="bottom",fontsize=big_fs,color="black",zorder=12)

    ax.set_xlim(*x_limits)
    ax.set_ylim(*y_limits)
    ax.set_aspect("equal"); ax.axis("off")


# ── 胶合整体页 Figure 构建 ────────────────────────────────────────
def _build_assembly_page_figure(cemented_data, settings, page_no=1, total_pages=1, hide_partname=False):
    """构建胶合镜片整体页的 Figure（第1页：整体图，不标注曲率）
    规则：
    - N/ΔN/B 留空不标注
    - 胶合页两侧不需要⨁标注，coating position的S1/S2行留空
    - 页码显示 page_no/total_pages
    """
    from matplotlib.figure import Figure
    from matplotlib.patches import Polygon
    from config import auto_CA, auto_chamfer

    lenses=cemented_data.lenses
    s=lambda k,d: settings.get(k,d)
    today=datetime.now().strftime("%Y.%m.%d")

    # 计算参数
    max_MD=max(l.MD for l in lenses)
    _ca_ratio = settings.get("ca_ratio", 0.94) if settings else 0.94
    # 组装页 CA：检查第1片和最后一片的手动 CA 值
    _ca_first, _ = _resolve_cemented_ca(settings, 0, _ca_ratio)
    CA1 = _ca_first if _ca_first is not None else auto_CA(lenses[0].AD_left, _ca_ratio)
    _, _ca_last = _resolve_cemented_ca(settings, len(lenses) - 1, _ca_ratio)
    CA2 = _ca_last if _ca_last is not None else auto_CA(lenses[-1].AD_right, _ca_ratio)
    var_c=s("proc_c_assembly","60″")
    var_sig=s("proc_signature","l.y.h")
    var_vnd=s("proc_vendor","CDGM")
    var_rnk=s("proc_ranking","01")
    var_gn=cemented_data.glass_names_str
    var_pn="" if hide_partname else cemented_data.part_name
    var_pno=cemented_data.part_no
    # 胶合页不需要镀膜数据（两侧不标⨁，S1/S2行留空）
    # 显示参数
    J_mult=settings.get("J_multiplier",0.10)
    font_size=settings.get("font_size",9)
    arrow_scale=settings.get("arrow_scale",1.0)
    t_tol=settings.get("t_tol",0.02)
    sag_tol=settings.get("sag_tol",0.02)
    dia_tol_upper=settings.get("dia_tol_pos_upper",settings.get("dia_tol_upper",0.010))
    dia_tol_lower=settings.get("dia_tol_pos_lower",settings.get("dia_tol_lower",0.025))
    dia_tol_nonpos_upper=settings.get("dia_tol_nonpos_upper",0.05)
    dia_tol_nonpos_lower=settings.get("dia_tol_nonpos_lower",0.10)
    cemented_ref=int(settings.get("cemented_ref_lens",2))
    chamfer_mode=settings.get("chamfer_mode","auto")
    cL,cR=auto_chamfer(max_MD,lenses[0].R_left,lenses[-1].R_right) if chamfer_mode=="auto" else (settings.get("chamfer_left",0.2),settings.get("chamfer_right",0.4))

    fig=Figure(figsize=(11.69,8.27),dpi=300)  # 11.69*100=1169, 8.27*100=827, closest integer pixels to A4 ratio
    # ── 页面坐标 axes ──
    ax_page=fig.add_axes([0,0,1,1])
    ax_page.set_xlim(0,297); ax_page.set_ylim(0,210); ax_page.set_aspect("equal"); ax_page.axis("off")

    # 外框
    margin_l,margin_r,margin_t,margin_b=10,5,5,5
    fx0,fy0,fx1,fy1=margin_l,margin_b,297-margin_r,210-margin_t
    ax_page.plot([fx0,fx1,fx1,fx0,fx0],[fy0,fy0,fy1,fy1,fy0],"k-",lw=1.2)

    # ── 左侧文字（胶合页：无Material，从Sample accuracy开始） ──
    tx_x,tx_y=18,195; line_h=3.0; fs=7.5
    def _tx(dx,dy,text,bold=False,f=fs):
        ax_page.text(tx_x+dx,tx_y+dy,text,ha="left",va="bottom",fontsize=f,color="black",fontweight="bold" if bold else "normal")
    def _tx_pair(dx,dy,label,value):
        _tx(dx,dy,label); _tx(dx+42,dy,value)
    _tx(0,0,"1.Sample accuracy",bold=True)
    _tx_pair(4,-line_h,"ΔR:","A")
    _tx(0,-line_h*2,"2.Processing",bold=True)
    _tx_pair(4,-line_h*3,"Chamfer:",f"{cL:.1f}")
    _tx_pair(4,-line_h*4,"Chipping:","0.2")
    _tx_pair(4,-line_h*5,"Clear Aperture",f"S1 φ{CA1:.2f}  S2 φ{CA2:.2f}")
    _tx(0,-line_h*6.5,"3.Spraying",bold=True)
    _tx_pair(4,-line_h*7.5,"Ink Brand&Model:","GT-7II")
    _tx_pair(4,-line_h*8.5,"Ink Proportion:","8: 1: 9(Paint: Curing agent: Diluent)")
    _tx_pair(4,-line_h*9.5,"Thickness:","3~5um")
    _tx_pair(4,-line_h*10.5,"Spraying position:","Arrow indication The dashed line")
    _tx_pair(4,-line_h*11.5,"Dimensions:","According to the drawing")
    _tx_pair(4,-line_h*12.5,"Ink over spray/Light Leakage:","0.1")
    _tx(0,-line_h*14,"4.The rest",bold=True)
    _tx(4,-line_h*15,"roughness")
    tri_x,tri_y=tx_x+10,tx_y-line_h*17; tri_w=3; tri_h=tri_w*1.732
    ax_page.plot([tri_x-tri_w,tri_x+tri_w,tri_x,tri_x-tri_w],[tri_y,tri_y,tri_y-tri_h,tri_y],"k-",lw=0.8)
    ax_page.plot([tri_x-tri_w,tri_x+tri_w],[tri_y,tri_y],"k-",lw=0.8)
    ax_page.plot([tri_x+tri_w,tri_x+tri_w*2],[tri_y,tri_y+tri_h],"k-",lw=0.8)
    ax_page.text(tri_x,tri_y+1.0,"0.01",ha="center",va="bottom",fontsize=fs,color="black")

    # ── 胶合整体页精简标题栏 ──
    # 左下只保留胶合页 C；中间镀膜表及无值的 N/ΔN/B 不绘制。
    # 右侧项目栏保持原坐标和内容，避免不同页之间的项目字段跳动。
    tbl_y0 = 10
    row_h = 35.0 / 6
    rows_y = [tbl_y0 + i * row_h for i in range(7)]
    left_x = [18, 40, 62]
    right_x = [148, 170, 190, 212, 240, 268, 287]

    # 左侧两行小表：标题 + C 值，贴齐页面左下。
    for y in (rows_y[0], rows_y[1], rows_y[2]):
        ax_page.plot([left_x[0], left_x[-1]], [y, y], "k-", lw=0.6)
    for x in (left_x[0], left_x[-1]):
        ax_page.plot([x, x], [rows_y[0], rows_y[2]], "k-", lw=0.6)
    ax_page.plot([left_x[1], left_x[1]], [rows_y[0], rows_y[1]], "k-", lw=0.6)
    ax_page.text(
        (left_x[0] + left_x[-1]) / 2, (rows_y[1] + rows_y[2]) / 2,
        "Special technical requirement", ha="center", va="center",
        fontsize=6.5, color="black",
    )
    ax_page.text((left_x[0]+left_x[1])/2, (rows_y[0]+rows_y[1])/2-0.5,
                 "C", ha="center", va="center", fontsize=6.5, color="black")
    ax_page.text((left_x[1]+left_x[2])/2, (rows_y[0]+rows_y[1])/2-0.5,
                 var_c, ha="center", va="center", fontsize=6.5, color="black")

    # 右侧项目栏，边界和合并关系沿用原胶合页布局。
    for x in right_x:
        ax_page.plot([x, x], [rows_y[0], rows_y[6]], "k-", lw=0.6)
    for y in (rows_y[0], rows_y[2], rows_y[4], rows_y[6]):
        ax_page.plot([right_x[0], right_x[-1]], [y, y], "k-", lw=0.6)
    ax_page.plot([right_x[0], right_x[3]], [rows_y[1], rows_y[1]], "k-", lw=0.6)
    ax_page.plot([right_x[5], right_x[-1]], [rows_y[1], rows_y[1]], "k-", lw=0.6)

    header_y = (rows_y[4] + rows_y[6]) / 2
    headers = ["Project", "Signature", "Date", "Part No.", "Part Name", "Version"]
    for index, text in enumerate(headers):
        ax_page.text(
            (right_x[index]+right_x[index+1])/2, header_y, text,
            ha="center", va="center", fontsize=6.5, color="black",
        )

    def _right_cell(row_index, col_index, text, f=6.5):
        ax_page.text(
            (right_x[col_index]+right_x[col_index+1])/2,
            rows_y[row_index]+row_h/2-0.5, text,
            ha="center", va="center", fontsize=f, color="black",
        )

    def _right_merge(row_index, col_index, text, f=6.5):
        ax_page.text(
            (right_x[col_index]+right_x[col_index+1])/2,
            (rows_y[row_index]+rows_y[row_index+2])/2-0.5, text,
            ha="center", va="center", fontsize=f, color="black", linespacing=0.85,
        )

    _right_merge(2, 0, "Drafting")
    _right_merge(2, 1, var_sig)
    _right_merge(2, 2, today)
    _right_merge(2, 3, var_pno)
    _right_merge(2, 4, var_pn)
    _right_merge(2, 5, "1.0")
    _right_cell(1, 0, "Checked")
    _right_cell(1, 5, "Page No.")
    _right_cell(0, 0, "Approved")
    _right_cell(0, 5, f"{page_no}/{total_pages}")
    _right_merge(0, 3, "Material")
    _right_merge(0, 4, var_gn)

    # ── 镜片组装图 axes ──
    ax_lens=fig.add_axes([90/297,62/210,210/297,130/210])
    draw_cemented_assembly(ax_lens,lenses,
        settings.get("J_multiplier",0.10),
        settings.get("ct_offset_J",3.0),settings.get("et_offset_J",2.0),
        settings.get("sag_offset_J",3.0),settings.get("dia_offset_J",3.0),
        settings.get("ad_offset_J",2.0),settings.get("spray_gap_J",0.1),
        cL,cR,t_tol,sag_tol,font_size,arrow_scale,
        settings.get("r_offset_J",0.8),dia_tol_upper,dia_tol_lower,
        dia_tol_nonpos_upper,dia_tol_nonpos_lower,cemented_ref,
        no_curvature=True)
    ax_lens.set_clip_on(False)
    return fig


# ── 预览可编辑字段坐标计算 ──────────────────────────────────────
def _table_col_centers():
    """返回底部表格各列中心 x 坐标（基于 cum 数组）"""
    cols = [22, 22, 18, 28, 20, 20, 22, 20, 22, 28, 28, 19]
    cum = [18]
    for c in cols:
        cum.append(cum[-1] + c)
    return [(cum[i] + cum[i+1]) / 2 for i in range(12)]

def _table_row_ys():
    """返回底部表格各行中心 y 坐标（基于 rows_y 数组）"""
    row_h = 35.0 / 6
    return [10 + i * row_h + row_h/2 - 0.5 for i in range(6)]

def get_preview_field_metadata(is_cemented_single=False):
    """
    计算单页预览图中可编辑字段的 mm 坐标和当前值占位。
    返回 list[dict]，每项含: id, x_mm, y_mm, w_mm, h_mm, label, source
    其中 source 指示参数来源: 'proc'=proc_params, 'setting'=settings, 'calc'=自动计算
    """
    tx_x, tx_y, line_h = 18, 195, 3.0
    cols = _table_col_centers()
    rows = _table_row_ys()

    fields = []

    # ═══ 左侧文字区 ═══
    val_x = tx_x + 4 + 42  # _tx_pair 中 label 在 dx=4，value 在 dx+42 处

    if is_cemented_single:
        # 胶合单片页：无 Spraying，序号重排
        # 1.Material
        fields.append({"id":"vendor","label":"Vendor","x_mm":val_x,"y_mm":tx_y-line_h,"w_mm":40,"h_mm":3,"source":"setting","key":"proc_vendor"})
        fields.append({"id":"ranking","label":"Ranking","x_mm":val_x,"y_mm":tx_y-line_h*2,"w_mm":50,"h_mm":3,"source":"setting","key":"proc_ranking"})
        fields.append({"id":"molding","label":"Molding","x_mm":val_x,"y_mm":tx_y-line_h*3,"w_mm":45,"h_mm":3,"source":"setting","key":"proc_molding"})
        # 3.Processing
        fields.append({"id":"chamfer","label":"Chamfer","x_mm":val_x,"y_mm":tx_y-line_h*7.5,"w_mm":20,"h_mm":3,"source":"calc","key":"chamfer_left"})
        # Clear Aperture — 拆分为 CA1 / CA2 两个独立输入框
        ca_y = tx_y - line_h * 9.5
        fields.append({"id":"ca1","label":"CA1","x_mm":74,"y_mm":ca_y,"w_mm":18,"h_mm":3,"source":"calc","key":"CA1"})
        fields.append({"id":"ca2","label":"CA2","x_mm":98,"y_mm":ca_y,"w_mm":18,"h_mm":3,"source":"calc","key":"CA2"})
        fields.extend([
            {
                "id": "sapphire_s1", "label": "S1 内表面镀膜",
                "source": "surface", "kind": "select",
                "requires_position": True,
            },
            {
                "id": "sapphire_s2", "label": "S2 内表面镀膜",
                "source": "surface", "kind": "select",
                "requires_position": True,
            },
        ])
    else:
        # 独立单片页：完整 5 段
        fields.append({"id":"vendor","label":"Vendor","x_mm":val_x,"y_mm":tx_y-line_h,"w_mm":40,"h_mm":3,"source":"setting","key":"proc_vendor"})
        fields.append({"id":"ranking","label":"Ranking","x_mm":val_x,"y_mm":tx_y-line_h*2,"w_mm":50,"h_mm":3,"source":"setting","key":"proc_ranking"})
        fields.append({"id":"molding","label":"Molding","x_mm":val_x,"y_mm":tx_y-line_h*3,"w_mm":45,"h_mm":3,"source":"setting","key":"proc_molding"})
        fields.append({"id":"chamfer","label":"Chamfer","x_mm":val_x,"y_mm":tx_y-line_h*7.5,"w_mm":20,"h_mm":3,"source":"calc","key":"chamfer_left"})
        ca_y = tx_y - line_h * 9.5
        fields.append({"id":"ca1","label":"CA1","x_mm":74,"y_mm":ca_y,"w_mm":18,"h_mm":3,"source":"calc","key":"CA1"})
        fields.append({"id":"ca2","label":"CA2","x_mm":98,"y_mm":ca_y,"w_mm":18,"h_mm":3,"source":"calc","key":"CA2"})

    # ═══ 底部表格 ═══
    # C (row 3, col 1)
    fields.append({"id":"c_val","label":"C","x_mm":cols[1],"y_mm":rows[3],"w_mm":cols[2]-cols[1]+4,"h_mm":5,"source":"setting","key":"proc_c_single"})
    # N (row 2, col 1)
    fields.append({"id":"n_val","label":"N","x_mm":cols[1],"y_mm":rows[2],"w_mm":cols[2]-cols[1]+4,"h_mm":5,"source":"calc","key":"N"})
    # ΔN (row 1, col 1 — 值所在列，与 N/C 同列)
    fields.append({"id":"dn_val","label":"ΔN","x_mm":cols[1],"y_mm":rows[1],"w_mm":cols[1]-cols[0]+4,"h_mm":5,"source":"setting","key":"proc_DN"})
    # B (row 0, col 1 — 值所在列，与 N/C 同列)
    fields.append({"id":"b_val","label":"B","x_mm":cols[1],"y_mm":rows[0],"w_mm":cols[1]-cols[0]+4,"h_mm":5,"source":"setting","key":"proc_surface_defect"})
    # Signature (rows 2-3 merged, col 7)
    sig_y = (10 + 2*35/6 + 10 + 4*35/6) / 2 - 0.5
    fields.append({"id":"signature","label":"Sig","x_mm":cols[7],"y_mm":sig_y,"w_mm":cols[8]-cols[7]+4,"h_mm":5,"source":"setting","key":"proc_signature"})

    return fields


def extract_field_positions(fig, dpi=100):
    """渲染 Figure 后提取带 _field_id 标签的文字元素的像素 BBox，返回百分比坐标。

    返回: {field_id: {"left_pct", "top_pct", "w_pct", "h_pct"}}
    百分比基于图片宽高，可直接用于前端覆盖层定位。
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg, RendererAgg

    original_dpi = fig.dpi
    fig.set_dpi(dpi)

    fig_w = int(fig.get_figwidth() * dpi)
    fig_h = int(fig.get_figheight() * dpi)

    # 用 Agg 后端创建渲染器并强制渲染
    canvas = FigureCanvasAgg(fig)
    renderer = RendererAgg(fig_w, fig_h, dpi)
    fig.draw(renderer)

    PAD_PX = 3       # 仅用于没有显式版面热区的旧字段
    top_pad = PAD_PX + 1

    positions = {}
    for ax in fig.axes:
        for artist in [*ax.texts, *ax.patches]:
            fid = getattr(artist, '_field_id', None)
            if fid is None:
                continue
            bbox = artist.get_window_extent(renderer=renderer)
            is_region = bool(getattr(artist, '_field_region', False))
            pad_x = 0 if is_region else PAD_PX
            pad_top = 0 if is_region else top_pad
            pad_bottom = 0 if is_region else PAD_PX
            positions[fid] = {
                "left_pct": round((bbox.x0 - pad_x) / fig_w * 100, 2),
                "top_pct": round((fig_h - bbox.y1 - pad_top) / fig_h * 100, 2),
                "w_pct": round((bbox.x1 - bbox.x0 + pad_x * 2) / fig_w * 100, 2),
                "h_pct": round((bbox.y1 - bbox.y0 + pad_top + pad_bottom) / fig_h * 100, 2),
            }

    fig.set_dpi(original_dpi)  # 恢复原 DPI
    return positions



def _resolve_cemented_chamfer(settings, lens_index):
    """Return (cL, cR) - None means use auto calculation.
    Reads chamfer_mode_N / chamfer_N_left / chamfer_N_right from settings.
    If no per-lens setting, falls back to global chamfer_mode/chamfer_left/chamfer_right.
    """
    mode = settings.get(f"chamfer_mode_{lens_index + 1}", "auto")
    if mode == "manual":
        left = settings.get(f"chamfer_{lens_index + 1}_left")
        right = settings.get(f"chamfer_{lens_index + 1}_right")
        cL = float(left) if left not in (None, "") else None
        cR = float(right) if right not in (None, "") else None
    else:
        cL = cR = None

    # Fallback: 批量出图模块使用单镜片全局 chamfer_mode/chamfer_left/chamfer_right 键
    if cL is None and cR is None:
        global_mode = settings.get("chamfer_mode", "auto")
        if global_mode == "manual":
            left = settings.get("chamfer_left")
            right = settings.get("chamfer_right")
            cL = float(left) if left not in (None, "") else None
            cR = float(right) if right not in (None, "") else None

    return cL, cR

def _resolve_cemented_ca(settings, lens_index, ca_ratio):
    """返回 (ca1, ca2) — None 表示使用自动计算。
    根据 settings 中的 ca_mode_N / ca_N_left / ca_N_right 字段决定。
    若无逐片设置，回退到单镜片全局 CA_mode / CA1 / CA2 字段。
    """
    mode = settings.get(f"ca_mode_{lens_index + 1}", "auto")
    if mode == "manual":
        left = settings.get(f"ca_{lens_index + 1}_left")
        right = settings.get(f"ca_{lens_index + 1}_right")
        ca1 = float(left) if left not in (None, "") else None
        ca2 = float(right) if right not in (None, "") else None
    else:
        ca1 = ca2 = None

    # Fallback: 批量出图模块使用单镜片全局 CA_mode/CA1/CA2 键
    if ca1 is None and ca2 is None:
        global_mode = settings.get("CA_mode", "auto")
        if global_mode == "manual":
            left = settings.get("CA1")
            right = settings.get("CA2")
            ca1 = float(left) if left not in (None, "") else None
            ca2 = float(right) if right not in (None, "") else None

    return ca1, ca2


def _validate_lens_page_settings(settings, lens, lens_index):
    """Validate per-page overrides before they reach drawing calculations."""
    def finite_value(key, label, default=None):
        raw = settings.get(key, default)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{label} 的值 '{raw}' 无效，请输入数字")
        if not math.isfinite(value):
            raise ValueError(f"{label} 必须是有限数值")
        return value

    ratio = finite_value("ca_ratio", "CA 自动系数", 0.94)
    if not 0 < ratio <= 1:
        raise ValueError("CA 自动系数必须大于 0 且不大于 1")

    for key, label, default in (
        ("t_tol", "厚度公差", 0.02),
        ("sag_tol", "矢高公差", 0.02),
        ("dia_tol_pos_upper", "直径定位上偏差", 0.010),
        ("dia_tol_pos_lower", "直径定位下偏差", 0.025),
        ("dia_tol_nonpos_upper", "直径非定位上偏差", 0.05),
        ("dia_tol_nonpos_lower", "直径非定位下偏差", 0.10),
    ):
        if finite_value(key, label, default) < 0:
            raise ValueError(f"{label}不能为负数")

    n_mode = settings.get("proc_N_mode", "auto")
    if n_mode not in ("auto", "manual"):
        raise ValueError("N 模式无效")
    if n_mode == "manual":
        if finite_value("proc_N_manual", "N") <= 0:
            raise ValueError("N 必须大于 0")

    chamfer_mode = settings.get(f"chamfer_mode_{lens_index + 1}", "auto")
    if chamfer_mode not in ("auto", "manual"):
        raise ValueError(f"镜片{lens_index + 1}倒角模式无效")
    if chamfer_mode == "manual":
        for side in ("left", "right"):
            key = f"chamfer_{lens_index + 1}_{side}"
            side_label = "左" if side == "left" else "右"
            if settings.get(key) in (None, ""):
                raise ValueError(f"手动模式下必须填写镜片{lens_index + 1}{side_label}侧倒角")
            if finite_value(key, f"镜片{lens_index + 1}{side_label}侧倒角") < 0:
                raise ValueError(f"镜片{lens_index + 1}{side_label}侧倒角不能为负数")
    else:
        global_chamfer_mode = settings.get("chamfer_mode", "auto")
        if global_chamfer_mode not in ("auto", "manual"):
            raise ValueError("倒角模式无效")
        if global_chamfer_mode == "manual":
            for key, label in (("chamfer_left", "左侧倒角"),
                               ("chamfer_right", "右侧倒角")):
                if finite_value(key, label) < 0:
                    raise ValueError(f"{label}不能为负数")

    ca_mode = settings.get(f"ca_mode_{lens_index + 1}", "auto")
    if ca_mode not in ("auto", "manual"):
        raise ValueError(f"镜片{lens_index + 1} CA 模式无效")
    if ca_mode == "manual":
        for side in ("left", "right"):
            key = f"ca_{lens_index + 1}_{side}"
            if settings.get(key) in (None, ""):
                side_label = "左" if side == "left" else "右"
                raise ValueError(f"手动模式下必须填写镜片{lens_index + 1}{side_label}侧 CA")
    elif settings.get("CA_mode", "auto") == "manual":
        if settings.get("CA1") in (None, "") or settings.get("CA2") in (None, ""):
            raise ValueError("手动模式下必须同时填写 CA1 和 CA2")

    ca_left, ca_right = _resolve_cemented_ca(settings, lens_index, ratio)
    for value, ad_value, side in (
        (ca_left, lens.AD_left, "左"),
        (ca_right, lens.AD_right, "右"),
    ):
        if value is None:
            continue
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"镜片{lens_index + 1}{side}侧 CA 必须是大于 0 的有限数值")
        if value > ad_value:
            raise ValueError(
                f"镜片{lens_index + 1}{side}侧 CA 不能大于对应 AD ({ad_value:g} mm)"
            )


def _validate_all_lens_page_settings(lenses, settings, page_overrides):
    if not isinstance(page_overrides, dict):
        raise ValueError("逐页加工参数 page_overrides 必须是对象")

    if len(lenses) > 1:
        try:
            ref_value = float(settings.get("cemented_ref_lens", 2))
        except (TypeError, ValueError):
            raise ValueError("胶合定位镜片必须是整数")
        if (not math.isfinite(ref_value) or not ref_value.is_integer()
                or not 1 <= int(ref_value) <= len(lenses)):
            raise ValueError(f"胶合定位镜片必须在 1~{len(lenses)} 之间")

    for index, lens in enumerate(lenses):
        lens_settings = settings.copy()
        page_index = index + 1
        page_override = page_overrides.get(
            page_index, page_overrides.get(str(page_index), {})
        )
        if not isinstance(page_override, dict):
            raise ValueError(f"第 {page_index} 页加工参数必须是对象")
        lens_settings.update(page_override)
        sapphire_surfaces = lens_settings.get("sapphire_surfaces", []) or []
        if not isinstance(sapphire_surfaces, list):
            raise ValueError("蓝宝石膜表面 sapphire_surfaces 必须是数组")
        allowed_surfaces = {
            surface
            for interface_index in range(1, len(lenses))
            for surface in (
                f"{interface_index}:S2",
                f"{interface_index + 1}:S1",
            )
        }
        invalid_surfaces = sorted(set(sapphire_surfaces) - allowed_surfaces)
        if invalid_surfaces:
            raise ValueError(f"蓝宝石膜表面无效: {', '.join(invalid_surfaces)}")
        _validate_lens_page_settings(lens_settings, lens, index)


_LENS_PAGE_PROC_DEFAULTS = {
    "proc_c_single": "60″",
    "proc_surface_defect": "60/40",
    "proc_N_mode": "auto",
    "proc_N_manual": "1.5",
    "proc_DN": "0.3",
    "proc_signature": "l.y.h",
    "proc_vendor": "CDGM",
    "proc_ranking": "01",
    "proc_molding": "Molding",
    "coat_s1_wave1": "420-680",
    "coat_s1_wave2": "680-850",
    "coat_s2_wave1": "420-680",
    "coat_s2_wave2": "680-850",
    "coat_s1_ravg1": "0.4",
    "coat_s1_ravg2": "0.8",
    "coat_s2_ravg1": "0.4",
    "coat_s2_ravg2": "0.8",
    "coat_s1_angle1": "0-15",
    "coat_s1_angle2": "0-15",
    "coat_s2_angle1": "0-15",
    "coat_s2_angle2": "0-15",
    "coat_preset": "Custom",
}


def _build_lens_page_context(cemented_data, settings, page_overrides,
                             lens_index, hide_partname=False):
    """Resolve one lens page once so preview and both PDF paths stay identical."""
    from config import auto_chamfer_by_dia

    lenses = cemented_data.lenses
    lens = lenses[lens_index]
    is_multi = len(lenses) > 1
    page_index = lens_index + 1

    lens_settings = settings.copy()
    page_override = page_overrides.get(
        page_index, page_overrides.get(str(page_index), {})
    )
    if page_override:
        lens_settings.update(page_override)

    has_outer_s1 = lens_index == 0
    has_outer_s2 = lens_index == len(lenses) - 1
    sapphire_surfaces = lens_settings.get("sapphire_surfaces", []) or []
    proc_params = {
        "part_name": "" if is_multi or hide_partname else cemented_data.part_name,
        "part_no": "" if is_multi else cemented_data.part_no,
        "glass_name": lens.glass,
        "is_cemented_single": is_multi,
        "has_outer_s1": has_outer_s1,
        "has_outer_s2": has_outer_s2,
        "sapphire_s1": f"{page_index}:S1" in sapphire_surfaces,
        "sapphire_s2": f"{page_index}:S2" in sapphire_surfaces,
        "sapphire_s1_key": f"{page_index}:S1",
        "sapphire_s2_key": f"{page_index}:S2",
    }
    for key, default in _LENS_PAGE_PROC_DEFAULTS.items():
        proc_params[key] = lens_settings.get(key, default)

    for prefix in ("wave", "ravg", "angle"):
        for group in (1, 2):
            if not has_outer_s1:
                proc_params[f"coat_s1_{prefix}{group}"] = ""
            if not has_outer_s2:
                proc_params[f"coat_s2_{prefix}{group}"] = ""

    cL, cR = _resolve_cemented_chamfer(lens_settings, lens_index)
    if cL is None or cR is None:
        if lens_settings.get("chamfer_mode", "auto") == "auto":
            if is_multi:
                cL = cR = auto_chamfer_by_dia(lens.MD)
            else:
                cL, cR = auto_chamfer(lens.MD, lens.R_left, lens.R_right)
        else:
            cL = float(lens_settings.get("chamfer_left", 0.2))
            cR = float(lens_settings.get("chamfer_right", 0.4))

    ref_index = int(lens_settings.get("cemented_ref_lens", 2)) - 1
    if is_multi and lens_index != ref_index:
        dia_upper = lens_settings.get("dia_tol_nonpos_upper", 0.05)
        dia_lower = lens_settings.get("dia_tol_nonpos_lower", 0.10)
    else:
        dia_upper = lens_settings.get(
            "dia_tol_pos_upper", lens_settings.get("dia_tol_upper", 0.010)
        )
        dia_lower = lens_settings.get(
            "dia_tol_pos_lower", lens_settings.get("dia_tol_lower", 0.025)
        )

    ca_ratio = float(lens_settings.get("ca_ratio", 0.94))
    ca1, ca2 = _resolve_cemented_ca(lens_settings, lens_index, ca_ratio)
    return {
        "settings": lens_settings,
        "proc_params": proc_params,
        "chamfer_left": cL,
        "chamfer_right": cR,
        "dia_upper": dia_upper,
        "dia_lower": dia_lower,
        "ca1": ca1,
        "ca2": ca2,
    }


def build_cemented_preview_figures(cemented_data, settings, page_overrides=None):
    """构建胶合镜片预览所需的所有 Figure：组装页 + 各单片页。
    返回: [(label, figure), ...]  其中 label 如 "整体"、"镜片1"、"镜片2"
    供预览 API 使用，避免与 PDF 导出逻辑重复。
    page_overrides: { pageIndex: { settingKey: value, ... }, ... } 逐页加工参数覆盖
    """
    lenses = cemented_data.lenses
    is_multi = len(lenses) > 1
    if not is_multi:
        return []  # 不应调用此函数

    from config import validate_cemented_lenses

    errors = validate_cemented_lenses(cemented_data.lenses)
    if errors:
        raise ValueError("; ".join(errors))

    figures = []
    if page_overrides is None:
        page_overrides = {}
    _validate_all_lens_page_settings(lenses, settings, page_overrides)

    # ── 组装页 ──
    total_pages = 1 + len(lenses)
    fig_asm = _build_assembly_page_figure(cemented_data, settings, page_no=1, total_pages=total_pages)
    figures.append(("整体", fig_asm))

    # ── 各单片页 ──
    for i, lens in enumerate(lenses):
        page_no = 2 + i
        context = _build_lens_page_context(
            cemented_data, settings, page_overrides, i
        )
        lens_settings = context["settings"]

        fig_s = _build_single_page_figure(
            lens.T, lens.R_left, lens.R_right, lens.MD, lens.AD_left, lens.AD_right,
            lens_settings.get("J_multiplier", 0.10),
            lens_settings.get("ct_offset_J", 3.0), lens_settings.get("et_offset_J", 2.0),
            lens_settings.get("sag_offset_J", 3.0), lens_settings.get("dia_offset_J", 3.0),
            lens_settings.get("ad_offset_J", 2.0), lens_settings.get("spray_gap_J", 0.1),
            context["chamfer_left"], context["chamfer_right"],
            lens_settings.get("t_tol", 0.02), lens_settings.get("sag_tol", 0.02),
            lens_settings.get("font_size", 9), lens_settings.get("arrow_scale", 1.0),
            lens_settings.get("r_offset_J", 0.8),
            context["dia_upper"], context["dia_lower"],
            context["proc_params"], lens_settings,
            ca1=context["ca1"], ca2=context["ca2"],
            page_no=page_no, total_pages=total_pages,
            hide_partname=False,
        )
        figures.append(("镜片{}".format(i + 1), fig_s))

    return figures


# ── 胶合镜片多页 PDF 导出 ────────────────────────────────────────
def export_cemented_pdf(cemented_data, settings, output_path, hide_partname=False, page_overrides=None):
    """导出胶合镜片多页 PDF（整体页 + 各单片页）
    hide_partname: 为True时表格中PartName留空（用于Mfr PDF）
    page_overrides: { pageIndex: { settingKey: value, ... }, ... } 逐页加工参数覆盖
    """
    from matplotlib.backends.backend_pdf import PdfPages
    from config import validate_cemented_lenses

    lenses=cemented_data.lenses
    errors = validate_cemented_lenses(lenses)
    if errors:
        raise ValueError("; ".join(errors))
    is_multi = len(lenses) > 1
    # 总页数：整体页(1) + 各单片页(N)
    total_pages = (1 if is_multi else 0) + len(lenses)
    if page_overrides is None:
        page_overrides = {}
    _validate_all_lens_page_settings(lenses, settings, page_overrides)

    with PdfPages(output_path) as pdf:
        # ── 第 1 页：整体组装图（仅双胶合/三胶合） ──
        if is_multi:
            fig_asm=_build_assembly_page_figure(cemented_data, settings, page_no=1, total_pages=total_pages, hide_partname=hide_partname)
            pdf.savefig(fig_asm); plt.close(fig_asm)

        # ── 第 2~N 页：各单片单独出图 ──
        for i, lens in enumerate(lenses):
            page_no = (2 if is_multi else 1) + i
            context = _build_lens_page_context(
                cemented_data, settings, page_overrides, i, hide_partname
            )
            lens_settings = context["settings"]
            fig_s = _build_single_page_figure(
                lens.T, lens.R_left, lens.R_right, lens.MD, lens.AD_left, lens.AD_right,
                lens_settings.get("J_multiplier",0.10),
                lens_settings.get("ct_offset_J",3.0), lens_settings.get("et_offset_J",2.0),
                lens_settings.get("sag_offset_J",3.0), lens_settings.get("dia_offset_J",3.0),
                lens_settings.get("ad_offset_J",2.0), lens_settings.get("spray_gap_J",0.1),
                context["chamfer_left"], context["chamfer_right"],
                lens_settings.get("t_tol",0.02), lens_settings.get("sag_tol",0.02),
                lens_settings.get("font_size",9), lens_settings.get("arrow_scale",1.0),
                lens_settings.get("r_offset_J",0.8),
                context["dia_upper"], context["dia_lower"],
                context["proc_params"], lens_settings,
                ca1=context["ca1"], ca2=context["ca2"],
                page_no=page_no, total_pages=total_pages,
                hide_partname=hide_partname,
            )
            pdf.savefig(fig_s); plt.close(fig_s)


# ── 胶合镜片 PDF 字节生成（供预览 API 使用）────────────────────────
def build_cemented_pdf_bytes(cemented_data, settings, hide_partname=False, page_overrides=None):
    """生成胶合镜片多页 PDF 字节，返回 bytes。
    与 export_cemented_pdf() 逻辑相同，但输出到 BytesIO 而非文件。
    """
    from matplotlib.backends.backend_pdf import PdfPages
    from config import validate_cemented_lenses

    buf = io.BytesIO()
    lenses = cemented_data.lenses
    errors = validate_cemented_lenses(lenses)
    if errors:
        raise ValueError("; ".join(errors))
    is_multi = len(lenses) > 1
    total_pages = (1 if is_multi else 0) + len(lenses)
    if page_overrides is None:
        page_overrides = {}
    _validate_all_lens_page_settings(lenses, settings, page_overrides)

    with PdfPages(buf) as pdf:
        if is_multi:
            fig_asm = _build_assembly_page_figure(cemented_data, settings, page_no=1, total_pages=total_pages, hide_partname=hide_partname)
            fig_asm.set_dpi(72)  # PDF 坐标对齐：确保内容流缩放因子为 1.0
            pdf.savefig(fig_asm); plt.close(fig_asm)

        for i, lens in enumerate(lenses):
            page_no = (2 if is_multi else 1) + i
            context = _build_lens_page_context(
                cemented_data, settings, page_overrides, i, hide_partname
            )
            lens_settings = context["settings"]
            fig_s = _build_single_page_figure(
                lens.T, lens.R_left, lens.R_right, lens.MD, lens.AD_left, lens.AD_right,
                lens_settings.get("J_multiplier", 0.10),
                lens_settings.get("ct_offset_J", 3.0), lens_settings.get("et_offset_J", 2.0),
                lens_settings.get("sag_offset_J", 3.0), lens_settings.get("dia_offset_J", 3.0),
                lens_settings.get("ad_offset_J", 2.0), lens_settings.get("spray_gap_J", 0.1),
                context["chamfer_left"], context["chamfer_right"],
                lens_settings.get("t_tol", 0.02), lens_settings.get("sag_tol", 0.02),
                lens_settings.get("font_size", 9), lens_settings.get("arrow_scale", 1.0),
                lens_settings.get("r_offset_J", 0.8),
                context["dia_upper"], context["dia_lower"],
                context["proc_params"], lens_settings,
                ca1=context["ca1"], ca2=context["ca2"],
                page_no=page_no, total_pages=total_pages,
                hide_partname=hide_partname,
            )
            fig_s.set_dpi(72)  # PDF 坐标对齐：确保内容流缩放因子为 1.0
            pdf.savefig(fig_s); plt.close(fig_s)

    return buf.getvalue()
