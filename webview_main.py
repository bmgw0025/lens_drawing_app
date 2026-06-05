# -*- coding: utf-8 -*-
"""
webview_main.py - Lens Drawing Desktop Entry Point
Launches Flask backend in a background thread and opens a PyWebview window.
"""
import sys, os, time, traceback, json

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── 错误日志文件（--noconsole 模式下 sys.stdin/stderr 不可用） ──
_ERROR_LOG = os.path.join(os.path.expanduser("~"), "Desktop", "LensDrawing_error.log")


def _write_error_log(msg):
    """将错误信息写入桌面日志文件，便于无控制台模式排查问题。"""
    try:
        with open(_ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n{msg}\n")
    except Exception:
        pass


def log(msg):
    print(f"[Lens Drawing] {msg}", flush=True)


def wait_for_server(url, timeout=10):
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def _center_and_resize(window, width, height):
    """Resize window and immediately center it on screen."""
    import ctypes
    user32 = ctypes.windll.user32
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
    x = (screen_w - width) // 2
    y = (screen_h - height) // 2
    window.resize(width, height)
    window.move(x, y)


class JSApi:
    """Exposed to JavaScript for native file dialogs."""

    def __init__(self):
        self._window = None

    def _get_window(self):
        import webview
        return self._window or webview.active_window()

    def selectSavePath(self, default_name="", file_types="PDF (*.pdf)|*.pdf"):
        """Open native Save File dialog and return selected path."""
        import webview
        window = self._get_window()
        if not window:
            return json.dumps({"success": False, "error": "No active window"})

        try:
            # pywebview expects file_types as a list of strings like ['CSV (*.csv)', 'All files (*.*)']
            # Each string format: 'Description (*.extension)'
            filter_list = []
            for ft in file_types.split(";"):
                if "|" in ft:
                    desc, _ = ft.split("|", 1)
                    filter_list.append(desc)

            result = window.create_file_dialog(
                webview.SAVE_DIALOG,
                directory=os.path.expanduser("~"),
                save_filename=default_name,
                file_types=filter_list or None,
            )
            if result and len(result) > 0:
                path = result[0] if isinstance(result, (list, tuple)) else result
                return json.dumps({"success": True, "path": path})
            return json.dumps({"success": False, "error": "Cancelled"})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    def selectFolder(self, initial_dir=""):
        """Open native folder selection dialog."""
        import webview
        window = self._get_window()
        if not window:
            return json.dumps({"success": False, "error": "No active window"})

        try:
            result = window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=initial_dir or os.path.expanduser("~"),
            )
            if result and len(result) > 0:
                path = result[0] if isinstance(result, (list, tuple)) else result
                return json.dumps({"success": True, "path": path})
            return json.dumps({"success": False, "error": "Cancelled"})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    def resizeAndCenter(self, width, height):
        """Resize window and center it on screen in one atomic step."""
        window = self._get_window()
        if not window:
            return json.dumps({"success": False, "error": "No active window"})
        try:
            _center_and_resize(window, int(width), int(height))
            return json.dumps({"success": True})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    def setWindowTitle(self, title):
        """Update the window title."""
        window = self._get_window()
        if not window:
            return json.dumps({"success": False, "error": "No active window"})
        try:
            window.set_title(title)
            return json.dumps({"success": True})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})


def _fatal_error(context, exc):
    """统一错误处理：写日志 + 弹窗提示（无控制台安全）。"""
    msg = f"ERROR in {context}: {exc}\n{traceback.format_exc()}"
    log(msg)
    _write_error_log(msg)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, f"Lens Drawing 启动失败\n\n{context}: {exc}", "Lens Drawing 错误", 0x10
        )
    except Exception:
        pass


def main():
    log("Starting up...")

    try:
        log("Importing webview...")
        import webview
        ver = getattr(webview, "__version__", "unknown")
        log(f"webview version: {ver}")
    except Exception as e:
        _fatal_error("importing webview", e)
        return

    try:
        log("Importing Flask backend...")
        from web_app import run_server_in_thread, get_free_port
    except Exception as e:
        _fatal_error("importing web_app", e)
        return

    try:
        log("Starting Flask server...")
        port = run_server_in_thread(host="127.0.0.1", port=0)
        url = f"http://127.0.0.1:{port}/"
        log(f"Flask server started at {url}")
    except Exception as e:
        _fatal_error("starting Flask server", e)
        return

    log("Waiting for server to be ready...")
    if not wait_for_server(url, timeout=15):
        _fatal_error("Flask startup", "Server did not start within 15 seconds")
        return
    log("Server is ready")

    try:
        log("Creating webview window...")
        api = JSApi()
        window = webview.create_window(
            title="Lens Drawing - 镜片工程图绘制",
            url=url,
            width=520,
            height=720,
            min_size=(480, 600),
            text_select=False,
            confirm_close=True,
            js_api=api,
        )
        api._window = window
        log("Starting GUI event loop...")
        webview.start(debug=False)
        log("GUI loop ended")
    except Exception as e:
        _fatal_error("webview GUI", e)
        return


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _fatal_error("top-level", e)
