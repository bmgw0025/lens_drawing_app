"""Generate 5 icon design variations for Lens Drawing app."""
from PIL import Image, ImageDraw, ImageFont
import math

SIZE = 512  # Work at 512 for quality, downscale later
CORNER_R = 96

def rounded_rect_mask(size, radius):
    """Create a rounded rectangle mask."""
    mask = Image.new('L', size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (size[0]-1, size[1]-1)], radius=radius, fill=255)
    return mask

def gradient_bg(w, h, color_top, color_bot):
    """Create vertical gradient image."""
    img = Image.new('RGB', (w, h))
    for y in range(h):
        r = int(color_top[0] + (color_bot[0] - color_top[0]) * y / h)
        g = int(color_top[1] + (color_bot[1] - color_top[1]) * y / h)
        b = int(color_top[2] + (color_bot[2] - color_top[2]) * y / h)
        for x in range(w):
            img.putpixel((x, y), (r, g, b))
    return img

def draw_biconvex_lens(draw, cx, cy, half_w, half_h, fill_color, outline_color, outline_w=4):
    """Draw a biconvex lens shape using ellipse arcs."""
    # Left surface (convex outward)
    bbox_left = (cx - half_w * 2, cy - half_h, cx, cy + half_h)
    # Right surface (convex outward)
    bbox_right = (cx, cy - half_h, cx + half_w * 2, cy + half_h)
    
    # Draw filled lens body
    # Use polygon approximation for the lens shape
    points = []
    # Right convex surface
    for i in range(80):
        t = -math.pi/2 + math.pi * i / 79
        x = cx + half_w * 0.35 * math.cos(t) + half_w * 0.65
        y = cy + half_h * math.sin(t)
        points.append((x, y))
    # Left convex surface (reversed)
    for i in range(80):
        t = math.pi/2 + math.pi * i / 79
        x = cx + half_w * 0.35 * math.cos(t) - half_w * 0.65
        y = cy + half_h * math.sin(t)
        points.append((x, y))
    
    draw.polygon(points, fill=fill_color, outline=outline_color, width=outline_w)
    return points

def draw_highlight(draw, cx, cy, half_w, half_h):
    """Draw a highlight reflection on the lens."""
    # Small elliptical highlight on the upper-left area of the lens
    hx = cx - half_w * 0.25
    hy = cy - half_h * 0.35
    hw = half_w * 0.15
    hh = half_h * 0.2
    draw.ellipse([(hx-hw, hy-hh), (hx+hw, hy+hh)], fill=(255, 255, 255, 80))


def draw_arrow(draw, start, end, color=(0,0,0), width=3, head_size=10):
    """Draw an arrow from start to end."""
    draw.line([start, end], fill=color, width=width)
    # Arrowhead
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.sqrt(dx*dx + dy*dy)
    if length == 0: return
    ux, uy = dx/length, dy/length
    px, py = -uy, ux
    tip = end
    b1 = (end[0] - head_size*ux + head_size*0.4*px, end[1] - head_size*uy + head_size*0.4*py)
    b2 = (end[0] - head_size*ux - head_size*0.4*px, end[1] - head_size*uy - head_size*0.4*py)
    draw.polygon([tip, b1, b2], fill=color)


# ═══════════════════════════════════════════════════════
# Style 1: 参考图风格（白底 + 灰阶透镜 + 标注，无水印）
# ═══════════════════════════════════════════════════════
def make_style_1():
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    bg = Image.new('RGBA', (SIZE, SIZE), (255, 255, 255, 255))
    mask = rounded_rect_mask((SIZE, SIZE), CORNER_R)
    img = Image.composite(bg, img, mask)
    draw = ImageDraw.Draw(img)
    
    cx, cy = SIZE//2, SIZE//2 - 10
    hw, hh = 100, 170
    
    # Lens body with grayscale shading
    points = []
    for i in range(80):
        t = -math.pi/2 + math.pi * i / 79
        x = cx + hw * 0.35 * math.cos(t) + hw * 0.65
        y = cy + hh * math.sin(t)
        points.append((x, y))
    for i in range(80):
        t = math.pi/2 + math.pi * i / 79
        x = cx + hw * 0.35 * math.cos(t) - hw * 0.65
        y = cy + hh * math.sin(t)
        points.append((x, y))
    
    # Fill with gray gradient effect
    draw.polygon(points, fill=(200, 200, 205), outline=(40, 40, 40), width=5)
    # Highlight
    draw_highlight(draw, cx, cy, hw, hh)
    
    # Try to load a font for annotations
    try:
        font = ImageFont.truetype("arial.ttf", 28)
        font_sm = ImageFont.truetype("arial.ttf", 22)
    except:
        font = ImageFont.load_default()
        font_sm = font
    
    # R1 annotation (left)
    draw.text((60, cy - 50), "R1", fill=(30, 30, 30), font=font)
    draw_arrow(draw, (110, cy - 30), (cx - hw*0.65 - 5, cy - 20), color=(30,30,30), width=3)
    
    # R2 annotation (right)
    draw.text((SIZE - 130, cy - 50), "R2", fill=(30, 30, 30), font=font)
    draw_arrow(draw, (SIZE - 100, cy - 30), (cx + hw*0.65 + 5, cy - 20), color=(30,30,30), width=3)
    
    # T annotation (bottom)
    draw.text((cx - 10, cy + hh + 30), "T", fill=(30, 30, 30), font=font)
    draw_arrow(draw, (cx, cy + hh + 25), (cx, cy + 10), color=(30,30,30), width=3)
    
    return img


# ═══════════════════════════════════════════════════════
# Style 2: 现代蓝色渐变 + 白色透镜轮廓
# ═══════════════════════════════════════════════════════
def make_style_2():
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    bg = gradient_bg(SIZE, SIZE, (20, 80, 200), (10, 160, 220))
    bg_rgba = bg.convert('RGBA')
    mask = rounded_rect_mask((SIZE, SIZE), CORNER_R)
    bg_rgba = Image.composite(bg_rgba, Image.new('RGBA', (SIZE, SIZE), (0,0,0,0)), mask)
    img = bg_rgba
    draw = ImageDraw.Draw(img)
    
    cx, cy = SIZE//2, SIZE//2
    hw, hh = 110, 190
    
    # Lens outline in white with glow effect
    points = []
    for i in range(80):
        t = -math.pi/2 + math.pi * i / 79
        x = cx + hw * 0.4 * math.cos(t) + hw * 0.6
        y = cy + hh * math.sin(t)
        points.append((x, y))
    for i in range(80):
        t = math.pi/2 + math.pi * i / 79
        x = cx + hw * 0.4 * math.cos(t) - hw * 0.6
        y = cy + hh * math.sin(t)
        points.append((x, y))
    
    # Semi-transparent fill
    overlay = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.polygon(points, fill=(255, 255, 255, 40))
    odraw.line(points + [points[0]], fill=(255, 255, 255, 220), width=5)
    
    # Glow effect (slightly larger, very transparent)
    glow = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.line(points + [points[0]], fill=(150, 220, 255, 60), width=12)
    
    img = Image.alpha_composite(img, glow)
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    
    # Optical axis
    draw.line([(cx - hw - 40, cy), (cx + hw + 40, cy)], fill=(255, 255, 255, 100), width=2)
    
    return img


# ═══════════════════════════════════════════════════════
# Style 3: 深色科技主题
# ═══════════════════════════════════════════════════════
def make_style_3():
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    bg = gradient_bg(SIZE, SIZE, (15, 20, 40), (25, 35, 65))
    bg_rgba = bg.convert('RGBA')
    mask = rounded_rect_mask((SIZE, SIZE), CORNER_R)
    bg_rgba = Image.composite(bg_rgba, Image.new('RGBA', (SIZE, SIZE), (0,0,0,0)), mask)
    img = bg_rgba
    draw = ImageDraw.Draw(img)
    
    # Subtle grid pattern
    for x in range(0, SIZE, 32):
        draw.line([(x, 0), (x, SIZE)], fill=(60, 80, 120, 30), width=1)
    for y in range(0, SIZE, 32):
        draw.line([(0, y), (SIZE, y)], fill=(60, 80, 120, 30), width=1)
    
    cx, cy = SIZE//2, SIZE//2
    hw, hh = 105, 185
    
    points = []
    for i in range(80):
        t = -math.pi/2 + math.pi * i / 79
        x = cx + hw * 0.38 * math.cos(t) + hw * 0.62
        y = cy + hh * math.sin(t)
        points.append((x, y))
    for i in range(80):
        t = math.pi/2 + math.pi * i / 79
        x = cx + hw * 0.38 * math.cos(t) - hw * 0.62
        y = cy + hh * math.sin(t)
        points.append((x, y))
    
    # Outer glow
    glow = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.line(points + [points[0]], fill=(0, 180, 255, 40), width=16)
    img = Image.alpha_composite(img, glow)
    
    # Inner glow
    glow2 = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    gdraw2 = ImageDraw.Draw(glow2)
    gdraw2.line(points + [points[0]], fill=(0, 200, 255, 80), width=8)
    img = Image.alpha_composite(img, glow2)
    
    # Bright edge
    overlay = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.polygon(points, fill=(0, 150, 220, 20))
    odraw.line(points + [points[0]], fill=(100, 230, 255, 220), width=4)
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    
    # Light beam lines passing through lens
    beam_color = (0, 200, 255, 60)
    for dy in [-60, -30, 0, 30, 60]:
        draw.line([(40, cy + dy), (cx - hw*0.6, cy + dy)], fill=beam_color, width=2)
        draw.line([(cx + hw*0.6, cy + dy), (SIZE - 40, cy + dy*0.3)], fill=beam_color, width=2)
    
    return img


# ═══════════════════════════════════════════════════════
# Style 4: 蓝紫色专业工程风（带标注线）
# ═══════════════════════════════════════════════════════
def make_style_4():
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    bg = gradient_bg(SIZE, SIZE, (30, 50, 120), (50, 80, 160))
    bg_rgba = bg.convert('RGBA')
    mask = rounded_rect_mask((SIZE, SIZE), CORNER_R)
    bg_rgba = Image.composite(bg_rgba, Image.new('RGBA', (SIZE, SIZE), (0,0,0,0)), mask)
    img = bg_rgba
    draw = ImageDraw.Draw(img)
    
    cx, cy = SIZE//2, SIZE//2
    hw, hh = 100, 180
    
    # Lens with semi-transparent blue fill
    points = []
    for i in range(80):
        t = -math.pi/2 + math.pi * i / 79
        x = cx + hw * 0.35 * math.cos(t) + hw * 0.65
        y = cy + hh * math.sin(t)
        points.append((x, y))
    for i in range(80):
        t = math.pi/2 + math.pi * i / 79
        x = cx + hw * 0.35 * math.cos(t) - hw * 0.65
        y = cy + hh * math.sin(t)
        points.append((x, y))
    
    overlay = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.polygon(points, fill=(100, 160, 255, 60))
    odraw.line(points + [points[0]], fill=(200, 230, 255, 230), width=4)
    odraw.line([(cx, cy - hh - 30), (cx, cy + hh + 30)], fill=(200, 230, 255, 80), width=2)  # optical axis
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    
    # Dimension lines (engineering style)
    line_color = (180, 210, 255, 180)
    # R1 dimension
    r1_x = cx - hw*0.65 - 20
    draw.line([(r1_x, cy - hh*0.6), (r1_x, cy + hh*0.6)], fill=line_color, width=2)
    draw.line([(r1_x - 10, cy - hh*0.6), (r1_x + 10, cy - hh*0.6)], fill=line_color, width=2)
    draw.line([(r1_x - 10, cy + hh*0.6), (r1_x + 10, cy + hh*0.6)], fill=line_color, width=2)
    
    # R2 dimension
    r2_x = cx + hw*0.65 + 20
    draw.line([(r2_x, cy - hh*0.6), (r2_x, cy + hh*0.6)], fill=line_color, width=2)
    draw.line([(r2_x - 10, cy - hh*0.6), (r2_x + 10, cy - hh*0.6)], fill=line_color, width=2)
    draw.line([(r2_x - 10, cy + hh*0.6), (r2_x + 10, cy + hh*0.6)], fill=line_color, width=2)
    
    # T dimension
    t_y = cy + hh + 40
    draw.line([(cx - hw*0.15, t_y), (cx + hw*0.15, t_y)], fill=line_color, width=2)
    draw.line([(cx - hw*0.15, t_y - 10), (cx - hw*0.15, t_y + 10)], fill=line_color, width=2)
    draw.line([(cx + hw*0.15, t_y - 10), (cx + hw*0.15, t_y + 10)], fill=line_color, width=2)
    
    return img


# ═══════════════════════════════════════════════════════
# Style 5: 极简几何风
# ═══════════════════════════════════════════════════════
def make_style_5():
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    bg = gradient_bg(SIZE, SIZE, (45, 55, 72), (65, 75, 95))
    bg_rgba = bg.convert('RGBA')
    mask = rounded_rect_mask((SIZE, SIZE), CORNER_R)
    bg_rgba = Image.composite(bg_rgba, Image.new('RGBA', (SIZE, SIZE), (0,0,0,0)), mask)
    img = bg_rgba
    draw = ImageDraw.Draw(img)
    
    cx, cy = SIZE//2, SIZE//2
    
    # Two overlapping circles to form a biconvex lens shape
    r = 200
    offset = 140
    
    # Left circle arc (right portion forms left lens surface)
    # Right circle arc (left portion forms right lens surface)
    
    # Simple approach: two elliptical arcs
    # Left surface
    bbox_l = (cx - offset - r, cy - r, cx - offset + r, cy + r)
    bbox_r = (cx + offset - r, cy - r, cx + offset + r, cy + r)
    
    # Draw the lens as the intersection area
    overlay = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    
    # Create lens shape manually
    points = []
    # Right arc (from right circle, left portion)
    for i in range(100):
        angle = -70 + 140 * i / 99  # degrees
        rad = math.radians(angle)
        x = (cx + offset) + r * math.cos(rad)
        y = cy + r * math.sin(rad)
        points.append((x, y))
    # Left arc (from left circle, right portion)
    for i in range(100):
        angle = 70 + 140 * i / 99  # degrees
        rad = math.radians(angle)
        x = (cx - offset) + r * math.cos(rad)
        y = cy + r * math.sin(rad)
        points.append((x, y))
    
    odraw.polygon(points, fill=(255, 255, 255, 25))
    odraw.line(points + [points[0]], fill=(255, 255, 255, 180), width=3)
    
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    
    # Small focal point dots
    draw.ellipse([(cx - 4, cy - 4), (cx + 4, cy + 4)], fill=(255, 200, 100, 200))
    
    return img


# ═══════════════════════════════════════════════════════
# Generate all styles
# ═══════════════════════════════════════════════════════
print("Generating icon styles...")

styles = {
    '1_classic': make_style_1,
    '2_blue_gradient': make_style_2,
    '3_dark_tech': make_style_3,
    '4_engineering': make_style_4,
    '5_minimal': make_style_5,
}

for name, func in styles.items():
    img = func()
    # Save PNG preview
    img.save(f'icon_style_{name}.png', 'PNG')
    # Save ICO
    img_256 = img.resize((256, 256), Image.LANCZOS)
    img_256.save(f'icon_style_{name}.ico', format='ICO', 
                 sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    print(f"  Style {name}: PNG + ICO created")

print("\nAll 5 icon styles generated!")
print("Files:")
for name in styles:
    print(f"  icon_style_{name}.png / .ico")
