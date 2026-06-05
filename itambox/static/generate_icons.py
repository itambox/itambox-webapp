import os
from PIL import Image, ImageDraw

def generate_icon(size, output_path):
    # Create a new transparent image
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Scale factor from 40x40 design grid
    scale = size / 40.0
    
    # 1. Draw rounded rectangle background
    # Design grid rect: x=2, y=2, width=36, height=36, rx=9
    rect_x0 = 2 * scale
    rect_y0 = 2 * scale
    rect_x1 = 38 * scale
    rect_y1 = 38 * scale
    radius = 9 * scale
    
    # Color: #2fb344 (RGB: 47, 179, 68)
    green_color = (47, 179, 68, 255)
    draw.rounded_rectangle([rect_x0, rect_y0, rect_x1, rect_y1], radius=radius, fill=green_color)
    
    # 2. Draw isometric box outline
    # Design grid coordinates scaled
    pts = [
        (20 * scale, 11 * scale),  # Top
        (30 * scale, 16 * scale),  # Right Top
        (30 * scale, 25 * scale),  # Right Bottom
        (20 * scale, 30 * scale),  # Bottom
        (10 * scale, 25 * scale),  # Left Bottom
        (10 * scale, 16 * scale),  # Left Top
    ]
    
    stroke_width = int(round(2.5 * scale))
    white_color = (255, 255, 255, 255)
    
    # Draw outer hexagon
    draw.polygon(pts, outline=white_color, width=stroke_width)
    
    # Draw inner lines radiating from center (20, 20)
    center = (20 * scale, 20 * scale)
    draw.line([center, pts[1]], fill=white_color, width=stroke_width)  # Center to Right Top
    draw.line([center, pts[5]], fill=white_color, width=stroke_width)  # Center to Left Top
    draw.line([center, pts[3]], fill=white_color, width=stroke_width)  # Center to Bottom
    
    # Save image
    img.save(output_path, 'PNG')
    print(f"Generated {size}x{size} icon at {output_path}")

def generate_svg(output_path):
    svg_content = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512" fill="none">
  <!-- Icon Mark (Solid Green Rounded Square + White Isometric Box Silhouette) -->
  <rect width="512" height="512" rx="128" fill="#2fb344"/>
  <path d="M256 141 L384 205 L384 320 L256 384 L128 320 L128 205 Z" stroke="#ffffff" stroke-width="32" stroke-linejoin="round" stroke-linecap="round"/>
  <path d="M256 256 L384 205" stroke="#ffffff" stroke-width="32" stroke-linejoin="round" stroke-linecap="round"/>
  <path d="M256 256 L128 205" stroke="#ffffff" stroke-width="32" stroke-linejoin="round" stroke-linecap="round"/>
  <path d="M256 256 L256 384" stroke="#ffffff" stroke-width="32" stroke-linejoin="round" stroke-linecap="round"/>
</svg>
"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(svg_content)
    print(f"Generated SVG icon at {output_path}")

if __name__ == '__main__':
    static_dir = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.join(static_dir, 'dist')
    os.makedirs(dist_dir, exist_ok=True)
    
    generate_icon(192, os.path.join(dist_dir, 'logo-icon-192.png'))
    generate_icon(512, os.path.join(dist_dir, 'logo-icon-512.png'))
    generate_svg(os.path.join(dist_dir, 'logo-icon.svg'))
