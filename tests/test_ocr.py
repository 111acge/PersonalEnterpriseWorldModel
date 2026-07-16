"""OCR 模块测试。"""
from pathlib import Path
from unittest.mock import patch

import pytest

import pewm.processors.ocr as ocr
from pewm.processors.ocr import (
    is_local_available,
    list_media_files,
    ocr_for_inbox_file,
    ocr_image,
    ocr_image_api,
    ocr_image_local,
    process_all_media,
)


def _cleanup_media():
    media_dir = ocr.MEDIA_DIR
    if media_dir.exists():
        for p in media_dir.iterdir():
            if p.is_file():
                p.unlink()


def test_list_media_files_empty_when_no_media(temp_project):
    _cleanup_media()
    files = list_media_files()
    assert files == []


def test_list_media_files_finds_images(temp_project):
    _cleanup_media()
    ocr.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    (ocr.MEDIA_DIR / "test.png").write_bytes(b"fake png")
    (ocr.MEDIA_DIR / "note.txt").write_text("not an image")
    files = list_media_files()
    assert len(files) == 1
    assert files[0].name == "test.png"


def test_is_local_available_is_false_without_paddle(temp_project):
    with patch("builtins.__import__", side_effect=ImportError("no paddle")):
        assert is_local_available() is False


def test_ocr_image_api_with_mock(temp_project):
    """mock API 调用，避免真实网络请求。"""
    _cleanup_media()
    ocr.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    img = ocr.MEDIA_DIR / "fake.png"
    img.write_bytes(b"fake")
    with patch("pewm.processors.ocr.ocr_by_api", return_value=[{"text": "hello"}]):
        text = ocr_image_api(img, "baidu", {"api_key": "key"})
    assert "hello" in text


def test_ocr_image_local_raises_without_paddle(temp_project):
    """未安装 paddle 时本地 OCR 抛出异常。"""
    _cleanup_media()
    ocr.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    img = ocr.MEDIA_DIR / "fake.png"
    img.write_bytes(b"fake")
    with patch("pewm.processors.ocr._load_paddle", side_effect=RuntimeError("no paddle")):
        with pytest.raises(RuntimeError, match="no paddle"):
            ocr_image_local(img)


def test_ocr_image_uses_api_mode_when_configured(temp_project):
    """ocr_image 根据配置选择 API 模式。"""
    _cleanup_media()
    ocr.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    img = ocr.MEDIA_DIR / "fake.png"
    img.write_bytes(b"fake")
    with patch("pewm.processors.ocr.load_ocr_config", return_value={"mode": "api", "provider": "baidu", "credentials": {"api_key": "k"}}):
        with patch("pewm.processors.ocr.ocr_by_api", return_value=[{"text": "ok"}]):
            text = ocr_image(img)
    assert "ok" in text


def test_ocr_for_inbox_file_finds_related_media(temp_project):
    _cleanup_media()
    ocr.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ocr.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    (ocr.INBOX_DIR / "2026-07-15-note.md").write_text("note", encoding="utf-8")
    (ocr.MEDIA_DIR / "2026-07-15-note-fig1.png").write_bytes(b"fake")
    with patch("pewm.processors.ocr.ocr_image", return_value="hello"):
        result = ocr_for_inbox_file(ocr.INBOX_DIR / "2026-07-15-note.md")
    assert "hello" in result


def test_process_all_media_with_callback(temp_project):
    _cleanup_media()
    ocr.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    (ocr.MEDIA_DIR / "a.png").write_bytes(b"fake")
    with patch("pewm.processors.ocr.ocr_image", return_value="text"):
        progress = []
        results = process_all_media(progress_callback=lambda c, t, m: progress.append((c, t, m)))
    assert len(results) == 1
    assert len(progress) > 0


def test_process_all_media_empty(temp_project):
    _cleanup_media()
    results = process_all_media()
    assert results == {}
