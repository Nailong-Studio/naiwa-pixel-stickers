"""Inspect exported milk-family GIFs. Optional previews do not modify the inputs."""

import argparse
import hashlib
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OTHER_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".apng",
                        ".avif", ".bmp", ".tif", ".tiff"}


def inspect_gif(path, minimum_hold, make_preview, animation_hash=None):
    errors = []
    samples = []
    size_bytes = path.stat().st_size
    if size_bytes > 500 * 1024:
        errors.append("文件超过 500 KB")

    with Image.open(path) as image:
        if image.format != "GIF":
            return "格式为 " + str(image.format), ["文件内容不是 GIF"], samples
        width, height = image.size
        if (width, height) != (240, 240):
            return f"{width}×{height}", [*errors, "画布应为 240×240"], samples

        edges = ((0, 0, width, 1), (0, height - 1, width, height),
                 (0, 0, 1, height), (width - 1, 0, width, height))
        loop = image.info.get("loop")
        if loop != 0:
            errors.append("微信投稿版应设置无限循环")
        count = image.n_frames
        sample_indices = {0, count // 2, count - 1}
        first_pixels = None
        previous_pixels = None
        changed = False
        missing_delay = False
        solid_border = False
        blank = False
        final_hold = 0
        total = 0
        for index in range(count):
            image.seek(index)
            frame = image.convert("RGBA")
            # Fully transparent pixels may carry arbitrary RGB in different GIF palettes.
            visible = Image.alpha_composite(Image.new("RGBA", frame.size), frame)
            alpha = visible.getchannel("A")
            blank |= alpha.getbbox() is None
            solid_border |= all(alpha.crop(edge).getextrema()[0] == 255 for edge in edges)
            pixels = visible.tobytes()
            if first_pixels is None:
                first_pixels = pixels
            changed |= pixels != first_pixels
            delay = image.info.get("duration", 0)
            missing_delay |= delay <= 0
            total += delay
            if pixels != previous_pixels:
                # Hold duration belongs to the pose, even if an export splits it across frames.
                if animation_hash is not None and previous_pixels is not None:
                    animation_hash.update(previous_pixels)
                    animation_hash.update(final_hold.to_bytes(8, "big"))
                previous_pixels = pixels
                final_hold = 0
            final_hold += delay
            if make_preview and index in sample_indices:
                samples.append((index + 1, visible))

    if animation_hash is not None and previous_pixels is not None:
        animation_hash.update(previous_pixels)
        animation_hash.update(final_hold.to_bytes(8, "big"))
    if count < 2 or not changed:
        errors.append("画面没有可见的动画")
    if blank:
        errors.append("出现全透明空帧")
    if solid_border:
        errors.append("有帧的外缘整圈不透明，请检查导出底色")
    if missing_delay:
        errors.append("有帧未设有效时长")
    if minimum_hold is not None and final_hold < minimum_hold:
        errors.append(f"终姿停留不足 {minimum_hold} ms")
    loop_text = "无限" if loop == 0 else str(loop)
    info = (f"{width}×{height}，{size_bytes:,} B，{count} 帧，"
            f"全长 {total} ms，终姿 {final_hold} ms，循环 {loop_text}")
    return info, errors, samples


def save_preview(rows, target):
    box = 90
    label = 18
    row_height = box + label
    sheet = Image.new("RGB", (6 * box, row_height * len(rows)), "white")
    draw = ImageDraw.Draw(sheet)
    for row, (item, samples) in enumerate(rows):
        top = row * row_height
        for background_index, background in enumerate(("#f3f2ef", "#22252a")):
            for col, (index, frame) in enumerate(samples):
                x = (background_index * 3 + col) * box
                draw.rectangle((x, top, x + box - 1, top + box - 1), fill=background)
                thumb = frame.resize((box, box), Image.Resampling.NEAREST)
                sheet.paste(thumb, (x, top), thumb)
                draw.text((x + 5, top + box + 1), f"#{item:02d} F{index:02d}", fill="#202020")
    sheet.save(target, format="PNG")


def main():
    parser = argparse.ArgumentParser(description="检查奶家族微信聊天动图")
    parser.add_argument("path", type=Path, help="单个 GIF 或只放聊天 GIF 的目录")
    parser.add_argument("--album", action="store_true", help="核对专辑数量、文件类型和重复动图")
    parser.add_argument("--hold-ms", type=int, help="需要明确停住的终姿的最短毫秒数")
    parser.add_argument("--preview", type=Path, help="保存浅、深底 PNG 对照图")
    args = parser.parse_args()
    if args.hold_ms is not None and args.hold_ms <= 0:
        parser.error("--hold-ms 必须是正整数")
    if args.preview and args.preview.suffix.lower() != ".png":
        parser.error("--preview 请指定一个 .png 输出文件")
    other_images = []
    if args.path.is_dir():
        entries = sorted(p for p in args.path.iterdir() if p.is_file())
        files = [p for p in entries if p.suffix.lower() == ".gif"]
        if args.album:
            if args.preview and args.preview.resolve().parent == args.path.resolve():
                parser.error("请把 --preview 的对照图保存在聊天动图目录外")
            other_images = [p.name for p in entries if p.suffix.lower() in OTHER_IMAGE_SUFFIXES]
    elif args.path.is_file() and args.path.suffix.lower() == ".gif":
        files = [args.path]
    else:
        parser.error("找不到指定的 GIF 或目录")
    if not files:
        parser.error("目录中没有聊天 GIF")

    failed = args.album and not 8 <= len(files) <= 24
    if failed:
        print(f"专辑含 {len(files)} 枚，平台范围为 8～24 枚。")
    if other_images:
        print("聊天动图目录混入其他图片：" + "、".join(other_images))
        failed = True
    rows = []
    seen_animations = {}
    for item, path in enumerate(files, 1):
        animation_hash = hashlib.sha256() if args.album else None
        try:
            info, errors, samples = inspect_gif(path, args.hold_ms, bool(args.preview), animation_hash)
        except (OSError, EOFError, ValueError, SyntaxError) as exc:
            info, errors, samples = "", [f"文件无法完整解码：{exc}"], []
        if animation_hash is not None and not errors:
            original = seen_animations.setdefault(animation_hash.digest(), path.name)
            if original != path.name:
                errors.append(f"与 {original} 的画面和停顿完全相同")
        if errors:
            failed = True
        result = "、".join(errors) if errors else "通过"
        print(f"#{item:02d}  {path.name}  {info}  [{result}]")
        if samples:
            rows.append((item, samples))
    if args.preview and rows:
        save_preview(rows, args.preview)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
