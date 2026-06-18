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

def write_accessory_order_rpa_request(internal_code: str) -> dict:
    """
    生成辅料采购单 RPA 请求文件。
    只负责写 request_code.txt，并删除旧 result.json。
    不启动影刀，不处理 Excel。
    """
    import os
    from pathlib import Path

    internal_code = str(internal_code or "").strip()

    if not internal_code:
        return {
            "success": False,
            "message": "采购单查询码不能为空",
            "request_path": "",
        }

    runtime_dir = Path(r"C:\Users\admin\Desktop\MaterialSystem\rpa_runtime\accessory_order")
    request_path = runtime_dir / "request_code.txt"
    result_path = runtime_dir / "result.json"

    runtime_dir.mkdir(parents=True, exist_ok=True)

    # 删除旧的 result.json，避免页面读取到上一次 RPA 结果
    try:
        if result_path.exists():
            result_path.unlink()
    except Exception:
        pass

    # 写入本次采购单查询码
    request_path.write_text(internal_code + "\n", encoding="utf-8-sig")
    # 写入等待运行状态，供页面提示用户下一步操作
    import json
    from datetime import datetime

    status_path = runtime_dir / "rpa_status.json"
    status_tmp_path = runtime_dir / "rpa_status.tmp.json"

    status_data = {
        "status": "pending",
        "internal_code": internal_code,
        "file_path": "",
        "message": "RPA 请求文件已生成，请手动运行影刀 RPA",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    status_tmp_path.write_text(
        json.dumps(status_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    status_tmp_path.replace(status_path)

    return {
        "success": True,
        "message": f"已生成 RPA 请求文件：{internal_code}",
        "request_path": str(request_path),
        "internal_code": internal_code,
    }

def load_accessory_order_rpa_status() -> dict:
    """
    读取辅料采购单 RPA 状态文件 rpa_status.json。

    额外保护：
    如果 pending / running 状态长时间没有更新，认为是旧状态或 RPA 异常中断，
    自动删除 rpa_status.json，避免页面一直提示“已有 RPA 查询正在进行”。
    """
    import json
    from pathlib import Path
    from datetime import datetime

    status_path = Path(r"C:\Users\admin\Desktop\MaterialSystem\rpa_runtime\accessory_order\rpa_status.json")

    if not status_path.exists():
        return {
            "exists": False,
            "status": "none",
            "internal_code": "",
            "file_path": "",
            "message": "暂未检测到 RPA 状态文件",
            "updated_at": "",
        }

    try:
        data = json.loads(status_path.read_text(encoding="utf-8-sig"))

        status_value = str(data.get("status", "")).strip()
        updated_at = str(data.get("updated_at", "")).strip()

        # pending / running 长时间未更新，自动清理旧状态
        if status_value in ("pending", "running") and updated_at:
            try:
                updated_time = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
                minutes_passed = (datetime.now() - updated_time).total_seconds() / 60

                # pending：只是请求已生成，5分钟没变 running，基本可认为没有成功启动
                # running：正在查询，20分钟没更新，基本可认为 RPA 异常中断或被手动关闭
                timeout_minutes = 5 if status_value == "pending" else 20

                if minutes_passed >= timeout_minutes:
                    try:
                        status_path.unlink()
                    except Exception:
                        pass

                    return {
                        "exists": False,
                        "status": "none",
                        "internal_code": "",
                        "file_path": "",
                        "message": f"检测到旧的 RPA 状态已超过 {timeout_minutes} 分钟，已自动清理，可重新启动查询。",
                        "updated_at": "",
                    }
            except Exception:
                # 时间格式异常时不阻断页面，继续按原状态返回
                pass

        return {
            "exists": True,
            "status": status_value,
            "internal_code": str(data.get("internal_code", "")).strip(),
            "file_path": str(data.get("file_path", "")).strip(),
            "message": str(data.get("message", "")).strip(),
            "updated_at": updated_at,
        }

    except Exception as e:
        return {
            "exists": True,
            "status": "error",
            "internal_code": "",
            "file_path": "",
            "message": f"读取 RPA 状态文件失败：{e}",
            "updated_at": "",
        }
    
def start_accessory_order_rpa() -> dict:
    """
    启动影刀 RPA 指定应用。

    当前绑定流程：
    旺店通采购单原表下载_读取request_code

    注意：
    1. 不阻塞 Streamlit 页面；
    2. 不等待影刀执行结束；
    3. 使用 ShadowBot.exe + shadowbot:Run?robot-uuid=... 启动指定流程；
    4. 如果影刀已经打开，通常会复用现有客户端，不重复打开新窗口。
    """
    import subprocess
    from pathlib import Path

    exe_path = Path(r"C:\Program Files\ShadowBot\ShadowBot.exe")
    robot_uuid = "c491840e-87e8-4b10-9f7a-648a9f494ff2"
    run_arg = f"shadowbot:Run?robot-uuid={robot_uuid}"

    if not exe_path.exists():
        return {
            "success": False,
            "message": f"未找到影刀程序：{exe_path}",
            "exe_path": str(exe_path),
            "pid": None,
            "already_running": False,
            "robot_uuid": robot_uuid,
        }

    already_running = False

    # 只用于提示：检查影刀是否已经在运行
    try:
        tasklist_result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ShadowBot.Shell.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            shell=False,
        )
        tasklist_text = tasklist_result.stdout or ""
        already_running = "ShadowBot.Shell.exe".lower() in tasklist_text.lower()
    except Exception:
        already_running = False

    try:
        process = subprocess.Popen(
            [str(exe_path), run_arg],
            cwd=str(exe_path.parent),
            shell=False,
        )

        if already_running:
            message = "已向正在运行的影刀发送 RPA 流程启动指令。"
        else:
            message = "影刀 RPA 已启动，并已发送指定流程运行指令。"

        return {
            "success": True,
            "message": message,
            "exe_path": str(exe_path),
            "pid": process.pid,
            "already_running": already_running,
            "robot_uuid": robot_uuid,
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"启动影刀 RPA 指定流程失败：{e}",
            "exe_path": str(exe_path),
            "pid": None,
            "already_running": already_running,
            "robot_uuid": robot_uuid,
        }