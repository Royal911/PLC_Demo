# encoding: utf-8
# Headless CODESYS ScriptEngine:
# open project -> create online app -> inject credentials (NO Password Manager) -> connect -> login
# -> source download -> save archive -> exit
#
# This version writes ALL output to: C:\PLC_REPO\Logs\grab_archive_YYYYMMDD_HHMMSS.log

import os
import datetime
import time
import sys

# -------------------------
# CONFIG
# -------------------------
OUT_DIR = r"C:\PLC_REPO\exports\archives"
LOG_DIR = r"C:\PLC_REPO\Logs"
TIMEOUT_S = 120

# -------------------------
# Logging setup (redirect print/stdout/stderr to file)
# -------------------------
def _init_logging():
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, "grab_archive_%s.log" % ts)

    # Open as line-buffered so you see output even if the process crashes
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

    # Keep console output + also write to file
    sys.stdout = _Tee(sys.__stdout__, f)
    sys.stderr = _Tee(sys.__stderr__, f)

    print("===== grab_archive started:", ts, "=====")
    print("Log file:", log_path)
    return log_path

LOG_PATH = _init_logging()

# -------------------------
# Helpers
# -------------------------
def fail(msg):
    print("ERROR:", msg)
    try:
        system.exit()
    except:
        pass

# -------------------------
# Args / creds
# -------------------------
if len(sys.argv) < 2:
    print("ERROR: Missing project path argument.")
    print('Usage: --scriptargs:"C:\\path\\to\\PLC_DEV.project"')
    system.exit()

PROJECT_PATH = sys.argv[1]
print("Project path:", PROJECT_PATH)

# Credentials provided per-run (recommended via BAT wrapper / Task Scheduler)
CODESYS_USER = os.environ.get("CODESYS_USER", "")
CODESYS_PASS = os.environ.get("CODESYS_PASS", "")
if not CODESYS_USER or not CODESYS_PASS:
    print("ERROR: Missing CODESYS_USER or CODESYS_PASS environment variables.")
    system.exit()

if not os.path.isdir(OUT_DIR):
    os.makedirs(OUT_DIR)

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
# Inject credentials WITHOUT using saved credentials / Password Manager
# Key line: online.set_specific_credentials(...)
# -------------------------
print("Injecting credentials for this script run (avoid Password Manager)...")
try:
    online.set_specific_credentials(dev, CODESYS_USER, CODESYS_PASS)
except Exception as e:
    print("WARNING: online.set_specific_credentials failed:", repr(e))

# Optional fallback: some runtimes honor this for initial user auth
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
# Login to application (OnlineChangeOption on your system: Keep / Force / Never)
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
archive_path = os.path.join(OUT_DIR, "PLC_DEV_%s.projectarchive" % ts)
print("Saving archive to:", archive_path)

if hasattr(proj, "save_archive"):
    proj.save_archive(archive_path)
    print("Archive saved OK.")
else:
    print("ERROR: proj.save_archive() not available in this environment.")
    system.exit()

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
