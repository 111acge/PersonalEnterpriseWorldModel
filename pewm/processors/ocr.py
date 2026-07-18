"""OCR 处理模块：支持本地 PaddleOCR 和云端 API 双模式。

模式切换通过 load_ocr_config() 读取，支持：
- mode=local:  使用 PaddleOCR（首次自动下载模型）
- mode=api:    使用百度/腾讯/阿里云 HTTP API

所有图片处理都支持 progress_callback(current, total, message) 回调，用于 GUI 显示进度。
"""
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from pewm.paths import INBOX_DIR, MEDIA_DIR, ROOT
from pewm.processors.log_config import get_logger
from pewm.processors.ocr_api import ocr_by_api, load_ocr_config

logger = get_logger(__name__)
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}

# 全局缓存：PaddleOCR 实例（初始化需线程安全）
_OCR = None
_OCR_LOCK = threading.Lock()


def _load_paddle():
    """懒加载 PaddleOCR，首次调用会下载模型。"""
    global _OCR
    if _OCR is not None:
        return _OCR
    with _OCR_LOCK:
        if _OCR is not None:
            return _OCR
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            raise RuntimeError(
                "未安装 PaddleOCR。请在「OCR 配置」Tab 中切换到 API 模式，"
                "或执行：pip install paddlepaddle paddleocr"
            )
        det_dir = ROOT / "data" / "ocr_models"
        det_dir.mkdir(parents=True, exist_ok=True)
        try:
            _OCR = PaddleOCR(
                use_angle_cls=True,
                lang="ch",
                det_model_dir=str(det_dir / "det"),
                rec_model_dir=str(det_dir / "rec"),
                cls_model_dir=str(det_dir / "cls"),
                show_log=False,
            )
        except TypeError as e:
            raise RuntimeError(
                f"PaddleOCR 初始化参数不兼容：{e}。"
                "当前安装的可能是 PaddleOCR 3.x，本程序仅兼容 2.x。"
                '请执行：pip install "paddleocr<3"'
            ) from e
        return _OCR


def list_media_files() -> List[Path]:
    """列出 00-Inbox/_media/ 下所有图片。"""
    if not MEDIA_DIR.exists():
        return []
    files = []
    for p in MEDIA_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            files.append(p)
    return sorted(files)


def ocr_image_local(image_path: Path) -> str:
    """本地 PaddleOCR 识别单张图片。"""
    ocr = _load_paddle()
    result = ocr.ocr(str(image_path), cls=True)
    if not result or not result[0]:
        return ""
    lines = []
    for line in result[0]:
        box, (text, conf) = line
        if conf >= 0.5 and text.strip():
            lines.append(text.strip())
    return "\n".join(lines)


def ocr_image_api(image_path: Path, provider: str, credentials: Dict) -> str:
    """云端 API 识别单张图片。"""
    results = ocr_by_api(image_path, provider, credentials)
    # 过滤低置信度
    lines = [r["text"] for r in results if r.get("confidence", 1.0) >= 0.5]
    return "\n".join(lines)


def ocr_image(image_path: Path, mode: str = None, provider: str = None,
              credentials: Dict = None) -> str:
    """统一入口：根据 mode 调用本地或 API。"""
    if mode is None or provider is None or credentials is None:
        cfg = load_ocr_config()
        mode = mode or cfg.get("mode", "local")
        provider = provider or cfg.get("provider", "baidu")
        credentials = credentials or cfg.get("credentials", {})

    if mode == "api":
        return ocr_image_api(image_path, provider, credentials)
    else:
        return ocr_image_local(image_path)


def ocr_for_inbox_file(inbox_file: Path,
                       progress_callback: Optional[Callable] = None) -> str:
    """为某个 Inbox md 文件找出 _media/ 下相关图片并 OCR。

    progress_callback(current, total, message) 会被调用：
    - 开始时：(0, N, "准备识别 N 张图片")
    - 每张图前：(i, N, "正在识别 {filename}")
    - 结束时：(N, N, "完成")
    """
    if not MEDIA_DIR.exists():
        return ""
    stem = inbox_file.stem
    related = []
    for p in list_media_files():
        p_stem = p.stem
        if p_stem == stem or p_stem.startswith(stem + "-"):
            related.append(p)
    if not related:
        return ""

    total = len(related)
    if progress_callback:
        progress_callback(0, total, f"准备识别 {total} 张图片")

    texts = []
    for idx, img in enumerate(related, 1):
        if progress_callback:
            progress_callback(idx - 1, total, f"[{idx}/{total}] 正在识别 {img.name}")
        try:
            t = ocr_image(img)
            if t:
                texts.append(f"[图片: {img.name}]\n{t}")
        except Exception as e:
            logger.warning("图片 %s 识别失败: %s", img.name, e)
            texts.append(f"[图片 {img.name} 识别失败]")

    if progress_callback:
        progress_callback(total, total, "OCR 完成")
    return "\n\n".join(texts)


def process_all_media(progress_callback: Optional[Callable] = None) -> Dict[Path, str]:
    """对 _media/ 下所有图片做 OCR，返回 {图片路径: 文本}。"""
    images = list_media_files()
    total = len(images)
    if progress_callback:
        progress_callback(0, total, f"准备识别 {total} 张图片")
    out = {}
    for idx, img in enumerate(images, 1):
        if progress_callback:
            progress_callback(idx - 1, total, f"[{idx}/{total}] 正在识别 {img.name}")
        try:
            out[img] = ocr_image(img)
        except Exception as e:
            logger.warning("图片 %s 识别失败: %s", img.name, e)
            out[img] = "[识别失败]"
    if progress_callback:
        progress_callback(total, total, "OCR 完成")
    return out


def is_local_available() -> bool:
    """检查 PaddleOCR 是否已安装。"""
    try:
        import paddleocr  # noqa: F401
        return True
    except ImportError:
        return False
