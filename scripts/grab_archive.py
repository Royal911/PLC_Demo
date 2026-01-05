# encoding: utf-8
# Headless CODESYS ScriptEngine:
# open project -> (optional) inject creds -> connect -> login -> source download
# -> export archive + PLCopen XML -> git commit if changed -> exit
#
# Logs:   C:\PLC_REPO\Logs\grab_archive_YYYYMMDD_HHMMSS.log
# Exports: C:\PLC_REPO\exports\archives\*.projectarchive
#          C:\PLC_REPO\exports\plcopen\PLC_DEV.xml  (+ timestamped copy)

import os
import datetime
import time
import sys
import subprocess

# -------------------------
# CONFIG
# -------------------------
REPO_ROOT = r"C:\PLC_REPO"
OUT_DIR = os.path.join(REPO_ROOT, "exports", "archives")
PLCOPEN_DIR = os.path.join(REPO_ROOT, "exports", "plcopen")
LOG_DIR = os.path.join(REPO_ROOT, "Logs")
TIMEOUT_S = 120

PLC_NAME = "PLC_DEV"  # used for filenames + git commit message

# -------------------------
# Logging setup (redirect print/stdout/stderr to file)
# -------------------------
def _init_logging():
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, "grab_archive_%s.log" % ts)

    f = open(log_path, "a", buffering=1)

    class _Tee(object):
        def __init__(self, *streams):
            self.streams = streams
        def write(self, s):
            for st in self.streams:
                try:
                    st.write(s)
                except:
                    pass
        def flush(self):
            for st in self.streams:
                try:
                    st.flush()
                except:
                    pass

    sys.stdout = _Tee(sys.__stdout__, f)
    sys.stderr = _Tee(sys.__stderr__, f)

    print("===== grab_archive started:", ts, "=====")
    print("Log file:", log_path)
    return log_path

LOG_PATH = _init_logging()

def _ensure_dir(p):
    if not os.path.isdir(p):
        os.makedirs(p)

def _run_git(args, check=False):
    """
    Runs git in REPO_ROOT. Returns (rc, stdout, stderr).
    Uses subprocess so it works from ScriptEngine.
    """
    try:
        p = subprocess.Popen(
            ["git"] + args,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False
        )
        out, err = p.communicate()
        out_s = out.decode("utf-8", errors="replace")
        err_s = err.decode("utf-8", errors="replace")
        if check and p.returncode != 0:
            print("GIT ERROR running:", "git " + " ".join(args))
            print("stdout:", out_s)
            print("stderr:", err_s)
        return p.returncode, out_s, err_s
    except Exception as e:
        print("WARNING: Git command failed (is Git installed / in PATH?):", repr(e))
        return 1, "", repr(e)

def _git_is_repo():
    return os.path.isdir(os.path.join(REPO_ROOT, ".git"))

# -------------------------
# Args
# -------------------------
if len(sys.argv) < 2:
    print("ERROR: Missing project path argument.")
    print('Usage: --scriptargs:"C:\\path\\to\\PLC_DEV.project"')
    system.exit()

PROJECT_PATH = sys.argv[1]
print("Project path:", PROJECT_PATH)

# Optional credentials: use if provided; otherwise rely on locally stored creds
CODESYS_USER = os.environ.get("CODESYS_USER", "")
CODESYS_PASS = os.environ.get("CODESYS_PASS", "")
if CODESYS_USER and CODESYS_PASS:
    print("Env credentials detected: will inject for this run.")
else:
    print("No env credentials provided: relying on locally stored credentials.")

_ensure_dir(OUT_DIR)
_ensure_dir(PLCOPEN_DIR)

# -------------------------
# Open project (headless-safe)
# -------------------------
proj = projects.primary
if proj is None:
    print("No primary project loaded. Opening project...")
    proj = projects.open(PROJECT_PATH, primary=True)

# Wait for active application
start = time.time()
app = None
while (time.time() - start) < TIMEOUT_S:
    if hasattr(proj, "active_application"):
        app = proj.active_application
        if app is not None:
            break
    time.sleep(1)

if app is None:
    print("ERROR: active_application not available within %ss." % TIMEOUT_S)
    system.exit()

# -------------------------
# Online wrapper
# -------------------------
online_app = online.create_online_application(app)
print("Online wrapper created.")
print("Logged in:", online_app.is_logged_in)

dev = online_app.get_online_device()

# -------------------------
# Optional credential injection (avoids Password Manager prompts)
# -------------------------
if CODESYS_USER and CODESYS_PASS:
    print("Injecting credentials for this script run (avoid Password Manager)...")
    try:
        online.set_specific_credentials(dev, CODESYS_USER, CODESYS_PASS)
    except Exception as e:
        print("WARNING: online.set_specific_credentials failed:", repr(e))

    if hasattr(dev, "set_credentials_for_initial_user"):
        try:
            dev.set_credentials_for_initial_user(CODESYS_USER, CODESYS_PASS)
            print("set_credentials_for_initial_user applied.")
        except Exception as e:
            print("WARNING: set_credentials_for_initial_user failed:", repr(e))

# -------------------------
# Connect device
# -------------------------
try:
    if hasattr(dev, "connected") and dev.connected:
        print("Device already connected.")
    else:
        print("Connecting online device...")
        dev.connect()
except Exception as e:
    print("ERROR: dev.connect() failed:", repr(e))
    system.exit()

# Wait until connected
start = time.time()
while (time.time() - start) < TIMEOUT_S:
    if hasattr(dev, "connected") and dev.connected:
        break
    time.sleep(0.5)

if not (hasattr(dev, "connected") and dev.connected):
    print("ERROR: Device did not connect within %ss." % TIMEOUT_S)
    system.exit()

print("Device connected:", dev.connected)

# -------------------------
# Login to application
# -------------------------
if not online_app.is_logged_in:
    if "OnlineChangeOption" not in globals():
        print("ERROR: OnlineChangeOption enum not found in globals().")
        system.exit()

    OnlineChangeOption = globals()["OnlineChangeOption"]

    print("Logging in (headless) with OnlineChangeOption.Keep ...")
    try:
        online_app.login(OnlineChangeOption.Keep, False)
    except Exception as e:
        print("ERROR: online_app.login(...) failed:", repr(e))
        system.exit()

    print("Logged in now:", online_app.is_logged_in)
    if not online_app.is_logged_in:
        print("ERROR: Login failed.")
        system.exit()

# -------------------------
# Source download from controller (if supported)
# -------------------------
if hasattr(online_app, "source_download"):
    print("Pulling source from controller (online_app.source_download)...")
    online_app.source_download()
    print("Source pull done.")
elif hasattr(online_app, "source_upload"):
    print("Pulling source from controller (online_app.source_upload)...")
    online_app.source_upload()
    print("Source pull done.")
else:
    print("No source upload/download method found on online_app. Continuing...")

# -------------------------
# Save project archive
# -------------------------
ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
archive_path = os.path.join(OUT_DIR, "%s_%s.projectarchive" % (PLC_NAME, ts))
print("Saving archive to:", archive_path)

if hasattr(proj, "save_archive"):
    proj.save_archive(archive_path)
    print("Archive saved OK.")
else:
    print("ERROR: proj.save_archive() not available in this environment.")
    system.exit()

# -------------------------
# Export PLCopen XML (for diffs)
# -------------------------
# We keep a stable filename for Git diffs + a timestamped snapshot for traceability.
plcopen_stable = os.path.join(PLCOPEN_DIR, "%s.xml" % PLC_NAME)
plcopen_ts = os.path.join(PLCOPEN_DIR, "%s_%s.xml" % (PLC_NAME, ts))

exported = False
try:
    # Different builds expose export differently; try common patterns safely.
    # If your CODESYS exposes a direct PLCopen export on the project object, it is often named like export_plcopenxml.
    if hasattr(proj, "export_plcopenxml"):
        print("Exporting PLCopen XML via proj.export_plcopenxml...")
        proj.export_plcopenxml(plcopen_stable)
        exported = True
    elif hasattr(app, "export_plcopenxml"):
        print("Exporting PLCopen XML via app.export_plcopenxml...")
        app.export_plcopenxml(plcopen_stable)
        exported = True
    else:
        print("PLCopen export method not found (export_plcopenxml). Skipping XML export for now.")
except Exception as e:
    print("WARNING: PLCopen export failed:", repr(e))

if exported:
    # Copy to timestamped snapshot too
    try:
        if os.path.isfile(plcopen_stable):
            with open(plcopen_stable, "rb") as src:
                with open(plcopen_ts, "wb") as dst:
                    dst.write(src.read())
            print("PLCopen XML saved:", plcopen_stable)
            print("PLCopen XML snapshot:", plcopen_ts)
    except Exception as e:
        print("WARNING: Could not create timestamped PLCopen snapshot:", repr(e))

# -------------------------
# Git commit if repo exists and changes present
# -------------------------
if _git_is_repo():
    print("Git repo detected. Checking for changes...")
    rc, out, err = _run_git(["status", "--porcelain"])
    if rc == 0 and out.strip():
        print("Changes detected. Committing...")

        _run_git(["add", "-A"])

        msg = "Day 2: %s export %s" % (PLC_NAME, ts)
        rc2, out2, err2 = _run_git(["commit", "-m", msg])
        if rc2 == 0:
            print("Git commit OK:", msg)
        else:
            print("WARNING: Git commit failed.")
            print(out2)
            print(err2)
    else:
        print("No git changes to commit.")
else:
    print("No .git folder found in", REPO_ROOT, "- skipping git steps.")

# -------------------------
# Optional disconnect
# -------------------------
try:
    if hasattr(dev, "connected") and dev.connected and hasattr(dev, "disconnect"):
        dev.disconnect()
except Exception as e:
    print("WARNING: disconnect failed:", repr(e))

print("===== grab_archive finished OK =====")
system.exit()
