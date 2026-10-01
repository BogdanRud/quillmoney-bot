"""
Генератор изображений для Telegram Stories — тематика заработка/финансов.
1080×1920, тёмный градиент, индиго-фиолетовая палитра.
"""
import io
import math
import os
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

STORY_W = 1080
STORY_H = 1920

_FONT_CACHE: dict = {}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = f"{'b' if bold else 'r'}_{size}"
    if key not in _FONT_CACHE:
        candidates = (
            [
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/ARIALBD.TTF",
            ]
            if bold
            else [
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/ARIAL.TTF",
            ]
        )
        # fallback: bold → regular
        candidates += [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/ARIAL.TTF",
        ]
        for p in candidates:
            try:
                _FONT_CACHE[key] = ImageFont.truetype(p, size)
                return _FONT_CACHE[key]
            except Exception:
                continue
        _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


# ── Вспомогательные рисовалки ────────────────────────────────

def _gradient_bg(img: Image.Image):
    """Тёмный вертикальный градиент с фиолетовым оттенком."""
    draw = ImageDraw.Draw(img)
    for y in range(STORY_H):
        t = y / STORY_H
        r = int(15 + t * 12)
        g = int(10 + t * 8)
        b = int(40 + t * 15)
        draw.line([(0, y), (STORY_W, y)], fill=(r, g, b))


def _draw_glow(draw: ImageDraw.ImageDraw, cx: int, cy: int, radius: int, color: tuple, alpha: int = 30):
    """Мягкое свечение — полупрозрачные конц�ческие круги."""
    for i in range(radius, 0, -4):
        a = int(alpha * (i / radius))
        c = (*color, a)
        draw.ellipse([cx - i, cy - i, cx + i, cy + i], fill=c)


def _draw_coin(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, color: tuple):
    """Стилизованная монета."""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    inner = int(r * 0.65)
    draw.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], fill=(*color[:3], 60) if len(color) == 4 else None)
    f = _font(int(r * 0.9), bold=True)
    draw.text((cx, cy), "$", fill="#ffffff", font=f, anchor="mm")


def _draw_card(draw: ImageDraw.ImageDraw, x1: int, y1: int, x2: int, y2: int, radius: int = 28):
    """Полупрозрачная карточка с тонкой рамкой."""
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=(255, 255, 255, 12))
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, outline=(255, 255, 255, 25), width=1)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Перенос текста по словам."""
    words = text.split()
    lines, current = [], ""
    for w in words:
        test = f"{current} {w}".strip()
        bbox = font.getbbox(test)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = w
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def _center_text(draw, y, text, font, fill, width=STORY_W):
    draw.text((width // 2, y), text, fill=fill, font=font, anchor="mt")


# ── Основная функция ────────────────────────────────────────

def generate_story(
    first_name: str,
    referral_link: str,
    referral_code: str,
    bot_username: str,
) -> io.BytesIO:
    img = Image.new("RGBA", (STORY_W, STORY_H), (0, 0, 0, 255))
    _gradient_bg(img)
    draw = ImageDraw.Draw(img)

    # ── Свечения ──────────────────────────────────────────────
    _draw_glow(draw, 200, 300, 350, (99, 102, 241), 18)
    _draw_glow(draw, 880, 1400, 300, (168, 85, 247), 15)
    _draw_glow(draw, 540, 960, 500, (79, 70, 229), 8)

    # ── Декоративные монеты ───────────────────────────────────
    coins = [(120, 550, 35), (950, 480, 28), (180, 1300, 22),
             (900, 1100, 32), (80, 1650, 25), (980, 700, 20)]
    for cx, cy, r in coins:
        alpha = random.randint(25, 55)
        _draw_coin(draw, cx, cy, r, (250, 204, 21, alpha))

    # ── Заголовок ─────────────────────────────────────────────
    y = 120
    _center_text(draw, y, "QUILL MONEY", _font(42, bold=True), (165, 180, 252, 200))
    y += 65
    draw.line([(STORY_W // 2 - 120, y), (STORY_W // 2 + 120, y)], fill=(165, 180, 252, 80), width=2)
    y += 30

    # ── Основной баннер ───────────────────────────────────────
    # Пробуем загрузить баннер
    banner_path = None
    assets = Path(__file__).parent.parent / "Degin_Quill_Stidio"
    for fname in ["679867y97.png", "5678657654.png", "5796780.png", "IMG_20250819_170138_788.jpg"]:
        p = assets / fname
        if p.exists():
            banner_path = p
            break

    if banner_path:
        try:
            banner = Image.open(banner_path).convert("RGBA")
            bw, bh = banner.size
            scale = min(STORY_W * 0.85 / bw, 400 / bh)
            nw, nh = int(bw * scale), int(bh * scale)
            banner = banner.resize((nw, nh), Image.LANCZOS)
            # Слегка прозрачный
            alpha_layer = Image.new("RGBA", banner.size, (255, 255, 255, 200))
            banner = Image.alpha_composite(banner, alpha_layer)
            px = (STORY_W - nw) // 2
            img.paste(banner, (px, y + 30), banner)
            y += nh + 80
        except Exception:
            pass
    else:
        y += 40

    # ── Блок «Зарабатывай» ────────────────────────────────────
    y += 20
    _draw_card(draw, 60, y, STORY_W - 60, y + 280, 30)

    font_big = _font(64, bold=True)
    _center_text(draw, y + 35, "Зарабатывай", font_big, "#ffffff")
    _center_text(draw, y + 115, "вместе со мной!", font_big, "#ffffff")

    font_med = _font(38, bold=False)
    _center_text(draw, y + 210, "Подписывайся на каналы — получай деньги", font_med, (180, 180, 210, 220))

    y += 320

    # ── Блок с именем ─────────────────────────────────────────
    display_name = first_name or "друг"
    font_name = _font(48, bold=True)
    _center_text(draw, y, f"👋 {display_name}", font_name, (196, 181, 253, 240))
    y += 80

    # ── Преимущества ──────────────────────────────────────────
    benefits = [
        ("💰", "Деньги за каждое задание"),
        ("⏳", "Верификация за 7 дней"),
        ("💸", "Быстрый вывод средств"),
        ("🎁", "Бонусы за друзей"),
    ]
    font_benefit = _font(36, bold=False)
    icon_font = _font(40, bold=False)
    for icon, text in benefits:
        draw.text((120, y), icon, font=icon_font, anchor="lt")
        draw.text((190, y + 4), text, fill=(210, 210, 230, 230), font=font_benefit)
        y += 62

    y += 30

    # ── Реферальная ссылка — яркий блок ───────────────────────
    card_top = y
    card_bot = y + 260
    # Градиентная заливка карточки
    for row in range(card_top, card_bot):
        t = (row - card_top) / (card_bot - card_top)
        r = int(55 + t * 20)
        g = int(40 + t * 15)
        b = int(160 + t * 40)
        draw.line([(80, row), (STORY_W - 80, row)], fill=(r, g, b, 220))
    draw.rounded_rectangle([80, card_top, STORY_W - 80, card_bot], radius=28, outline=(165, 180, 252, 120), width=2)

    font_ref_label = _font(34, bold=False)
    _center_text(draw, card_top + 30, "Твоя реферальная ссылка", font_ref_label, (200, 190, 255, 200))

    # Ссылка
    link_text = f"t.me/{bot_username}?start={referral_code}"
    font_link = _font(40, bold=True)
    # Фон под ссылкой
    link_bbox = font_link.getbbox(link_text)
    link_w = link_bbox[2] - link_bbox[0]
    lx = (STORY_W - link_w) // 2 - 16
    ly = card_top + 90
    draw.rounded_rectangle([lx, ly - 4, lx + link_w + 32, ly + 56], radius=12, fill=(0, 0, 0, 100))
    draw.text((STORY_W // 2, ly + 22), link_text, fill="#ffffff", font=font_link, anchor="mm")

    # Кнопка-призыв
    btn_y = card_top + 185
    btn_w = 380
    btn_h = 56
    bx = (STORY_W - btn_w) // 2
    draw.rounded_rectangle([bx, btn_y, bx + btn_w, btn_y + btn_h], radius=28, fill=(99, 102, 241, 240))
    font_btn = _font(32, bold=True)
    draw.text((STORY_W // 2, btn_y + btn_h // 2), "🚀 Начать зарабатывать", fill="#ffffff", font=font_btn, anchor="mm")

    y = card_bot + 60

    # ── Нижний текст ──────────────────────────────────────────
    font_bottom = _font(30, bold=False)
    lines_bottom = [
        "Нажми на ссылку и начни",
        "зарабатывать прямо сейчас!",
    ]
    for line in lines_bottom:
        _center_text(draw, y, line, font_bottom, (140, 140, 170, 180))
        y += 42

    # ── Лого внизу ────────────────────────────────────────────
    _center_text(draw, STORY_H - 100, "QUILL STUDIO", _font(24, bold=True), (100, 100, 140, 120))

    # ── Сохраняем ─────────────────────────────────────────────
    rgb = Image.new("RGB", img.size, (0, 0, 0))
    rgb.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    rgb.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
