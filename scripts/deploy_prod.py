# encoding: utf-8
# ============================================================
# deploy_production.py (PRODUCTION: Git + PLCopen -> PLC)  [IMPORT + SOURCE + SAVE]
#
# Structure (per your screenshot):
#   Device
#     └─ PLC Logic
#         └─ Application
#
# Workflow:
#   1) Git: checkout production + pull
#   2) Open PROD reference project (contains configured device)
#   3) Delete ONLY Application under PLC Logic
#   4) Import PLCopen XML into PLC Logic
#   5) Save project (persist imported objects)
#   6) Online connect + login
#   7) Source download (so the project contains source)
#   8) Create boot application (+ start if needed)
#   9) Save project again
#
# Run:
# "C:\Program Files\CODESYS 3.5.21.40\CODESYS\Common\CODESYS.exe" --noUI ^
#   --profile="CODESYS V3.5 SP21 Patch 4" ^
#   --runscript="C:\PLC_REPO\scripts\deploy_production.py" ^
#   --scriptargs:"C:\Users\Test_bench\Documents\PLC_PROD.project"
# ============================================================

import os
import sys
import time
import subprocess
import traceback

REPO_ROOT = r"C:\PLC_REPO"
TIMEOUT_S = 180
BRANCH = "prod"

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


def _open_project(project_path):
    _close_projects_best_effort()
    return projects.open(project_path)


def _iter_children(obj, recursive=False):
    try:
        if hasattr(obj, "get_children"):
            for c in obj.get_children(recursive):
                yield c
    except:
        return


def _obj_name(obj):
    for attr in ("name", "Name", "get_name"):
        try:
            if hasattr(obj, attr):
                v = getattr(obj, attr)
                return v() if callable(v) else v
        except:
            pass
    try:
        return str(obj)
    except:
        return "<unknown>"


def _find_node_by_name(proj_or_parent, name, recursive=True):
    target = (name or "").strip().lower()
    for obj in _iter_children(proj_or_parent, recursive=recursive):
        nm = (_obj_name(obj) or "").strip().lower()
        if nm == target:
            return obj
    return None


def _find_child_exact(parent, name):
    return _find_node_by_name(parent, name, recursive=False)


def _dump_tree_one_level(proj):
    print("PROD: top-level nodes:")
    for c in _iter_children(proj, recursive=False):
        print(" -", _obj_name(c))


def _get_device_and_plclogic(proj):
    print("PROD: searching for Device node...")
    dev = _find_node_by_name(proj, "Device", recursive=True)
    if dev is None:
        _dump_tree_one_level(proj)
        raise Exception("PROD: Device node not found in PROD project. (project must contain configured device)")

    print("PROD: found device:", _obj_name(dev))

    plc_logic = _find_child_exact(dev, "PLC Logic")
    if plc_logic is None:
        # handle case-insensitive variations like "Plc Logic"
        for c in _iter_children(dev, recursive=False):
            nm = (_obj_name(c) or "").strip().lower()
            if "plc" in nm and "logic" in nm:
                plc_logic = c
                break

    if plc_logic is None:
        print("PROD: children under Device:")
        for c in _iter_children(dev, recursive=False):
            print(" -", _obj_name(c))
        raise Exception("PROD: 'PLC Logic' not found under Device")

    print("PROD: found plc logic:", _obj_name(plc_logic))
    return dev, plc_logic


def _delete_application_under_plclogic(plc_logic):
    app = _find_child_exact(plc_logic, "Application")
    if app is None:
        print("PROD: No Application found under PLC Logic (already removed?)")
        return

    print("PROD: deleting Application under PLC Logic...")
    if hasattr(app, "remove"):
        app.remove()
        print("PROD: Application deleted via app.remove()")
        return

    # best-effort fallbacks
    if hasattr(plc_logic, "remove"):
        try:
            plc_logic.remove(app)
            print("PROD: Application deleted via plc_logic.remove(app)")
            return
        except:
            pass

    raise Exception("PROD: Could not delete Application (no supported remove method found)")


def _save_project_best_effort(proj):
    # CODESYS versions differ; try common patterns
    try:
        if hasattr(proj, "save"):
            proj.save()
            print("PROD: project saved via proj.save()")
            return True
    except Exception as e:
        print("WARN: proj.save() failed:", repr(e))

    print("WARN: could not save project (no compatible save method found)")
    return False


# -------------------------
# PLCopen import reporter
# -------------------------
def _make_import_reporter():
    if "ImportReporter" not in globals():
        return None, "ImportReporter not available in globals()"

    Base = globals().get("ImportReporter")

    class IR(Base):
        def error(self, *args):
            print("PLCOPEN import ERROR:", args)

        def warning(self, *args):
            print("PLCOPEN import WARNING:", args)

        def info(self, *args):
            print("PLCOPEN import INFO:", args)

        def added(self, *args):
            print("PLCOPEN import ADDED:", args)

        def replaced(self, *args):
            print("PLCOPEN import REPLACED:", args)

        def skipped(self, *args):
            print("PLCOPEN import SKIPPED:", args)

        def nonimportable(self, *args):
            print("PLCOPEN import NONIMPORTABLE:", args)

        @property
        def aborting(self):
            return False

    try:
        return IR(), "subclassed ImportReporter"
    except Exception as e:
        return None, "failed to construct reporter: %s" % repr(e)


def _import_plcopen_into_plclogic(plc_logic, plcopen_path):
    if not os.path.isfile(plcopen_path):
        return False, "PLCOPEN file not found: %s" % plcopen_path

    if not hasattr(plc_logic, "import_xml"):
        return False, "PLC Logic has no import_xml method"

    reporter, rep_note = _make_import_reporter()
    print("ImportReporter:", rep_note)

    fn = getattr(plc_logic, "import_xml")
    last_err = None

    if reporter is not None:
        for label, args in [
            ("import_xml(reporter, path)", (reporter, plcopen_path)),
            ("import_xml(reporter, path, True)", (reporter, plcopen_path, True)),
            ("import_xml(reporter, path, False)", (reporter, plcopen_path, False)),
        ]:
            try:
                fn(*args)
                return True, label
            except Exception as e:
                last_err = e
                print("PLCOPEN import attempt failed:", label, "->", repr(e))

    try:
        fn(plcopen_path)
        return True, "import_xml(path)"
    except Exception as e:
        last_err = e
        print("PLCOPEN import attempt failed: import_xml(path) ->", repr(e))

    return False, "no working import_xml signature (last_err=%s)" % repr(last_err)


# -------------------------
# Online / deploy
# -------------------------
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


def _source_download_best_effort(online_app):
    if hasattr(online_app, "source_download"):
        try:
            print("PROD: source_download...")
            online_app.source_download()
            print("PROD: source_download OK")
            return True
        except Exception as e:
            print("WARN: source_download failed:", repr(e))
            return False

    print("WARN: online_app.source_download not available on this runtime")
    return False


def _start_if_needed(online_app):
    try:
        st = getattr(online_app, "application_state", None)
        print("Application state:", st)
        if st is not None and str(st).lower().endswith(".run"):
            print("Already RUN; not starting.")
            return True
    except:
        pass

    if hasattr(online_app, "start"):
        try:
            online_app.start()
            print("DEPLOY: start OK")
            return True
        except Exception as e:
            print("DEPLOY: start failed:", repr(e))

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
        print("ERROR: Missing PROD project path")
        system.exit()

    prod_project_path = sys.argv[1].strip().strip('"')
    print("PROD project:", prod_project_path)
    print("PLCOPEN: using:", PLCOPEN_PATH)

    if not _git_checkout_and_update(BRANCH):
        print("ERROR: git checkout/pull failed")
        system.exit()

    if not os.path.isfile(PLCOPEN_PATH):
        print("ERROR: PLCopen missing:", PLCOPEN_PATH)
        system.exit()

    # 1) Open project
    proj = _open_project(prod_project_path)

    # 2) Find Device -> PLC Logic
    dev, plc_logic = _get_device_and_plclogic(proj)

    # 3) Delete Application under PLC Logic
    _delete_application_under_plclogic(plc_logic)

    # 4) Import PLCopen into PLC Logic
    ok, used = _import_plcopen_into_plclogic(plc_logic, PLCOPEN_PATH)
    if not ok:
        raise Exception("PLCopen import failed: %s" % used)
    print("PLCOPEN: import OK via", used)

    # 5) Save project after import
    _save_project_best_effort(proj)

    # 6) Get active app
    app = _wait_active_app(proj)
    if app is None:
        raise Exception("active_application timeout after PLCopen import")

    # 7) Online connect + login
    user = os.environ.get("CODESYS_USER", "")
    pw = os.environ.get("CODESYS_PASS", "")
    online_app, dev_online = _connect_and_login(app, user, pw)

    try:
        # 8) Source download (so project contains source)
        _source_download_best_effort(online_app)

        # 9) Create boot app (+start)
        ok, used = _deploy_boot_app(online_app)
        if not ok:
            raise Exception("DEPLOY failed: %s" % used)
        print("DEPLOY OK:", used)

    finally:
        _disconnect_best_effort(online_app, dev_online)

    # 10) Save project again (persist anything updated after source download)
    _save_project_best_effort(proj)

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
