#!/usr/bin/env python3
"""PyInstaller 打包辅助脚本。

运行后会根据当前平台生成可执行文件：
- Windows: dist/个人企业世界模型.exe
- Linux/macOS: dist/个人企业世界模型

打包完成后自动验证 torch 完整性与 bge 模型文件，并输出 dist/torch-validation-report.json。

用法：
    python3 build.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pewm.processors.log_config import get_logger, setup_logging

ROOT = Path(__file__).resolve().parent
setup_logging()
logger = get_logger(__name__)


def check_pyinstaller():
    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            logger.error("未找到 pyinstaller。请运行： pip install pyinstaller")
            sys.exit(1)


def _pyinstaller_cmd() -> list:
    """优先使用 PATH 中的 pyinstaller，否则回退到 python -m PyInstaller。"""
    if shutil.which("pyinstaller"):
        return ["pyinstaller"]
    return [sys.executable, "-m", "PyInstaller"]


def verify_build_artifact(exe_path: Path) -> dict:
    """验证打包产物中 torch 与 bge 模型完整性。

    检查项：
    1. exe 存在且非空
    2. build.spec 中 torch 相关 hiddenimports 未被裁剪
    3. 本地 bge-model/ 必要文件完整
    """
    report = {
        "exe_path": str(exe_path),
        "exe_exists": exe_path.exists(),
        "exe_size_mb": round(exe_path.stat().st_size / (1024 * 1024), 1) if exe_path.exists() else 0,
        "hiddenimports_ok": False,
        "bge_model_files_ok": False,
        "torch_importable_in_current_env": False,
        "errors": [],
    }

    # 1. 检查 build.spec 中的 torch hiddenimports
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")
    required_imports = [
        "torch", "torch.nn", "torch.nn.functional",
        "transformers", "sentence_transformers",
        "tokenizers", "huggingface_hub", "safetensors",
    ]
    missing = [m for m in required_imports if f"'{m}'" not in spec and f'"{m}"' not in spec]
    if missing:
        report["errors"].append(f"build.spec 缺少 hiddenimports: {missing}")
    else:
        report["hiddenimports_ok"] = True

    # 2. 检查 bge 模型文件
    bge_dir = ROOT / "bge-model"
    required_files = ["config.json", "pytorch_model.bin", "tokenizer.json", "vocab.txt"]
    missing_files = [f for f in required_files if not (bge_dir / f).exists()]
    if missing_files:
        report["errors"].append(f"bge-model 缺少文件: {missing_files}")
    else:
        report["bge_model_files_ok"] = True

    # 3. 当前环境 torch 可导入性（打包前的环境自检）
    try:
        import torch  # noqa: F401
        report["torch_importable_in_current_env"] = True
        report["torch_version"] = torch.__version__
    except Exception as e:
        report["errors"].append(f"当前环境 torch 导入失败: {e}")

    report["healthy"] = (
        report["exe_exists"]
        and report["hiddenimports_ok"]
        and report["bge_model_files_ok"]
        and report["torch_importable_in_current_env"]
    )
    return report


def main():
    check_pyinstaller()

    logger.info("开始打包个人企业世界模型...")
    cmd = _pyinstaller_cmd() + ["build.spec", "--clean", "--noconfirm"]
    result = subprocess.run(cmd, cwd=ROOT)

    if result.returncode != 0:
        logger.error("打包失败，请查看上方错误信息。")
        sys.exit(result.returncode)

    exe_name = "个人企业世界模型.exe" if sys.platform == "win32" else "个人企业世界模型"
    exe_path = ROOT / "dist" / exe_name
    logger.info("打包完成：%s", exe_path)

    # 打包产物验证
    report = verify_build_artifact(exe_path)
    report_path = ROOT / "dist" / "torch-validation-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("torch 验证报告已写入：%s", report_path)

    if report["healthy"]:
        logger.info("torch 完整性验证通过。")
    else:
        logger.warning("torch 完整性验证存在问题：%s", report["errors"])

    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["healthy"] else 2)


if __name__ == "__main__":
    main()
