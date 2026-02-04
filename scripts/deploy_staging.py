# encoding: utf-8
# ============================================================
# deploy_staging.py  (STG: PLCopen -> STG Project -> Download)
#
# Workflow:
#   1) Open STG project (PLC_STG.project)
#   2) Delete the Device node (required or import can fail)
#   3) Import PLCopen XML: exports\plcopen\PLC_latest.plcopen.xml
#   4) Connect/login
#   5) Download (full)
#   6) Create boot application
#   7) Start ONLY if not already RUN
#
# Run:
# "C:\Program Files\CODESYS 3.5.21.40\CODESYS\Common\CODESYS.exe" --noUI --profile="CODESYS V3.5 SP21 Patch 4" --runscript="C:\PLC_REPO\scripts\deploy_staging.py" --scriptargs:"C:\Users\Test_bench\Documents\PLC_STG.project"
# ============================================================

import os
import sys
import time
import subprocess
import traceback

REPO_ROOT = r"C:\PLC_REPO"
TIMEOUT_S = 180
BRANCH = "staging"

PLCOPEN_PATH = os.path.join(REPO_ROOT, "exports", "plcopen", "PLC_latest.plcopen.xml")


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


<<<<<<< HEAD
<<<<<<< HEAD
=======
>>>>>>> dev
def _open_project_primary(project_path):
    _close_projects_best_effort()
    proj = projects.primary
    if proj is None:
<<<<<<< HEAD
        proj = projects.open(project_path)
        print("proj type:", type(proj))
        print("projects.primary:", projects.primary)
        print("proj.active_application:", getattr(proj, "active_application", None))
        print("project children:", len(proj.get_children(True)) if hasattr(proj, "get_children") else "n/a")

    return proj


=======
>>>>>>> dev
=======
        # some installs accept primary=True, some don’t; try both
        try:
            proj = projects.open(project_path, primary=True)
        except:
            proj = projects.open(project_path)
    return proj


>>>>>>> dev
def _wait_active_app(proj):
    start = time.time()
    while (time.time() - start) < TIMEOUT_S:
        try:
            if hasattr(proj, "active_application"):
                app = proj.active_application
                if app is not None:
                    return app
        except:
            pass
        time.sleep(1)
    return None


def _iter_tree(root):
    """
    Depth-first traverse of project objects (best effort).
    """
    stack = [root]
    seen = set()
    while stack:
        obj = stack.pop()
        if obj is None:
            continue
        oid = None
        try:
            oid = str(obj)  # not stable but avoids infinite loops
        except:
            oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)

        yield obj

        try:
            if hasattr(obj, "get_children"):
                kids = obj.get_children(True)
                if kids:
                    # reverse to keep order-ish
                    for k in list(kids)[::-1]:
                        stack.append(k)
        except:
            pass


def _obj_name(obj):
    for attr in ["name", "Name"]:
        try:
            v = getattr(obj, attr)
            if isinstance(v, basestring):  # python2 in ScriptLib often
                return v
        except:
            pass
    try:
        if hasattr(obj, "get_name"):
            return obj.get_name()
    except:
        pass
    try:
        return str(obj)
    except:
        return "<obj>"


def _looks_like_device(obj):
    """
    Heuristic: in CODESYS, the top device node is usually named 'Device'
    and/or object string contains 'Device('.
    """
    n = _obj_name(obj)
    s = ""
    try:
        s = str(obj)
    except:
        pass
    n_low = (n or "").lower()
    s_low = (s or "").lower()
    if n_low == "device":
        return True
    if "device(" in s_low:
        return True
    if "scriptdevice" in s_low:
        return True
    return False


def _delete_object(obj):
    """
    Try common delete/remove methods.
    """
    for m in ["remove", "delete", "Delete", "Remove"]:
        try:
            if hasattr(obj, m):
                getattr(obj, m)()
                return True, "obj.%s()" % m
        except Exception as e:
            last = repr(e)
    return False, "no supported delete/remove method"


def _delete_device_node(proj):
    """
    Delete the first object that looks like the Device node.
    """
    print("STG: searching for Device node to delete...")
    for obj in _iter_tree(proj):
        try:
            if _looks_like_device(obj):
                nm = _obj_name(obj)
                print("STG: deleting device-like object:", nm)
                ok, how = _delete_object(obj)
                if ok:
                    print("STG: deleted via", how)
                    return True
                else:
                    print("STG: found device but could not delete:", how)
                    return False
        except:
            pass
    print("STG: no Device node found (nothing deleted).")
    return True  # not fatal; sometimes project already has no device


# -------------------------
# PLCopen import
# -------------------------
class ImportReporter(object):
    """
    Must be tolerant: CODESYS calls reporter.error/warning with varying signatures
    and sometimes expects properties like .skipped.
    """
    def __init__(self):
        self._skipped = []
        self._warnings = []
        self._errors = []

    # accept any signature
    def error(self, *args):
        self._errors.append(args)
        try:
            print("PLCOPEN import ERROR:", args)
        except:
            pass

    def warning(self, *args):
        self._warnings.append(args)
        try:
            print("PLCOPEN import WARNING:", args)
        except:
            pass

    def info(self, *args):
        try:
            print("PLCOPEN import INFO:", args)
        except:
            pass

    def nonimportable(self, *args):
        self._skipped.append(args)
        try:
            print("PLCOPEN non-importable:", args)
        except:
            pass

    @property
    def aborting(self):
        return False

    # some installs try to read reporter.skipped
    @property
    def skipped(self):
        return self._skipped


def _try_import_xml(target, reporter, xml_path):
    """
    Try common import_xml signatures (no kwargs).
    """
    tries = [
        ("import_xml(reporter, path)", lambda: target.import_xml(reporter, xml_path)),
        ("import_xml(reporter, path, True)", lambda: target.import_xml(reporter, xml_path, True)),
        ("import_xml(reporter, path, False)", lambda: target.import_xml(reporter, xml_path, False)),
    ]

    last_err = None
    for label, fn in tries:
        if not hasattr(target, "import_xml"):
            return False, "target has no import_xml"
        try:
            fn()
            return True, label
        except Exception as e:
            last_err = repr(e)
            print("PLCOPEN import attempt failed:", label, "->", last_err)

    return False, "no working import_xml signature (last_err=%s)" % last_err


def _import_plcopen_into_project(proj, xml_path):
    if not os.path.isfile(xml_path):
        raise Exception("PLCopen file not found: %s" % xml_path)

    print("PLCOPEN: using:", xml_path)

    reporter = ImportReporter()

    # Prefer project-level import (more common)
    ok, used = _try_import_xml(proj, reporter, xml_path)
    if ok:
        print("PLCOPEN: import OK via project:", used)
        return True

    # Fallback: try active application object if available
    try:
        app = getattr(proj, "active_application", None)
        if app is not None:
            ok2, used2 = _try_import_xml(app, reporter, xml_path)
            if ok2:
                print("PLCOPEN: import OK via application:", used2)
                return True
    except:
        pass

    raise Exception("PLCopen import failed: %s" % used)


# -------------------------
# Online connect/login + download + boot
# -------------------------
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


def _try_download_full(online_app):
    OnlineChangeOption = globals().get("OnlineChangeOption", None)
    if OnlineChangeOption is None:
        return False, "OnlineChangeOption missing"

    # common method names
    method_names = ["download", "application_download", "program_download"]
    opt_names = ["Download", "FullDownload", "All", "Keep"]

    last_err = None

    for m in method_names:
        if not hasattr(online_app, m):
            continue
        fn = getattr(online_app, m)

        # try options
        for opt_name in opt_names:
            if hasattr(OnlineChangeOption, opt_name):
                opt = getattr(OnlineChangeOption, opt_name)
                try:
                    fn(opt, False)
                    return True, "online_app.%s(%s)" % (m, opt_name)
                except Exception as e:
                    last_err = repr(e)

        # try no-arg
        try:
            fn()
            return True, "online_app.%s()" % m
        except Exception as e:
            last_err = repr(e)

    return False, "no working download method (last_err=%s)" % last_err


def _start_if_needed(online_app):
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
            return True
        except Exception as e:
            print("DEPLOY: online_app.start() failed:", repr(e))

    return True


def _deploy_boot_app(online_app):
    if hasattr(online_app, "create_boot_application"):
        online_app.create_boot_application()
        print("DEPLOY: create_boot_application OK")
        _start_if_needed(online_app)
        return True, "create_boot_application"
    return False, "create_boot_application not available"


# -------------------------
# Main
# -------------------------
def main():
    if len(sys.argv) < 2:
        print("ERROR: Missing STG project path")
        print('--scriptargs:"C:\\Users\\Test_bench\\Documents\\PLC_STG.project"')
        system.exit()

    stg_project_path = sys.argv[1].strip().strip('"')
    print("STG project:", stg_project_path)

    if not _git_checkout_and_update(BRANCH):
        print("ERROR: git checkout/pull failed")
        system.exit()

    if not os.path.isfile(PLCOPEN_PATH):
        print("ERROR: PLCopen missing:", PLCOPEN_PATH)
        system.exit()

    user = os.environ.get("CODESYS_USER", "")
    pw = os.environ.get("CODESYS_PASS", "")

    # 1) Open STG project
    proj = _open_project_primary(stg_project_path)

    # 2) Delete Device (required for your import)
    ok_del = _delete_device_node(proj)
    if not ok_del:
        print("ERROR: Could not delete Device node (import will likely fail).")
        system.exit()

    # 3) Import PLCopen
    try:
        _import_plcopen_into_project(proj, PLCOPEN_PATH)
    except Exception as e:
        print("ERROR:", repr(e))
        traceback.print_exc()
        system.exit()

    # 4) Get active app
    app = _wait_active_app(proj)
    if app is None:
        print("ERROR: active_application timeout after import")
        system.exit()

    # 5) Connect/login and Download + Boot
    online_app, dev = _connect_and_login(app, user, pw)
    try:
        ok_dl, used_dl = _try_download_full(online_app)
        if not ok_dl:
            print("ERROR: download failed:", used_dl)
            system.exit()
        print("DEPLOY: download OK via", used_dl)

        ok_boot, used_boot = _deploy_boot_app(online_app)
        if not ok_boot:
            print("ERROR: boot step failed:", used_boot)
            system.exit()

        print("DEPLOY OK:", used_dl, "+", used_boot)

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
