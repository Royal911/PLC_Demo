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


def _open_project_primary(project_path):
    _close_projects_best_effort()
    proj = projects.primary
    if proj is None:
        try:
            proj = projects.open(project_path, primary=True)
        except:
            proj = projects.open(project_path)
    return proj


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
    stack = [root]
    seen = set()
    while stack:
        obj = stack.pop()
        if obj is None:
            continue
        try:
            oid = str(obj)
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
                    for k in list(kids)[::-1]:
                        stack.append(k)
        except:
            pass


def _obj_name(obj):
    for attr in ["name", "Name"]:
        try:
            v = getattr(obj, attr)
            if isinstance(v, basestring):
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
    for m in ["remove", "delete", "Delete", "Remove"]:
        try:
            if hasattr(obj, m):
                getattr(obj, m)()
                return True, "obj.%s()" % m
        except:
            pass
    return False, "no supported delete/remove method"


def _delete_device_node(proj):
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
                print("STG: found device but could not delete:", how)
                return False
        except:
            pass
    print("STG: no Device node found (nothing deleted).")
    return True


# -------------------------
# PLCopen import (IMPORTANT: must be IImportReporter)
# -------------------------
def _make_import_reporter():
    Base = globals().get("ImportReporter", None)
    if Base is None:
        raise Exception("CODESYS ImportReporter base class not found in globals().")

    class IR(Base):
        def __init__(self):
            try:
                Base.__init__(self)
            except:
                pass
            self._skipped = []

        # CODESYS varies: error(msg) OR error(obj, msg) OR error(obj, msg, detail)
        def error(self, *args):
            print("PLCOPEN import ERROR:", _fmt_reporter_args(args))

        def warning(self, *args):
            print("PLCOPEN import WARNING:", _fmt_reporter_args(args))

        def info(self, *args):
            print("PLCOPEN import INFO:", _fmt_reporter_args(args))

        # Sometimes called: nonimportable(obj) OR nonimportable(obj, msg)
        def nonimportable(self, *args):
            if args:
                self._skipped.append(args[0])
            print("PLCOPEN non-importable:", _fmt_reporter_args(args))

        @property
        def aborting(self):
            return False

        @property
        def skipped(self):
            return self._skipped

    return IR()


def _fmt_reporter_args(args):
    try:
        if not args:
            return "<no details>"
        # make it readable
        parts = []
        for a in args:
            try:
                parts.append(str(a))
            except:
                parts.append(repr(a))
        return " | ".join(parts)
    except:
        return repr(args)

    """
    Your error 'expected IImportReporter' means we must subclass the built-in ImportReporter
    exposed by CODESYS scripting, not a plain Python object.
    """
    Base = globals().get("ImportReporter", None)
    if Base is None:
        raise Exception("CODESYS ImportReporter base class not found in globals().")

    class IR(Base):
        def __init__(self):
            try:
                Base.__init__(self)
            except:
                pass
            self._skipped = []

        # Most installs use (obj, message)
        def error(self, obj, message):
            print("PLCOPEN import ERROR:", message)

        def warning(self, obj, message):
            print("PLCOPEN import WARNING:", message)

        def info(self, obj, message):
            print("PLCOPEN import INFO:", message)

        # Some installs call nonimportable(obj)
        def nonimportable(self, obj):
            self._skipped.append(obj)
            print("PLCOPEN non-importable:", obj)

        @property
        def aborting(self):
            return False

        # Some installs try to read reporter.skipped
        @property
        def skipped(self):
            return self._skipped

    return IR()


def _try_import_xml(target, reporter, xml_path):
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
    reporter = _make_import_reporter()
    print("Has ImportReporter?", "ImportReporter" in globals())
    print("Globals containing 'report':", [k for k in globals().keys() if "report" in k.lower()])

    ok, used = _try_import_xml(proj, reporter, xml_path)
    if ok:
        print("PLCOPEN: import OK via project:", used)
        return True

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

    method_names = ["download", "application_download", "program_download"]
    opt_names = ["Download", "FullDownload", "All", "Keep"]

    last_err = None

    for m in method_names:
        if not hasattr(online_app, m):
            continue
        fn = getattr(online_app, m)

        for opt_name in opt_names:
            if hasattr(OnlineChangeOption, opt_name):
                opt = getattr(OnlineChangeOption, opt_name)
                try:
                    fn(opt, False)
                    return True, "online_app.%s(%s)" % (m, opt_name)
                except Exception as e:
                    last_err = repr(e)

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

    # 2) Delete Device
    ok_del = _delete_device_node(proj)
    if not ok_del:
        print("ERROR: Could not delete Device node.")
        system.exit()

    # 3) Import PLCopen
    _import_plcopen_into_project(proj, PLCOPEN_PATH)

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
