# encoding: utf-8
# DEPLOY STAGING: Git (staging branch) -> import PLCopen XML -> build -> boot app -> start if needed
#
# Usage:
# --runscript="C:\PLC_REPO\scripts\deploy_staging.py" --scriptargs:"C:\Users\Test_bench\Documents\PLC_STG.project"

import os
import sys
import time
import subprocess
import traceback
import hashlib

# Reduce noisy CODESYS embedded-python warning threads
try:
    import warnings
    warnings.filterwarnings("ignore")
except:
    pass

REPO_ROOT = r"C:\PLC_REPO"
TIMEOUT_S = 120
BRANCH = "staging"

# Your single “latest” file (committed to Git)
PLCOPEN_XML = os.path.join(REPO_ROOT, "exports", "plcopen", "PLC_latest.plcopen.xml")


# -------------------------
# Small utilities
# -------------------------
def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def _list_methods(obj, label, contains=None):
    try:
        names = []
        for n in dir(obj):
            ln = n.lower()
            if contains is None:
                names.append(n)
            else:
                for c in contains:
                    if c in ln:
                        names.append(n)
                        break
        print(label, "type:", type(obj))
        print(label, "methods matching:", contains)
        for n in sorted(set(names)):
            print(" -", n)
    except Exception as e:
        print("WARN:", label, "method listing failed:", repr(e))


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
        proj = projects.open(project_path)
        print("proj type:", type(proj))
        print("projects.primary:", projects.primary)
        print("proj.active_application:", getattr(proj, "active_application", None))
        print("project children:", len(proj.get_children(True)) if hasattr(proj, "get_children") else "n/a")

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
# PLCopen import (critical part)
# -------------------------
def _import_plcopen_into_project(proj, app, xml_path):
    """
    Import PLCopen XML into the opened project BEFORE deploying.
    CODESYS scripting APIs vary, so we try several import entry points.

    Returns (ok: bool, used: str)
    """
    if not os.path.isfile(xml_path):
        return False, "PLCopen XML not found: %s" % xml_path

    size = os.path.getsize(xml_path)
    sha = _sha256(xml_path)
    print("PLCOPEN: using:", xml_path)
    print("PLCOPEN: size=%s sha256=%s" % (size, sha))

    # Show relevant methods for quick debugging
    _list_methods(app, "ActiveApplication", contains=["import", "xml"])
    _list_methods(proj, "Project", contains=["import", "xml"])
    _list_methods(projects, "projects module", contains=["import", "xml"])

    last_err = None

    # Helper: best effort save after import
    def _save_best_effort():
        try:
            if hasattr(proj, "save"):
                proj.save()
                print("Project saved.")
        except Exception as e:
            print("WARN: project save failed:", repr(e))

    # Attempt 1: app.import_xml(xml_path, ...)
    if hasattr(app, "import_xml"):
        try:
            # Some versions: import_xml(path, recursive=True)
            try:
                app.import_xml(xml_path, recursive=True)
                _save_best_effort()
                return True, "app.import_xml(path, recursive=True)"
            except TypeError:
                pass

            # Some versions: import_xml(path)
            app.import_xml(xml_path)
            _save_best_effort()
            return True, "app.import_xml(path)"
        except Exception as e:
            last_err = e
            print("PLCOPEN import attempt app.import_xml failed:", repr(e))

    # Attempt 2: proj.import_xml(...)
    if hasattr(proj, "import_xml"):
        try:
            # Some versions: proj.import_xml(path, recursive=True)
            try:
                proj.import_xml(xml_path, recursive=True)
                _save_best_effort()
                return True, "proj.import_xml(path, recursive=True)"
            except TypeError:
                pass

            proj.import_xml(xml_path)
            _save_best_effort()
            return True, "proj.import_xml(path)"
        except Exception as e:
            last_err = e
            print("PLCOPEN import attempt proj.import_xml failed:", repr(e))

    # Attempt 3: projects.import_xml(...)
    if hasattr(projects, "import_xml"):
        try:
            projects.import_xml(xml_path)
            _save_best_effort()
            return True, "projects.import_xml(path)"
        except Exception as e:
            last_err = e
            print("PLCOPEN import attempt projects.import_xml failed:", repr(e))

    return False, "No working PLCopen import method (last_err=%s)" % repr(last_err)


# -------------------------
# Deploy via boot application + start if needed
# -------------------------
def _start_if_needed(online_app):
    state = None
    try:
        if hasattr(online_app, "application_state"):
            state = online_app.application_state
            print("Application state (before):", state)
    except Exception as e:
        print("WARN: reading application_state failed:", repr(e))

    # If already running, don't start
    try:
        if state is not None and str(state).lower().endswith(".run"):
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

    try:
        if hasattr(online_app, "application_state"):
            print("Application state (after):", online_app.application_state)
    except Exception as e:
        print("WARN: reading application_state failed:", repr(e))

    return True


def _deploy_via_boot_application(online_app):
    if not hasattr(online_app, "create_boot_application"):
        return False, "create_boot_application not available"

    try:
        online_app.create_boot_application()
        print("DEPLOY: SUCCESS via OnlineApp.create_boot_application()")
        _start_if_needed(online_app)
        return True, "OnlineApp.create_boot_application()"
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

    # 1) Update local repo to latest staging
    if not _git_checkout_and_update(BRANCH):
        print("ERROR: git checkout/pull failed")
        system.exit()

    # 2) Ensure PLCopen XML exists in this branch
    if not os.path.isfile(PLCOPEN_XML):
        print("ERROR: PLCopen XML not found:", PLCOPEN_XML)
        print("Tip: make sure PLC_latest.plcopen.xml is committed and merged into staging.")
        system.exit()

    # 3) Open project
    user = os.environ.get("CODESYS_USER", "")
    pw = os.environ.get("CODESYS_PASS", "")

    proj = _open_project_primary(project_path)
    app = _wait_active_app(proj)
    if app is None:
        print("ERROR: active_application timeout")
        system.exit()

    # 4) IMPORT PLCopen into the STG project (this is the missing step)
    ok_imp, used_imp = _import_plcopen_into_project(proj, app, PLCOPEN_XML)
    if not ok_imp:
        print("ERROR: PLCopen import failed:", used_imp)
        system.exit()
    print("PLCOPEN import OK:", used_imp)

    # 5) Connect + deploy
    online_app, dev = _connect_and_login(app, user, pw)
    try:
        ok_dep, used_dep = _deploy_via_boot_application(online_app)
        if not ok_dep:
            print("ERROR: deploy failed:", used_dep)
            system.exit()
        print("DEPLOY OK:", used_dep)
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