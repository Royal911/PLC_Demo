# encoding: utf-8
# DEPLOY STAGING: Git (staging branch) -> PLC STG runtime
# Usage:
# --runscript="C:\PLC_REPO\scripts\deploy_staging.py" --scriptargs:"C:\Users\Test_bench\Documents\PLC_STG.project"

import os, sys, time, subprocess, traceback

REPO_ROOT = r"C:\PLC_REPO"
TIMEOUT_S = 120
BRANCH = "prod"

def _run_git(args):
    p = subprocess.Popen(["git"] + args, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    out, err = p.communicate()
    return p.returncode, out.decode("utf-8","replace"), err.decode("utf-8","replace")

def _git_checkout_and_update(branch):
    rc, out, err = _run_git(["checkout", branch])
    if rc != 0:
        rc2, out2, err2 = _run_git(["checkout", "-B", branch, "origin/%s" % branch])
        if rc2 != 0:
            print("GIT checkout failed"); print(out+out2); print(err+err2)
            return False
    rc3, out3, err3 = _run_git(["pull", "--ff-only", "origin", branch])
    if rc3 != 0:
        print("GIT pull failed"); print(out3); print(err3)
        return False
    return True

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

def _try_download(online_app):
    OnlineChangeOption = globals().get("OnlineChangeOption", None)
    methods = ["download", "application_download", "program_download"]
    opts = ["Download", "FullDownload", "All", "Keep"]
    last = "n/a"

    for m in methods:
        if not hasattr(online_app, m):
            continue
        fn = getattr(online_app, m)
        for o in opts:
            if OnlineChangeOption and hasattr(OnlineChangeOption, o):
                try:
                    fn(getattr(OnlineChangeOption, o), False)
                    return True, "%s(%s)" % (m, o)
                except Exception as e:
                    last = repr(e)
        try:
            fn()
            return True, "%s()" % m
        except Exception as e:
            last = repr(e)

    return False, last

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
        print("ERROR: Missing STG project path")
        system.exit()

    project_path = sys.argv[1].strip().strip('"')
    print("STG project:", project_path)

    if not _git_checkout_and_update(BRANCH):
        print("ERROR: git checkout/pull failed")
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
        ok, used = _try_download(online_app)
        if not ok:
            print("ERROR: download failed:", used)
            system.exit()
        print("DEPLOY OK:", used)
    finally:
        _disconnect_best_effort(online_app, dev)

    try: system.exit()
    except: pass

try:
    main()
except Exception as e:
    print("FATAL:", repr(e))
    traceback.print_exc()
    try: system.exit()
    except: pass
