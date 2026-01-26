# encoding: utf-8
# DEPLOY STAGING: Git (staging branch) -> PLC STG runtime
#
# Your environment:
# - online_app exposes create_boot_application() (no download/program_download methods)
# - After create_boot_application(), your runtime leaves the application STOPPED
# - This script explicitly STARTS the application after boot creation (best-effort)
#
# Usage (CODESYS headless):
# --runscript="C:\PLC_REPO\scripts\deploy_staging.py" --scriptargs:"C:\Users\Test_bench\Documents\PLC_STG.project"

import os
import sys
import time
import subprocess
import traceback

REPO_ROOT = r"C:\PLC_REPO"
TIMEOUT_S = 120
BRANCH = "staging"


# -------------------------
# Git helpers
# -------------------------
def _run_git(args):
    p = subprocess.Popen(
        ["git"] + args,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    out, err = p.communicate()
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _git_checkout_and_update(branch):
    rc, out, err = _run_git(["checkout", branch])
    if rc != 0:
        rc2, out2, err2 = _run_git(["checkout", "-B", branch, "origin/%s" % branch])
        if rc2 != 0:
            print("GIT checkout failed")
            print(out + out2)
            print(err + err2)
            return False

    rc3, out3, err3 = _run_git(["pull", "--ff-only", "origin", branch])
    if rc3 != 0:
        print("GIT pull failed")
        print(out3)
        print(err3)
        return False
    return True


# -------------------------
# CODESYS helpers
# -------------------------
def _close_projects_best_effort():
    try:
        p = projects.primary
        if p is not None and hasattr(p, "close"):
            try:
                p.close()
            except:
                pass
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
        print("Online: injecting env credentials")
        try:
            online.set_specific_credentials(dev, user, pw)
        except Exception as e:
            print("WARN: set_specific_credentials failed:", repr(e))
        if hasattr(dev, "set_credentials_for_initial_user"):
            try:
                dev.set_credentials_for_initial_user(user, pw)
            except Exception as e:
                print("WARN: set_credentials_for_initial_user failed:", repr(e))
    else:
        print("Online: relying on stored credentials")

    # Connect (retry - gateway can be flaky in headless)
    last_err = None
    for attempt in range(1, 4):
        try:
            if hasattr(dev, "connected") and dev.connected:
                print("Online: already connected")
                break
            print("Online: connecting... attempt", attempt)
            dev.connect()
            break
        except Exception as e:
            last_err = e
            print("Online: connect failed:", repr(e))
            time.sleep(3)

    start = time.time()
    while (time.time() - start) < TIMEOUT_S:
        if hasattr(dev, "connected") and dev.connected:
            break
        time.sleep(0.5)

    if not (hasattr(dev, "connected") and dev.connected):
        raise Exception("Device did not connect (last_err=%s)" % repr(last_err))

    # Login
    if not online_app.is_logged_in:
        OnlineChangeOption = globals().get("OnlineChangeOption", None)
        if OnlineChangeOption is None:
            raise Exception("OnlineChangeOption missing")
        print("Online: login (Keep)")
        online_app.login(OnlineChangeOption.Keep, False)
        if not online_app.is_logged_in:
            raise Exception("Login failed")

    return online_app, dev


def _disconnect_best_effort(online_app, dev):
    try:
        if hasattr(dev, "connected") and dev.connected and hasattr(dev, "disconnect"):
            dev.disconnect()
    except:
        pass
    try:
        if hasattr(online_app, "logout"):
            online_app.logout()
    except:
        pass


# -------------------------
# Deploy logic
# -------------------------
def _list_methods(obj, label):
    try:
        names = []
        for n in dir(obj):
            ln = n.lower()
            if (
                "download" in ln
                or "boot" in ln
                or "create" in ln
                or "start" in ln
                or "stop" in ln
                or "reset" in ln
                or "restart" in ln
                or "run" in ln
                or "application" in ln
            ):
                names.append(n)
        print(label, "type:", type(obj))
        print(label, "methods containing download/boot/create/start/stop/reset/restart/run/application:")
        for n in sorted(names):
            print(" -", n)
    except Exception as e:
        print(label, "method listing failed:", repr(e))


def _start_after_boot(online_app):
    """
    After create_boot_application(), many runtimes end up STOPPED.
    We try a few common "start/run" methods, and optionally reset/restart.
    """
    # state (if available)
    try:
        if hasattr(online_app, "application_state"):
            print("Application state (before):", online_app.application_state)
    except Exception as e:
        print("WARN: reading application_state failed:", repr(e))

    # Try start/run methods first
    started = False
    for m in ["start", "run", "start_application"]:
        if hasattr(online_app, m):
            try:
                getattr(online_app, m)()
                print("DEPLOY: called online_app.%s()" % m)
                started = True
                break
            except Exception as e:
                print("DEPLOY: online_app.%s() failed:" % m, repr(e))

    # If start didn't work, try reset/restart then start again
    if not started:
        for m in ["reset", "restart"]:
            if hasattr(online_app, m):
                try:
                    getattr(online_app, m)()
                    print("DEPLOY: called online_app.%s()" % m)
                except Exception as e:
                    print("DEPLOY: online_app.%s() failed:" % m, repr(e))

        for m in ["start", "run", "start_application"]:
            if hasattr(online_app, m):
                try:
                    getattr(online_app, m)()
                    print("DEPLOY: called online_app.%s() (post reset/restart)" % m)
                    started = True
                    break
                except Exception as e:
                    print("DEPLOY: online_app.%s() failed (post reset/restart):" % m, repr(e))

    # state (if available)
    try:
        if hasattr(online_app, "application_state"):
            print("Application state (after):", online_app.application_state)
    except Exception as e:
        print("WARN: reading application_state failed:", repr(e))

    if not started:
        print("WARNING: No supported start/run method worked (application may remain stopped).")


def _deploy_via_boot_application(online_app):
    """
    Your environment supports create_boot_application() and not the usual download APIs.
    """
    _list_methods(online_app, "OnlineApp")

    if not hasattr(online_app, "create_boot_application"):
        return False, "create_boot_application not available"

    try:
        online_app.create_boot_application()
        print("DEPLOY: SUCCESS via OnlineApp.create_boot_application()")

        # REQUIRED for your runtime: start the application after boot is created
        _start_after_boot(online_app)

        return True, "OnlineApp.create_boot_application() + start"
    except Exception as e:
        return False, "create_boot_application failed: %s" % repr(e)


# -------------------------
# Main
# -------------------------
def main():
    if len(sys.argv) < 2:
        print("ERROR: Missing STG project path")
        print('--scriptargs:"C:\\Users\\Test_bench\\Documents\\PLC_STG.project"')
        system.exit()

    project_path = sys.argv[1].strip().strip('"')
    print("STG project:", project_path)

    if not _git_checkout_and_update(BRANCH):
        print("ERROR: git checkout/pull failed")
        system.exit()

    user = os.environ.get("CODESYS_USER", "")
    pw = os.environ.get("CODESYS_PASS", "")

    proj = _open_project_primary(project_path)
    app = _wait_active_app(proj)
    if app is None:
        print("ERROR: active_application timeout")
        system.exit()

    online_app, dev = _connect_and_login(app, user, pw)

    try:
        ok, used = _deploy_via_boot_application(online_app)
        if not ok:
            print("ERROR: deploy failed:", used)
            system.exit()
        print("DEPLOY OK:", used)
    finally:
        _disconnect_best_effort(online_app, dev)

    try:
        system.exit()
    except:
        pass


try:
    main()
except Exception as e:
    print("FATAL:", repr(e))
    traceback.print_exc()
    try:
        system.exit()
    except:
        pass