"""OCR HTTP API 客户端适配器，支持三家主流云端 OCR：

- 百度智能云 OCR（推荐，每月免费 1000 次通用文字识别）
- 腾讯云 OCR（每月免费 1000 次）
- 阿里云 OCR（按量付费）

所有适配器都返回统一的 list[{text, confidence, bbox}] 格式，便于上层切换。
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from pewm.processors.llm_client import CONFIG_DIR, load_config, save_config


# 每家提供商的配置模板
OCR_PROVIDERS = {
    "baidu": {
        "name": "百度智能云",
        "description": "每月免费 1000 次通用文字识别。需要 ApiKey + SecretKey。",
        "fields": [
            ("api_key", "API Key"),
            ("secret_key", "Secret Key"),
        ],
    },
    "tencent": {
        "name": "腾讯云",
        "description": "每月免费 1000 次。需要 SecretId + SecretKey。",
        "fields": [
            ("secret_id", "SecretId"),
            ("secret_key", "SecretKey"),
        ],
    },
    "aliyun": {
        "name": "阿里云",
        "description": "按量付费。需要 AppCode（从云市场购买后获得）。可选填 endpoint。",
        "fields": [
            ("app_code", "AppCode"),
            ("endpoint", "端点 URL（可选）"),
        ],
    },
}


def _read_image_b64(path: Path) -> str:
    """读取图片并 base64 编码。"""
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _http_post(url: str, data: Dict, headers: Optional[Dict] = None,
               form: bool = False, timeout: int = 30) -> Dict:
    """发送 HTTP POST，返回 JSON 解析结果。"""
    if form:
        body = urllib.parse.urlencode(data).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    else:
        body = json.dumps(data).encode("utf-8")
        content_type = "application/json"
    req_headers = {"Content-Type": content_type}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        try:
            return json.loads(raw)
        except Exception:
            raise RuntimeError(f"OCR API 返回非 JSON: {raw[:200]}")


# ========== 百度智能云 OCR ==========

def _baidu_get_token(api_key: str, secret_key: str) -> str:
    """百度：用 api_key + secret_key 换取 access_token。"""
    url = (
        "https://aip.baidubce.com/oauth/2.0/token?"
        "grant_type=client_credentials"
        f"&client_id={urllib.parse.quote(api_key)}"
        f"&client_secret={urllib.parse.quote(secret_key)}"
    )
    req = urllib.request.Request(url, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "access_token" not in data:
        raise RuntimeError(f"百度 token 获取失败: {data}")
    return data["access_token"]


def ocr_baidu(image_path: Path, api_key: str, secret_key: str) -> List[Dict]:
    """百度通用文字识别（高精度版）。"""
    token = _baidu_get_token(api_key, secret_key)
    url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic?access_token={token}"
    b64 = _read_image_b64(image_path)
    data = _http_post(url, {"image": b64, "language_type": "CHN_ENG"}, form=True)
    if "words_result" not in data:
        raise RuntimeError(f"百度 OCR 失败: {data}")
    out = []
    for item in data.get("words_result", []):
        out.append({
            "text": item.get("words", ""),
            "confidence": item.get("probability", {}).get("average", 1.0),
            "bbox": item.get("location", {}),
        })
    return out


# ========== 腾讯云 OCR ==========

def _tencent_sign(secret_id: str, secret_key: str, payload: Dict, service: str, action: str, version: str) -> Dict:
    """腾讯云 v3 签名算法。"""
    host = f"{service}.tencentcloudapi.com"
    endpoint = f"https://{host}"
    timestamp = int(time.time())
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

    # 规范化请求
    http_request_method = "POST"
    canonical_uri = "/"
    canonical_querystring = ""
    ct = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{ct}\nhost:{host}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()
    canonical_request = (
        f"{http_request_method}\n{canonical_uri}\n{canonical_querystring}\n"
        f"{canonical_headers}\n{signed_headers}\n{hashed_payload}"
    )

    algorithm = "TC3-HMAC-SHA256"
    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashed_canonical}"

    def _hmac_sha256(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, service)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": ct,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version,
    }
    return {"endpoint": endpoint, "headers": headers}


def ocr_tencent(image_path: Path, secret_id: str, secret_key: str) -> List[Dict]:
    """腾讯云通用 OCR。"""
    b64 = _read_image_b64(image_path)
    payload = {"ImageBase64": b64, "Scene": "general"}
    sign = _tencent_sign(secret_id, secret_key, payload, "ocr", "GeneralBasicOCR", "2018-11-19")
    data = _http_post(sign["endpoint"], payload, headers=sign["headers"])
    resp = data.get("Response", {})
    if "Error" in resp:
        raise RuntimeError(f"腾讯 OCR 失败: {resp['Error']}")
    out = []
    for item in resp.get("TextDetections", []):
        pts = item.get("Polygon", [])
        bbox = {}
        if len(pts) >= 4:
            bbox = {
                "left": pts[0].get("X", 0),
                "top": pts[0].get("Y", 0),
                "width": pts[1].get("X", 0) - pts[0].get("X", 0),
                "height": pts[3].get("Y", 0) - pts[0].get("Y", 0),
            }
        out.append({
            "text": item.get("DetectedText", ""),
            "confidence": item.get("Confidence", 1.0) / 100.0,
            "bbox": bbox,
        })
    return out


# ========== 阿里云 OCR ==========

def ocr_aliyun(image_path: Path, app_code: str, endpoint: str = None) -> List[Dict]:
    """阿里云 OCR（云市场通用文字识别）。"""
    # 阿里云 OCR 云市场端点，用户也可在配置里覆盖
    url = endpoint or "https://ocr.market.alicloudapi.com/ai_ocr_accurate"
    b64 = _read_image_b64(image_path)
    headers = {"Authorization": f"APPCODE {app_code}"}
    data = _http_post(url, {"image": b64}, headers=headers)
    if data.get("code") != 0:
        raise RuntimeError(f"阿里 OCR 失败: {data}")
    out = []
    result_data = data.get("data", {}) or {}
    items = (result_data.get("data") or {}).get("data") or []
    for item in items:
        out.append({
            "text": item.get("word", ""),
            "confidence": item.get("prob", 1.0),
            "bbox": item.get("location", {}),
        })
    return out


# ========== 统一入口 ==========

def ocr_by_api(image_path: Path, provider: str, credentials: Dict) -> List[Dict]:
    """根据提供商分发到对应 API。"""
    if provider == "baidu":
        return ocr_baidu(
            image_path,
            api_key=credentials.get("api_key", ""),
            secret_key=credentials.get("secret_key", ""),
        )
    elif provider == "tencent":
        return ocr_tencent(
            image_path,
            secret_id=credentials.get("secret_id", ""),
            secret_key=credentials.get("secret_key", ""),
        )
    elif provider == "aliyun":
        return ocr_aliyun(
            image_path,
            app_code=credentials.get("app_code", ""),
            endpoint=credentials.get("endpoint"),
        )
    else:
        raise ValueError(f"不支持的 OCR 提供商: {provider}")


def load_ocr_config() -> Dict:
    """读取持久化的 OCR 配置（mode/provider/credentials）。"""
    cfg = load_config()
    ocr_cfg = cfg.get("ocr", {})
    # 默认值
    ocr_cfg.setdefault("mode", "local")  # local | api
    ocr_cfg.setdefault("provider", "baidu")
    ocr_cfg.setdefault("credentials", {})
    return ocr_cfg


def save_ocr_config(ocr_cfg: Dict) -> None:
    """把 OCR 配置写回 config.json。"""
    cfg = load_config()
    cfg["ocr"] = ocr_cfg
    save_config(cfg)


def test_ocr_api(provider: str, credentials: Dict, sample_image: Optional[Path] = None) -> str:
    """测试 OCR API 连通性，返回 'OK: ...' 或 'ERROR: ...'。"""
    # 使用一个 1x1 的纯白 PNG 作为测试样本
    if sample_image is None or not sample_image.exists():
        # 最小的合法 PNG（1x1 白色像素）
        sample_png = (CONFIG_DIR / "_ocr_test.png")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not sample_png.exists():
            # 预计算好的 1x1 白 PNG 的 base64
            png_b64 = (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwM"
                "CAO+ip1sAAAAASUVORK5CYII="
            )
            sample_png.write_bytes(base64.b64decode(png_b64))
        sample_image = sample_png
    try:
        results = ocr_by_api(sample_image, provider, credentials)
        return f"OK: 连通成功，返回 {len(results)} 个文字区域"
    except Exception as e:
        return f"ERROR: {e}"
