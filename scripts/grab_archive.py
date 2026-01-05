# encoding: utf-8
# Headless CODESYS ScriptEngine:
# open project -> (optional) inject creds -> connect -> login -> source download
# -> export archive + PLCopen XML -> normalize PLCopen (remove volatile metadata) -> git commit if changed -> exit
#


import os
import datetime
import time
import sys
import subprocess
import re

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
    
def _git_current_branch():
    rc, out, err = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0:
        return out.strip()
    return ""
def _git_has_origin():
    rc, out, err = _run_git(["remote"])
    return (rc == 0) and ("origin" in out.split())


# -------------------------
# PLCopen normalization (remove volatile metadata so it doesn't commit every run)
# -------------------------
import re

import re

def normalize_plcopen_xml(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()

        # BOM-safe decode
        try:
            text = raw.decode("utf-8-sig")
            enc = "utf-8"
        except:
            text = raw.decode("latin-1")
            enc = "latin-1"

        original = text

        # 1) Ignore volatile header timestamps
        text = re.sub(r'creationDateTime="[^"]+"', 'creationDateTime="1970-01-01T00:00:00"', text)
        text = re.sub(r'modificationDateTime="[^"]+"', 'modificationDateTime="1970-01-01T00:00:00"', text)

        # 2) Canonicalize PlaceholderRedirections block (sort + fixed indent)
        m = re.search(r"<PlaceholderRedirections>.*?</PlaceholderRedirections>", text, flags=re.DOTALL)
        if m:
            block = m.group(0)

            # Grab every PlaceholderRedirection line, strip whitespace to remove indent noise
            redirs = []
            for ln in block.splitlines():
                if "<PlaceholderRedirection" in ln:
                    redirs.append(ln.strip())

            # Sort and dedupe for deterministic output
            redirs = sorted(set(redirs))

            indent = "  "  # consistent indentation
            if redirs:
                new_block = "<PlaceholderRedirections>\n" + "\n".join([indent + r for r in redirs]) + "\n</PlaceholderRedirections>"
            else:
                new_block = "<PlaceholderRedirections>\n</PlaceholderRedirections>"

            text = text[:m.start()] + new_block + text[m.end():]

        if text != original:
            with open(path, "wb") as f:
                f.write(text.encode(enc, errors="replace"))
            print("Normalized PLCopen XML (timestamps + PlaceholderRedirections).")
        else:
            print("PLCopen XML already normalized/stable.")

    except Exception as e:
        print("WARNING: normalize_plcopen_xml failed:", repr(e))


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
# Export PLCopen XML (single stable file for Git diffs)
# -------------------------
plcopen_latest = os.path.join(PLCOPEN_DIR, "%s_latest.plcopen.xml" % PLC_NAME)

class ER(ExportReporter):
    def error(self, obj, message):
        print("PLCOPEN export ERROR on %s: %s" % (obj, message))
    def warning(self, obj, message):
        print("PLCOPEN export WARNING on %s: %s" % (obj, message))
    def nonexportable(self, obj):
        print("PLCOPEN not exportable: %s" % obj)
    @property
    def aborting(self):
        return False

reporter = ER()

print("Exporting PLCopen XML (latest) to:", plcopen_latest)

export_ok = False

# Try application export first
try:
    app_obj = proj.active_application
    app_obj.export_xml(reporter, plcopen_latest, recursive=True)
    export_ok = True
    print("PLCopen XML export OK via app.export_xml")
except Exception as e:
    print("PLCopen export via app.export_xml failed:", repr(e))

# Fallback to project export
if not export_ok:
    try:
        proj.export_xml(reporter, proj.get_children(False), plcopen_latest, recursive=True)
        export_ok = True
        print("PLCopen XML export OK via proj.export_xml")
    except Exception as e:
        print("PLCopen export via proj.export_xml failed:", repr(e))

if not export_ok:
    print("PLCopen XML export failed (no output written).")
    system.exit()

# Normalize PLCopen XML to remove volatile timestamps so it won't commit every run
normalize_plcopen_xml(plcopen_latest)

# -------------------------
# Git commit ONLY if git diff produces output for the PLCopen file
# + git push ONLY if commit succeeded
# -------------------------
if _git_is_repo():
    print("Git repo detected. Checking for PLCopen diff output...")

    rel_xml = os.path.relpath(plcopen_latest, REPO_ROOT).replace("\\", "/")

    # Run git diff WITHOUT --quiet so we can check output text
    rc, diff_out, diff_err = _run_git(["diff", "--", rel_xml])

    if diff_out.strip() == "":
        print("No diff output (files equal). Skipping commit.")
    else:
        print("Diff output detected. Committing...")

        # Stage only that file
        _run_git(["add", "--", rel_xml])

        msg = "%s export %s" % (PLC_NAME, ts)
        rc2, out2, err2 = _run_git(["commit", "-m", msg])

        if rc2 == 0:
            print("Git commit OK:", msg)

            # Push only after successful commit
            if _git_has_origin():
                branch = _git_current_branch()
                if not branch:
                    branch = "master"  # fallback
                print("Pushing to origin/%s ..." % branch)
                rc3, out3, err3 = _run_git(["push", "origin", branch])
                if rc3 == 0:
                    print("Git push OK.")
                else:
                    print("WARNING: Git push failed.")
                    print(out3)
                    print(err3)
            else:
                print("No 'origin' remote configured. Skipping push.")
        else:
            print("WARNING: Git commit failed.")
            print(out2)
            print(err2)

        # Optional: write diff into the log for debugging/audit
        try:
            print("----- git diff (truncated) -----")
            print(diff_out[:4000])
            print("----- end diff -----")
        except:
            pass
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
