# encoding: utf-8
# DEPLOY STAGING (ARCHIVE): Git staging -> use PLC_latest.projectarchive -> download to PLC -> boot app (+start if needed)
#
# Run:
# "C:\Program Files\CODESYS 3.5.21.40\CODESYS\Common\CODESYS.exe" --noUI --profile="CODESYS V3.5 SP21 Patch 4" --runscript="C:\PLC_REPO\scripts\deploy_staging.py" --scriptargs:"C:\Users\Test_bench\Documents\PLC_STG.project"

import os
import sys
import time
import subprocess
import traceback

REPO_ROOT = r"C:\PLC_REPO"
TIMEOUT_S = 180
BRANCH = "staging"

LATEST_ARCHIVE = os.path.join(REPO_ROOT, "exports", "archives", "PLC_latest.projectarchive")


# -------------------------
# Git
# -------------------------
def _run_git(args):
    p = subprocess.Popen(
        ["git"] + args,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False
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
# CODESYS: project + online
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


def _wait_active_app(proj):
    start = time.time()
    last_seen = None
    while (time.time() - start) < TIMEOUT_S:
        try:
            if hasattr(proj, "active_application"):
                last_seen = proj.active_application
                if last_seen is not None:
                    return last_seen
        except:
            pass
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

    # connect (retry)
    last_err = None
    for attempt in [1, 2, 3]:
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

    # login
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


def _start_if_needed(online_app):
    # only start if not already running
    try:
        if hasattr(online_app, "application_state"):
            st = online_app.application_state
            print("Application state (before):", st)
            if str(st).lower().endswith(".run"):
                print("Application already RUNNING. No start needed.")
                return True
    except:
        pass

    # try start
    if hasattr(online_app, "start"):
        try:
            online_app.start()
            print("DEPLOY: called online_app.start()")
            return True
        except Exception as e:
            print("DEPLOY: online_app.start() failed:", repr(e))

    # try reset as last resort
    if hasattr(online_app, "reset"):
        try:
            online_app.reset()
            print("DEPLOY: called online_app.reset()")
        except:
            pass

    return True


def _deploy_boot_app(online_app):
    if hasattr(online_app, "create_boot_application"):
        online_app.create_boot_application()
        print("DEPLOY: create_boot_application OK")
        _start_if_needed(online_app)
        return True, "create_boot_application"
    return False, "create_boot_application not available"


# -------------------------
# ARCHIVE open logic
# -------------------------
def _open_archive_project_best_effort(archive_path):
    if not os.path.isfile(archive_path):
        raise Exception("Latest archive not found: %s" % archive_path)

    print("ARCHIVE: using:", archive_path)

    _close_projects_best_effort()

    # 1) Try projects.open_archive(path) with NO kwargs (your install rejects primary=)
    if hasattr(projects, "open_archive"):
        try:
            proj = projects.open_archive(archive_path)
            print("ARCHIVE: opened via projects.open_archive(path)")
            return proj
        except Exception as e:
            print("ARCHIVE: open_archive(path) failed:", repr(e))

    # 2) Try projects.open(path) (works for you)
    try:
        proj = projects.open(archive_path)
        print("ARCHIVE: opened via projects.open(path)")
        return proj
    except Exception as e:
        print("ARCHIVE: projects.open(path) failed:", repr(e))

    raise Exception("Could not open archive with available API calls")


# -------------------------
# Main
# -------------------------
def main():
    if len(sys.argv) < 2:
        print("ERROR: Missing STG project path (reference)")
        system.exit()

    stg_project_path = sys.argv[1].strip().strip('"')
    print("STG project (reference):", stg_project_path)

    if not _git_checkout_and_update(BRANCH):
        print("ERROR: git checkout/pull failed")
        system.exit()

    if not os.path.isfile(LATEST_ARCHIVE):
        print("ERROR: latest archive missing:", LATEST_ARCHIVE)
        print("Tip: DEV capture must write exports\\archives\\PLC_latest.projectarchive")
        system.exit()

    user = os.environ.get("CODESYS_USER", "")
    pw = os.environ.get("CODESYS_PASS", "")

    # Try opening archive as a project
    proj = _open_archive_project_best_effort(LATEST_ARCHIVE)
    app = _wait_active_app(proj)

    # Fallback: if archive-open didn't create active_application, use reference project instead
    if app is None:
        print("WARNING: archive project has no active_application (headless limitation).")
        print("FALLBACK: opening STG reference project and deploying boot app from there.")
        _close_projects_best_effort()
        proj = projects.open(stg_project_path)
        app = _wait_active_app(proj)

    if app is None:
        print("ERROR: active_application timeout (both archive + reference project).")
        system.exit()

    online_app, dev = _connect_and_login(app, user, pw)
    try:
        ok, used = _deploy_boot_app(online_app)
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