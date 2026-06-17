import io
import json
import os
from pathlib import Path


def get_accessory_rpa_runtime_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpa_runtime", "accessory_order")


def get_accessory_rpa_result_path():
    return os.path.join(get_accessory_rpa_runtime_dir(), "result.json")


def load_accessory_rpa_result(expected_internal_code=None):
    result_path = Path(get_accessory_rpa_result_path())
    if not result_path.exists():
        return {
            "ok": False,
            "message": f"未找到 RPA 结果文件：{result_path}",
            "file_path": "",
            "raw": {},
        }

    try:
        with result_path.open("r", encoding="utf-8-sig") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "message": f"RPA 结果文件格式错误：{exc}",
            "file_path": "",
            "raw": {},
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"读取 RPA 结果文件失败：{exc}",
            "file_path": "",
            "raw": {},
        }

    if expected_internal_code is not None:
        raw_internal_code = str(raw.get("internal_code", "")).strip()
        if raw_internal_code != str(expected_internal_code).strip():
            return {
                "ok": False,
                "message": f"RPA 结果中的采购单查询码不一致：{raw_internal_code or '空'}",
                "file_path": "",
                "raw": raw,
            }

    status = str(raw.get("status", "")).strip().lower()
    if status != "success":
        return {
            "ok": False,
            "message": str(raw.get("message", "RPA 执行失败")).strip() or "RPA 执行失败",
            "file_path": "",
            "raw": raw,
        }

    file_path = str(raw.get("file_path", "")).strip()
    if not file_path:
        return {
            "ok": False,
            "message": "RPA 结果中未提供 file_path",
            "file_path": "",
            "raw": raw,
        }

    file_suffix = Path(file_path).suffix.lower()
    if file_suffix not in {".xls", ".xlsx", ".csv"}:
        return {
            "ok": False,
            "message": f"RPA 下载文件类型不支持：{file_suffix or '未知'}",
            "file_path": file_path,
            "raw": raw,
        }

    file_obj = Path(file_path)
    if not file_obj.exists():
        return {
            "ok": False,
            "message": f"RPA 下载文件不存在：{file_path}",
            "file_path": file_path,
            "raw": raw,
        }

    try:
        if file_obj.stat().st_size <= 0:
            return {
                "ok": False,
                "message": f"RPA 下载文件为空：{file_path}",
                "file_path": file_path,
                "raw": raw,
            }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"检查 RPA 下载文件失败：{exc}",
            "file_path": file_path,
            "raw": raw,
        }

    return {
        "ok": True,
        "message": str(raw.get("message", "RPA 下载成功")).strip() or "RPA 下载成功",
        "file_path": file_path,
        "raw": raw,
    }


def open_file_for_streamlit_upload(file_path):
    data = Path(file_path).read_bytes()
    buffer = io.BytesIO(data)
    buffer.name = Path(file_path).name
    buffer.seek(0)
    return buffer
