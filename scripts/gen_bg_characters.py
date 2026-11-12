"""배경용 오리지널 마스코트 24종 타일 SVG를 만든다(공식 캐릭터 아님).

사용: python scripts/gen_bg_characters.py            # 타일 SVG를 표준출력으로
      python scripts/gen_bg_characters.py --apply    # src/static/index.html 의 배경 CSS 변수를 갱신
모양 12가지 x 색·표정·장신구 변형으로 24종을 만든다. 모양 이름: 하트핑, 별핑, 동글핑, 물방울핑, 꽃핑, 구름핑,
다이아핑, 토끼핑, 고양이핑, 곰핑, 나비핑, 버섯핑.
"""
import math
import random
import re
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
random.seed(7)


def hsl(h, s, l):
    return f"hsl({h % 360},{s}%,{l}%)"


def star_pts(cx, cy, ro, ri):
    pts = []
    for i in range(10):
        r = ro if i % 2 == 0 else ri
        a = -math.pi / 2 + i * math.pi / 5
        pts.append(f"{cx + r * math.cos(a):.1f},{cy + r * math.sin(a):.1f}")
    return " ".join(pts)


def body(kind, f, e):
    st = f'fill="{f}" stroke="{e}" stroke-width="3.5" stroke-linejoin="round"'
    if kind == 0:  # 하트
        return f'<path d="M50 90C8 62 10 26 33 24c10-1 17 6 17 13 0-7 7-14 17-13 23 2 25 38-17 66z" {st}/>'
    if kind == 1:  # 별
        return f'<polygon points="{star_pts(50, 54, 46, 24)}" {st}/>'
    if kind == 2:  # 동글
        return f'<circle cx="50" cy="54" r="35" {st}/>'
    if kind == 3:  # 물방울
        return f'<path d="M50 10C50 10 20 44 20 66a30 30 0 0 0 60 0C80 44 50 10 50 10z" {st}/>'
    if kind == 4:  # 꽃
        angles = [-math.pi / 2 + i * 2 * math.pi / 5 for i in range(5)]
        petals = "".join(f'<circle cx="{50 + 25 * math.cos(a):.1f}" cy="{52 + 25 * math.sin(a):.1f}" r="19" {st}/>' for a in angles)
        return petals + f'<circle cx="50" cy="52" r="24" fill="{f}"/>'
    if kind == 5:  # 구름
        return (f'<circle cx="28" cy="62" r="20" {st}/><circle cx="72" cy="62" r="20" {st}/><circle cx="50" cy="46" r="26" {st}/>'
                f'<rect x="28" y="62" width="44" height="20" fill="{f}"/>')
    if kind == 6:  # 다이아
        return f'<polygon points="50,8 90,52 50,94 10,52" {st}/>'
    if kind == 7:  # 토끼
        return (f'<ellipse cx="34" cy="20" rx="10" ry="22" {st}/><ellipse cx="66" cy="20" rx="10" ry="22" {st}/>'
                f'<ellipse cx="34" cy="22" rx="4.5" ry="14" fill="#ffc2dd"/><ellipse cx="66" cy="22" rx="4.5" ry="14" fill="#ffc2dd"/>'
                f'<circle cx="50" cy="62" r="33" {st}/>')
    if kind == 8:  # 고양이
        return (f'<polygon points="18,46 22,8 48,28" {st}/><polygon points="82,46 78,8 52,28" {st}/>'
                f'<polygon points="23,36 25,17 38,28" fill="#ffc2dd"/><polygon points="77,36 75,17 62,28" fill="#ffc2dd"/>'
                f'<ellipse cx="50" cy="60" rx="36" ry="32" {st}/>')
    if kind == 9:  # 곰
        return (f'<circle cx="22" cy="28" r="13" {st}/><circle cx="78" cy="28" r="13" {st}/>'
                f'<circle cx="22" cy="28" r="6" fill="#ffc2dd"/><circle cx="78" cy="28" r="6" fill="#ffc2dd"/>'
                f'<circle cx="50" cy="58" r="35" {st}/>')
    if kind == 10:  # 나비
        return (f'<ellipse cx="24" cy="44" rx="22" ry="28" {st} transform="rotate(-18 24 44)"/>'
                f'<ellipse cx="76" cy="44" rx="22" ry="28" {st} transform="rotate(18 76 44)"/>'
                f'<ellipse cx="32" cy="76" rx="14" ry="16" {st}/><ellipse cx="68" cy="76" rx="14" ry="16" {st}/>'
                f'<circle cx="50" cy="54" r="22" fill="#fff" stroke="{e}" stroke-width="3.5"/>')
    return (f'<rect x="32" y="52" width="36" height="40" rx="14" fill="#fff6ea" stroke="{e}" stroke-width="3.5"/>'  # 버섯
            f'<path d="M8 56a42 38 0 0 1 84 0z" {st}/>'
            f'<circle cx="28" cy="40" r="6" fill="#fff"/><circle cx="52" cy="26" r="7" fill="#fff"/><circle cx="74" cy="42" r="5" fill="#fff"/>')


FACE = [(54, 13), (56, 11), (54, 13), (66, 11), (52, 13), (58, 13), (52, 13), (66, 12), (62, 13), (62, 13), (54, 10), (72, 8)]
ACC_AT = [(70, 24), (72, 16), (70, 26), (62, 24), (74, 18), (66, 30), (68, 22), (60, 2), (30, 24), (74, 24), (50, 24), (50, 14)]


def face(i, kind, dark):
    ey, ex = FACE[kind]
    s = i % 3
    if s == 0:
        eyes = "".join(
            f'<ellipse cx="{50 + d * ex}" cy="{ey}" rx="5.2" ry="6.8" fill="#fff" stroke="{dark}" stroke-width="1.4"/>'
            f'<ellipse cx="{50 + d * ex + 0.8}" cy="{ey + 1.4}" rx="3.2" ry="4.6" fill="{dark}"/>'
            f'<circle cx="{50 + d * ex + 1.8}" cy="{ey - 1}" r="1.5" fill="#fff"/>' for d in (-1, 1))
    elif s == 1:
        eyes = "".join(
            f'<path d="M{50 + d * ex - 5} {ey + 2}q5 -8 10 0" fill="none" stroke="{dark}" stroke-width="2.6" stroke-linecap="round"/>'
            for d in (-1, 1))
    else:
        eyes = "".join(
            f'<ellipse cx="{50 + d * ex}" cy="{ey}" rx="5.5" ry="7" fill="{dark}"/>'
            f'<circle cx="{50 + d * ex + 1.6}" cy="{ey - 2}" r="2" fill="#fff"/><circle cx="{50 + d * ex - 1.4}" cy="{ey + 2}" r="1" fill="#fff"/>'
            for d in (-1, 1))
    my = ey + 12
    m = (i // 3) % 3
    if m == 0:
        mouth = f'<path d="M44 {my}q6 6 12 0" fill="none" stroke="{dark}" stroke-width="2.2" stroke-linecap="round"/>'
    elif m == 1:
        mouth = f'<path d="M43 {my - 1}q7 11 14 0z" fill="{dark}"/><path d="M46 {my + 3.5}q4 3 8 0" fill="#ff8fb3"/>'
    else:
        mouth = f'<path d="M43 {my}q3.5 5 7 0q3.5 5 7 0" fill="none" stroke="{dark}" stroke-width="2.2" stroke-linecap="round"/>'
    cheeks = "".join(f'<ellipse cx="{50 + d * (ex + 10)}" cy="{ey + 9}" rx="5" ry="3" fill="#ff5fa8" opacity=".4"/>' for d in (-1, 1))
    return eyes + cheeks + mouth


def acc(i, kind, h):
    x, y = ACC_AT[kind]
    t = (i // 12) * 3 + (i % 3)
    c = hsl(h + 180, 90, 72)
    if t == 0:  # 리본
        return (f'<g transform="translate({x} {y})"><ellipse cx="-8" cy="0" rx="8" ry="5.5" fill="{c}" stroke="#fff" stroke-width="1.4" transform="rotate(-20 -8 0)"/>'
                f'<ellipse cx="8" cy="0" rx="8" ry="5.5" fill="{c}" stroke="#fff" stroke-width="1.4" transform="rotate(20 8 0)"/><circle r="3.6" fill="#fff"/></g>')
    if t == 1:  # 왕관
        return f'<g transform="translate({x} {y})"><path d="M-9 4l-2 -11 6 5 5 -9 5 9 6 -5 -2 11z" fill="#ffe27a" stroke="#f29a1f" stroke-width="1.6" stroke-linejoin="round"/></g>'
    if t == 2:  # 하트 핀
        return f'<g transform="translate({x} {y}) scale(.5)"><path d="M0 14C-18 3 -17 -10 -8 -11c4 0 8 3 8 6 0 -3 4 -6 8 -6 9 1 10 14 -8 25z" fill="#ff6fae" stroke="#fff" stroke-width="2.4"/></g>'
    if t == 3:  # 후광
        return f'<ellipse cx="{x}" cy="{y - 4}" rx="11" ry="3.6" fill="none" stroke="#ffd84d" stroke-width="3"/>'
    if t == 4:  # 별 핀
        return f'<polygon points="{star_pts(x, y, 9, 4)}" fill="#ffe27a" stroke="#f29a1f" stroke-width="1.5" stroke-linejoin="round"/>'
    return f'<g transform="translate({x} {y})"><path d="M0 -10Q2 -2 10 0Q2 2 0 10Q-2 2 -10 0Q-2 -2 0 -10z" fill="#fff" stroke="{c}" stroke-width="1.6"/></g>'  # 반짝이


def character(i):
    kind = i % 12
    h = (i * 47 + 330) % 360
    f, e, dark = hsl(h, 88, 83), hsl(h, 75, 62), hsl(h, 45, 28)
    wing = ""
    if i % 2 == 1 or kind in (0, 10):
        wing = ('<ellipse cx="8" cy="52" rx="10" ry="17" fill="#f0e6ff" stroke="#b78cff" stroke-width="2" transform="rotate(-25 8 52)"/>'
                '<ellipse cx="92" cy="52" rx="10" ry="17" fill="#f0e6ff" stroke="#b78cff" stroke-width="2" transform="rotate(25 92 52)"/>')
    return wing + body(kind, f, e) + face(i, kind, dark) + acc(i, kind, h)


def tile(w=960, cols=6, rows=4):
    cw, ch = w / cols, 190
    h = ch * rows
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h:.0f}" viewBox="0 0 {w} {h:.0f}"><g opacity=".55">']
    order = list(range(24))
    random.shuffle(order)
    for n, i in enumerate(order):
        r, c = divmod(n, cols)
        s = random.uniform(.62, .84)
        x = c * cw + cw / 2 + random.uniform(-22, 22)
        y = r * ch + ch / 2 + random.uniform(-16, 16)
        rot = random.uniform(-14, 14)
        out.append(f'<g transform="translate({x - 50 * s:.1f} {y - 50 * s:.1f}) rotate({rot:.1f} {50 * s:.1f} {50 * s:.1f}) scale({s:.2f})">{character(i)}</g>')
    out.append("</g></svg>")
    return "".join(out)


def apply_to_html():
    svg = tile()
    uri = 'url("data:image/svg+xml;utf8,' + quote(svg, safe="/:=;,' ()") + '")'
    p = ROOT / "src/static/index.html"
    t = p.read_text(encoding="utf-8")
    block = f"  --bg-characters: {uri};\n"
    if "--bg-characters:" in t:
        t = re.sub(r"  --bg-characters: .*?;\n", lambda _: block, t, count=1, flags=re.S)
    else:
        t = t.replace("  :root {\n", "  :root {\n" + block, 1)
    p.write_text(t, encoding="utf-8")
    print("배경 캐릭터 24종 갱신:", len(svg), "bytes(svg)")


if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply_to_html()
    else:
        print(tile())
