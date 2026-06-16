# -*- coding: utf-8 -*-
"""
打包脚本 v5 - 适配重构后 pywebview + Flask 架构
使用项目 venv 的 Python 3.12 进行打包
入口: webview_main.py (替代旧版 main.py)
新增: templates/ + static/ 前端资源文件
"""
import sys
import os
import shutil
import subprocess

# ── 配置路径 ──
WORKSPACE = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(WORKSPACE, "venv", "Scripts", "python.exe")
DIST_DIR = os.path.join(WORKSPACE, "dist")
BUILD_DIR = os.path.join(WORKSPACE, "build")
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "LensDrawing_v5")

# ── 0. 前置检查 ──
if not os.path.exists(VENV_PYTHON):
    print(f"[Error] venv Python not found at: {VENV_PYTHON}")
    print("  请确保项目 venv 已正确配置")
    sys.exit(1)

print(f"[Info] Using Python: {VENV_PYTHON}")
py_ver = subprocess.run(
    [VENV_PYTHON, "--version"], capture_output=True, text=True
).stdout.strip()
print(f"[Info] Python version: {py_ver}")

# ── 1. 清理旧构建 ──
for d in (DIST_DIR, BUILD_DIR):
    if os.path.exists(d):
        shutil.rmtree(d, ignore_errors=True)
        print(f"[Clean] Removed {d}")

if os.path.exists(OUTPUT_DIR):
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    print(f"[Clean] Removed {OUTPUT_DIR}")

# ── 2. 打补丁：跳过 discover_hook_directories ──
# 亿赛通加密软件会导致 entry_points.txt 被加密，PyInstaller 无法解析
print("[Patch] Patching PyInstaller hook discovery...")
patch_script = os.path.join(WORKSPACE, "patch_pyinstaller.py")
if os.path.exists(patch_script):
    # 在子进程中先运行补丁
    subprocess.run([VENV_PYTHON, patch_script], capture_output=True)
    print("[Patch] External patch applied")
else:
    print("[Patch] No external patch file found, using inline patch")

# ── 3. 寻找并收集所有必要的 DLL ──
venv_site_packages = os.path.join(WORKSPACE, "venv", "Lib", "site-packages")
pythoncom_dir = os.path.join(venv_site_packages, "pywin32_system32")

# 获取 Python 版本号后缀 (如 "312")
py_suffix = py_ver.replace("Python ", "").replace(".", "")[:3]  # e.g. "312"

dll_sources = []

# VC++ 运行时 DLL（从 venv 的 Python 目录或系统目录查找）
venv_root = os.path.join(WORKSPACE, "venv")
for dll_name in ["msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"]:
    # 先从 venv 根目录找
    src = os.path.join(venv_root, dll_name)
    if os.path.exists(src):
        dll_sources.append(src)
        continue
    # 再从系统目录找
    src = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", dll_name)
    if os.path.exists(src):
        dll_sources.append(src)

# pywin32 DLL
for dll_name in [f"pythoncom{py_suffix}.dll", f"pywintypes{py_suffix}.dll"]:
    src = os.path.join(pythoncom_dir, dll_name)
    if os.path.exists(src):
        dll_sources.append(src)

# Pythonwin MFC DLL
mfc_dll = os.path.join(venv_site_packages, "Pythonwin", "mfc140u.dll")
if os.path.exists(mfc_dll):
    dll_sources.append(mfc_dll)

print(f"[DLLs] Found {len(dll_sources)} system DLLs to include")
for d in dll_sources:
    print(f"  - {os.path.basename(d)}")

# ── 4. 获取 matplotlib 数据目录 ──
mpl_data_result = subprocess.run(
    [VENV_PYTHON, "-c", "import matplotlib; print(matplotlib.get_data_path())"],
    capture_output=True, text=True, encoding="utf-8", errors="replace"
)
mpl_data_dir = (mpl_data_result.stdout or "").strip()
if not mpl_data_dir or not os.path.exists(mpl_data_dir):
    print("[Error] Cannot find matplotlib data directory")
    sys.exit(1)
print(f"[Info] matplotlib data: {mpl_data_dir}")

# ── 5. 构建 PyInstaller 参数 ──
print("[Build] Configuring PyInstaller...")

# 运行时 hook
runtime_hooks = []
rthook_path = os.path.join(WORKSPACE, "pyi_rth_pywin32.py")
if os.path.exists(rthook_path):
    runtime_hooks.append(rthook_path)
    print(f"[Build] Runtime hook: {rthook_path}")

# 构建 PyInstaller 命令行
args = [
    VENV_PYTHON, "-m", "PyInstaller",
    "--name", "LensDrawing",
    "--noconfirm",
    "--noconsole",
    "--onedir",
    "--clean",
    "--workpath", BUILD_DIR,
    "--distpath", DIST_DIR,
    "--icon", os.path.join(WORKSPACE, "icon.ico"),
]

# 运行时 hooks
for hook in runtime_hooks:
    args.extend(["--runtime-hook", hook])

# ── 隐藏导入 ──
hidden_imports = [
    # ── pywebview + Flask (新增) ──
    "webview",
    "webview.platforms",
    "webview.platforms.winforms",
    "webview.platforms.cocoa",
    "webview.platforms.qt",
    "flask",
    "flask.json",
    "werkzeug",
    "werkzeug.serving",
    "werkzeug._reloader",
    "jinja2",
    "jinja2.ext",
    "bottle",
    "markupsafe",

    # ── pywin32 COM (批量导入亿赛通加密 Excel) ──
    "win32com",
    "win32com.client",
    "win32com.client.gencache",
    "win32com.client.makepy",
    "win32com.client.dynamic",
    "win32com.client.build",
    "win32com.server",
    "win32com.server.policy",
    "win32com.server.exception",
    "win32com.server.register",
    "pythoncom",
    "pywintypes",
    "win32api",
    "win32con",
    "win32event",
    "win32file",
    "win32gui",
    "win32pdh",
    "win32pipe",
    "win32process",
    "win32security",
    "win32service",
    "win32evtlog",
    "win32evtlogutil",
    "winerror",
    "win32timezone",

    # ── matplotlib (绘图核心) ──
    "matplotlib",
    "matplotlib.backends.backend_agg",
    "matplotlib.figure",
    "matplotlib.patches",
    "matplotlib.pyplot",
    "matplotlib.font_manager",

    # ── numpy ──
    "numpy",
    "numpy.core._multiarray_umath",

    # ── Pillow ──
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "PIL._imaging",

    # ── openpyxl (Excel 读写) ──
    "openpyxl",
    "openpyxl.cell",
    "openpyxl.styles",
    "openpyxl.worksheet",

    # ── 其他依赖 ──
    "fontTools",
    "kiwisolver",
    "contourpy",
    "cffi",
    "unittest",          # pyparsing.testing 依赖 unittest，必须显式包含

    # ── pythonnet (pywebview WinForms 后端必需) ──
    "pythonnet",
    "clr",
    "clr_loader",
    "clr_loader.ffi",
    "clr_loader.ffi.hostfxr",
    "clr_loader.util",
    "runtime",

    # ── 项目模块 ──
    "geometry",
    "config",
    "settings",
    "batch_import",
]

for mod in hidden_imports:
    args.extend(["--hidden-import", mod])

# ── 数据文件 ──
data_files = [
    # Flask 模板和静态资源 (新增 - 重构后核心前端文件)
    (os.path.join(WORKSPACE, "templates"), "templates"),
    (os.path.join(WORKSPACE, "static"), "static"),
    # 设置文件
    (os.path.join(WORKSPACE, "app_settings.json"), "."),
    # matplotlib 数据
    (mpl_data_dir, "matplotlib/mpl-data"),
]

for src, dst in data_files:
    if os.path.exists(src):
        args.extend(["--add-data", f"{src};{dst}"])
    else:
        print(f"[Warning] Data source not found: {src}")

# ── 收集 webview 子包数据 (WebView2 DLLs 等) ──
collect_submodules = [
    "webview",
    "webview.platforms",
    "webview.platforms.winforms",
]
for pkg in collect_submodules:
    args.extend(["--collect-submodules", pkg])

# 收集 webview 的数据文件 (DLL 等)
args.extend(["--collect-data", "webview"])

# ── 收集 pythonnet 全部 (pywebview WinForms 后端必需) ──
args.extend(["--collect-submodules", "pythonnet"])
args.extend(["--collect-submodules", "clr_loader"])
args.extend(["--collect-data", "pythonnet"])
args.extend(["--collect-data", "clr_loader"])

# ── 收集 pywin32 全部 ──
args.extend(["--collect-submodules", "win32com"])
args.extend(["--collect-submodules", "win32"])
args.extend(["--collect-data", "win32"])
args.extend(["--collect-data", "pywin32_system32"])

# ── 排除不需要的大包 ──
exclude_packages = [
    # 注意: 不能排除 pythonnet 和 clr_loader! pywebview WinForms 后端依赖它们
    "Pythonwin",     # MFC UI 框架 - 仅 COM 读取需要 pythoncom, 不需要 Pythonwin IDE
    "isapi",         # IIS 扩展 - 不需要
    "adodbapi",      # 旧版 ADO - 不需要
    "test",          # 测试包
    # 注意: 不能排除 unittest! pyparsing.testing 依赖它, pyparsing.__init__ 会自动导入 testing
    # "unittest",
]
for pkg in exclude_packages:
    args.extend(["--exclude-module", pkg])

# ── 主脚本 (入口改为 webview_main.py) ──
args.append(os.path.join(WORKSPACE, "webview_main.py"))

# ── 6. 执行打包 ──
print("[Build] Running PyInstaller...")
print(f"[Build] Command: {' '.join(args[:10])}...")

try:
    result = subprocess.run(args, capture_output=True, cwd=WORKSPACE)
    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    print(stdout[-3000:] if len(stdout) > 3000 else stdout)
    if result.returncode != 0:
        print(f"[Error] PyInstaller failed with code {result.returncode}")
        print(stderr[-3000:] if len(stderr) > 3000 else stderr)
        sys.exit(result.returncode)
except Exception as e:
    print(f"[Error] {e}")
    sys.exit(1)

# ── 7. 复制额外的 DLL 到 _internal ──
internal_dir = os.path.join(DIST_DIR, "LensDrawing", "_internal")
if dll_sources and os.path.exists(internal_dir):
    print(f"[DLLs] Copying runtime DLLs to {internal_dir}...")
    for src in dll_sources:
        dst = os.path.join(internal_dir, os.path.basename(src))
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
            print(f"  Copied: {os.path.basename(src)}")
        else:
            print(f"  Already exists: {os.path.basename(src)}")

# 确保 pywin32_system32 目录存在于 _internal
pywin32_sys_dir = os.path.join(internal_dir, "pywin32_system32")
os.makedirs(pywin32_sys_dir, exist_ok=True)
for src in dll_sources:
    if "pythoncom" in os.path.basename(src) or "pywintypes" in os.path.basename(src):
        dst = os.path.join(pywin32_sys_dir, os.path.basename(src))
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
            print(f"  Copied to pywin32_system32: {os.path.basename(src)}")

# ── 8. 复制 WebView2 Runtime (如果本地有) ──
webview_lib_dir = os.path.join(venv_site_packages, "webview", "lib")
if os.path.exists(webview_lib_dir):
    print(f"[WebView2] Checking WebView2 libraries...")
    target_webview_dir = os.path.join(internal_dir, "webview", "lib")
    os.makedirs(target_webview_dir, exist_ok=True)
    for item in os.listdir(webview_lib_dir):
        src = os.path.join(webview_lib_dir, item)
        dst = os.path.join(target_webview_dir, item)
        if os.path.isfile(src):
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                print(f"  Copied: {item}")
        elif os.path.isdir(src):
            if not os.path.exists(dst):
                shutil.copytree(src, dst)
                print(f"  Copied dir: {item}")

# ── 9. 验证关键文件 ──
print("[Verify] Checking distribution integrity...")

# 确定 python dll 后缀
py_dll = f"python{py_suffix}.dll"

checks = [
    ("EXE", os.path.join(DIST_DIR, "LensDrawing", "LensDrawing.exe")),
    ("Python DLL", os.path.join(internal_dir, py_dll)),
    ("Settings", os.path.join(internal_dir, "app_settings.json")),
    ("Templates", os.path.join(internal_dir, "templates", "launcher.html")),
    ("Draw HTML", os.path.join(internal_dir, "templates", "draw.html")),
    ("Batch HTML", os.path.join(internal_dir, "templates", "batch.html")),
    ("Settings HTML", os.path.join(internal_dir, "templates", "settings.html")),
    ("Style CSS", os.path.join(internal_dir, "static", "css", "style.css")),
    ("Draw JS", os.path.join(internal_dir, "static", "js", "draw.js")),
    ("Batch JS", os.path.join(internal_dir, "static", "js", "batch.js")),
    ("MPL Data", os.path.join(internal_dir, "matplotlib", "mpl-data")),
]

all_ok = True
for name, path in checks:
    exists = os.path.exists(path)
    status = "OK" if exists else "MISSING"
    print(f"  [{status}] {name}: {os.path.basename(path)}")
    if not exists:
        all_ok = False

if not all_ok:
    print("[Warning] Some expected files are missing!")

# ── 10. 复制到桌面 ──
source = os.path.join(DIST_DIR, "LensDrawing")
if os.path.exists(source):
    shutil.copytree(source, OUTPUT_DIR)
    print(f"\n[Done] Copied to: {OUTPUT_DIR}")
    print(f"[Done] Executable: {os.path.join(OUTPUT_DIR, 'LensDrawing.exe')}")
else:
    print(f"[Error] Expected output not found: {source}")
    sys.exit(1)

# ── 11. 计算包体大小 ──
total_size = 0
for dirpath, dirnames, filenames in os.walk(OUTPUT_DIR):
    for f in filenames:
        fp = os.path.join(dirpath, f)
        total_size += os.path.getsize(fp)
size_mb = total_size / (1024 * 1024)

print("\n" + "=" * 60)
print(f"Build completed successfully!")
print(f"Total size: {size_mb:.1f} MB")
print(f"Output: {OUTPUT_DIR}")
print("=" * 60)
