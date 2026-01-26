# encoding: utf-8
# DEPLOY STAGING (ARCHIVE): Git staging -> open PLC_latest.projectarchive -> download to PLC -> boot app
#
# Usage:
# "C:\Program Files\CODESYS 3.5.21.40\CODESYS\Common\CODESYS.exe" --noUI --profile="CODESYS V3.5 SP21 Patch 4" --runscript="C:\PLC_REPO\scripts\deploy_staging.py" --scriptargs:"C:\Users\Test_bench\Documents\PLC_STG.project"
#
# NOTE:
# - The .project path is only used to get a configured target/device entry if needed.
# - The actual content deployed comes from: exports\archives\PLC_latest.projectarchive

import os
import sys
import time
import subprocess
import traceback

REPO_ROOT = r"C:\PLC_REPO"
TIMEOUT_S = 120
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

    # connect
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
    # If already running, don't start
    try:
        if hasattr(online_app, "application_state"):
            st = online_app.application_state
            print("Application state (before):", st)
            if str(st).lower().endswith(".run"):
                print("Application already RUNNING. No start needed.")
                return True
    except:
        pass

    if hasattr(online_app, "start"):
        try:
            online_app.start()
            print("DEPLOY: called online_app.start()")
        except Exception as e:
            print("DEPLOY: online_app.start() failed:", repr(e))
    return True


def _deploy_boot_app(online_app):
    # Your runtime supports this (we already proved it)
    if hasattr(online_app, "create_boot_application"):
        online_app.create_boot_application()
        print("DEPLOY: create_boot_application OK")
        _start_if_needed(online_app)
        return True, "create_boot_application"
    return False, "create_boot_application not available"


# -------------------------
# ARCHIVE restore/open (best-effort)
# -------------------------
def _open_archive_as_project(archive_path):
    """
    CODESYS scripting differs by version.
    We try common patterns:
      - projects.open(archive_path, primary=True)  (sometimes works directly)
      - projects.open_archive(archive_path, primary=True) if available
    """
    if not os.path.isfile(archive_path):
        raise Exception("Latest archive not found: %s" % archive_path)

    print("ARCHIVE: using:", archive_path)

    # Try open_archive if it exists
    if hasattr(projects, "open_archive"):
        try:
            _close_projects_best_effort()
            proj = projects.open_archive(archive_path, primary=True)
            print("ARCHIVE: opened via projects.open_archive")
            return proj
        except Exception as e:
            print("ARCHIVE: open_archive failed:", repr(e))

    # Try opening directly
    try:
        _close_projects_best_effort()
        proj = projects.open(archive_path, primary=True)
        print("ARCHIVE: opened via projects.open(archive_path)")
        return proj
    except Exception as e:
        print("ARCHIVE: projects.open(archive_path) failed:", repr(e))

    raise Exception("Could not open archive via scripting API (need different method on this install)")


# -------------------------
# Main
# -------------------------
def main():
    if len(sys.argv) < 2:
        print("ERROR: Missing STG project path (used only for device config reference)")
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

    # Open the archive project (this is what we deploy)
    proj = _open_archive_as_project(LATEST_ARCHIVE)
    app = _wait_active_app(proj)
    if app is None:
        print("ERROR: active_application timeout after opening archive")
        system.exit()

    # Connect & deploy
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