# encoding: utf-8
# DEV CAPTURE: PLC (DEV) -> Git (dev branch)
#
# Usage (CODESYS headless):
# --runscript="C:\PLC_REPO\scripts\dev_capture.py" --scriptargs:"C:\Users\Test_bench\Documents\PLC_DEV.project"

import os, sys, time, re, datetime, subprocess, traceback

REPO_ROOT = r"C:\PLC_REPO"
EXPORTS_ROOT = os.path.join(REPO_ROOT, "exports")
PLCOPEN_DIR = os.path.join(EXPORTS_ROOT, "plcopen")
ARCH_DIR = os.path.join(EXPORTS_ROOT, "archives", "dev")
LOG_DIR = os.path.join(REPO_ROOT, "Logs")
TIMEOUT_S = 120

PLCOPEN_ONEFILE_NAME = "PLC_latest.plcopen.xml"
BRANCH = "dev"
PLC_NAME = "PLC_DEV"

def _ensure_dir(p):
    if not os.path.isdir(p):
        os.makedirs(p)

def _init_logging():
    _ensure_dir(LOG_DIR)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, "dev_capture_%s.log" % ts)
    f = open(log_path, "a", buffering=1)

    class _Tee(object):
        def __init__(self, *streams): self.streams = streams
        def write(self, s):
            for st in self.streams:
                try: st.write(s)
                except: pass
        def flush(self):
            for st in self.streams:
                try: st.flush()
                except: pass

    sys.stdout = _Tee(sys.__stdout__, f)
    sys.stderr = _Tee(sys.__stderr__, f)
    print("===== dev_capture started:", ts, "=====")
    print("Log:", log_path)
    return log_path

LOG_PATH = _init_logging()

def _run_git(args):
    p = subprocess.Popen(["git"] + args, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    out, err = p.communicate()
    return p.returncode, out.decode("utf-8","replace"), err.decode("utf-8","replace")

def _git_checkout(branch):
    rc, out, err = _run_git(["checkout", branch])
    if rc != 0:
        # create from origin if needed
        rc2, out2, err2 = _run_git(["checkout", "-B", branch, "origin/%s" % branch])
        if rc2 != 0:
            print("GIT: checkout failed"); print(out+out2); print(err+err2)
            return False
    return True

def _git_commit_all_if_dirty(branch, msg):
    rc, st, err = _run_git(["status", "--porcelain"])
    if rc != 0:
        print("GIT: status failed"); print(st); print(err)
        return False, "status failed"
    st = st.strip()
    if not st:
        print("GIT: clean, nothing to commit.")
        return True, "clean"

    print("GIT: changes -> committing repo")
    print(st)

    rc1, o1, e1 = _run_git(["add", "-A"])
    if rc1 != 0:
        print("GIT: add failed"); print(o1); print(e1)
        return False, "add failed"

    rc2, o2, e2 = _run_git(["commit", "-m", msg])
    if rc2 != 0:
        if "nothing to commit" in (o2+e2).lower():
            return True, "nothing to commit"
        print("GIT: commit failed"); print(o2); print(e2)
        return False, "commit failed"

    rc3, o3, e3 = _run_git(["push", "origin", branch])
    if rc3 != 0:
        print("GIT: push failed"); print(o3); print(e3)
        return False, "push failed"

    return True, "committed+push"

def normalize_plcopen_xml(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8-sig"); enc="utf-8"
    except:
        text = raw.decode("latin-1"); enc="latin-1"

    original = text
    text = re.sub(r'creationDateTime="[^"]+"', 'creationDateTime="1970-01-01T00:00:00"', text)
    text = re.sub(r'modificationDateTime="[^"]+"', 'modificationDateTime="1970-01-01T00:00:00"', text)

    if text != original:
        with open(path, "wb") as f:
            f.write(text.encode(enc, errors="replace"))
        print("PLCOPEN: normalized volatile metadata.")

def _close_projects_best_effort():
    try:
        p = projects.primary
        if p is not None and hasattr(p, "close"):
            try: p.close()
            except: pass
    except:
        pass

def _open_project_primary(project_path):
    _close_projects_best_effort()
    proj = projects.primary
    if proj is None:
        proj = projects.open(project_path, primary=True)
    return proj

def _wait_active_app(proj):
    start = time.time()
    while (time.time() - start) < TIMEOUT_S:
        if hasattr(proj, "active_application"):
            app = proj.active_application
            if app is not None:
                return app
        time.sleep(1)
    return None

def _connect_and_login(app, user, pw):
    online_app = online.create_online_application(app)
    dev = online_app.get_online_device()

    if user and pw:
        try: online.set_specific_credentials(dev, user, pw)
        except: pass
        if hasattr(dev, "set_credentials_for_initial_user"):
            try: dev.set_credentials_for_initial_user(user, pw)
            except: pass

    if not (hasattr(dev, "connected") and dev.connected):
        dev.connect()

    start = time.time()
    while (time.time() - start) < TIMEOUT_S:
        if hasattr(dev, "connected") and dev.connected:
            break
        time.sleep(0.5)

    if not (hasattr(dev, "connected") and dev.connected):
        raise Exception("Device did not connect")

    if not online_app.is_logged_in:
        OnlineChangeOption = globals().get("OnlineChangeOption", None)
        if OnlineChangeOption is None:
            raise Exception("OnlineChangeOption missing")
        online_app.login(OnlineChangeOption.Keep, False)
        if not online_app.is_logged_in:
            raise Exception("Login failed")

    return online_app, dev

def _disconnect_best_effort(online_app, dev):
    try:
        if hasattr(dev, "connected") and dev.connected and hasattr(dev, "disconnect"):
            dev.disconnect()
    except: pass
    try:
        if hasattr(online_app, "logout"):
            online_app.logout()
    except: pass

def main():
    if len(sys.argv) < 2:
        print("ERROR: Missing DEV project path")
        print('--scriptargs:"C:\\Users\\Test_bench\\Documents\\PLC_DEV.project"')
        system.exit()

    project_path = sys.argv[1].strip().strip('"')
    print("DEV project:", project_path)

    _ensure_dir(PLCOPEN_DIR)
    _ensure_dir(ARCH_DIR)

    # Ensure dev branch
    if not _git_checkout(BRANCH):
        print("ERROR: cannot checkout dev")
        system.exit()

    user = os.environ.get("CODESYS_USER","")
    pw   = os.environ.get("CODESYS_PASS","")

    proj = _open_project_primary(project_path)
    app = _wait_active_app(proj)
    if app is None:
        print("ERROR: active_application timeout")
        system.exit()

    online_app, dev = _connect_and_login(app, user, pw)
    try:
        if hasattr(online_app, "source_download"):
            print("[dev] source_download")
            online_app.source_download()

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = os.path.join(ARCH_DIR, "%s_%s.projectarchive" % (PLC_NAME, ts))
        if hasattr(proj, "save_archive"):
            print("[dev] save_archive ->", archive_path)
            proj.save_archive(archive_path)

        plcopen_path = os.path.join(PLCOPEN_DIR, PLCOPEN_ONEFILE_NAME)

        class ER(ExportReporter):
            def error(self, obj, message): print("PLCOPEN ERROR:", message)
            def warning(self, obj, message): print("PLCOPEN WARN:", message)
            def nonexportable(self, obj): print("PLCOPEN nonexportable:", obj)
            @property
            def aborting(self): return False

        print("[dev] export PLCopen ->", plcopen_path)
        proj.active_application.export_xml(ER(), plcopen_path, recursive=True)
        normalize_plcopen_xml(plcopen_path)

    finally:
        _disconnect_best_effort(online_app, dev)

    # Commit everything if changed
    msg = "DEV capture %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok, note = _git_commit_all_if_dirty(BRANCH, msg)
    print("GIT:", note)
    print("Log:", LOG_PATH)
    print("===== dev_capture finished =====")
    try: system.exit()
    except: pass

try:
    main()
except Exception as e:
    print("FATAL:", repr(e))
    traceback.print_exc()
    try: system.exit()
    except: pass
