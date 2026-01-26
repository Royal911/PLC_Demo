# encoding: utf-8
# ============================================================
# grab_archive.py  (ONE-RUN / MULTI-BRANCH PLC GitOps AGENT)
#
# DEV capture:
#   - pulls source from PLC
#   - exports PLCopen to ONE file (overwritten): exports\plcopen\PLC_latest.plcopen.xml
#   - saves archive to exports\archives\dev\...
#   - if repo changed -> commit EVERYTHING (git add -A) + push to origin/dev
#
# STAGING / PROD deploy:
#   - if origin/<branch> differs -> ff-only pull -> download to controller
#
# Run example:
#   --runscript="C:\PLC_REPO\scripts\grab_archive.py" --scriptargs:"C:\Users\Test_bench\Documents"
# ============================================================

import os
import datetime
import time
import sys
import subprocess
import re
import glob
import traceback

REPO_ROOT = r"C:\PLC_REPO"
EXPORTS_ROOT = os.path.join(REPO_ROOT, "exports")
LOG_DIR = os.path.join(REPO_ROOT, "Logs")
TIMEOUT_S = 120

BRANCHES = ["dev", "staging", "prod"]
BRANCH_TO_PLCNAME = {"dev": "PLC_DEV", "staging": "PLC_STG", "prod": "PLC_PROD"}

PLCOPEN_DIR = os.path.join(EXPORTS_ROOT, "plcopen")
PLCOPEN_ONEFILE_NAME = "PLC_latest.plcopen.xml"

# -------------------------
# Logging
# -------------------------
def _init_logging():
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, "plc_agent_%s.log" % ts)
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

    print("===== plc_agent started:", ts, "=====")
    print("Repo:", REPO_ROOT)
    print("Branches:", ", ".join(BRANCHES))
    print("Log file:", log_path)
    return log_path

LOG_PATH = _init_logging()

def _ensure_dir(p):
    if not os.path.isdir(p):
        os.makedirs(p)

# -------------------------
# Project discovery
# -------------------------
def discover_projects(project_root_arg):
    project_root_arg = (project_root_arg or "").strip().strip('"')
    folder = os.path.dirname(project_root_arg) if "*" in project_root_arg else project_root_arg
    if not folder:
        folder = os.path.join(os.path.expanduser("~"), "Documents")

    if not os.path.isdir(folder):
        raise Exception("Project folder not found: %s" % folder)

    print("Scanning for .project files in:", folder)
    candidates = glob.glob(os.path.join(folder, "*.project"))
    if not candidates:
        raise Exception("No .project files found in: %s" % folder)

    print("Found .project files:")
    for p in candidates:
        print(" -", os.path.basename(p))

    by_name = {os.path.basename(p).lower(): p for p in candidates}
    mapping = {
        "dev": by_name.get("plc_dev.project"),
        "staging": by_name.get("plc_stg.project"),
        "prod": by_name.get("plc_prod.project"),
    }

    missing = [k for k, v in mapping.items() if not v]
    if missing:
        raise Exception("Missing required projects: %s" % ", ".join(missing))

    return mapping

# -------------------------
# Git helpers
# -------------------------
def _run_git(args):
    try:
        p = subprocess.Popen(
            ["git"] + args,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False
        )
        out, err = p.communicate()
        return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    except Exception as e:
        return 1, "", repr(e)

def _git_is_repo():
    return os.path.isdir(os.path.join(REPO_ROOT, ".git"))

def _git_has_origin():
    rc, out, err = _run_git(["remote"])
    return rc == 0 and ("origin" in out.split())

def _git_fetch():
    if not _git_has_origin():
        print("GIT: No origin remote configured.")
        return False
    rc, out, err = _run_git(["fetch", "origin"])
    if rc != 0:
        print("GIT: fetch failed"); print(out); print(err)
        return False
    return True

def _git_current_branch():
    rc, out, err = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip() if rc == 0 else "unknown"

def _git_checkout(branch):
    rc, out, err = _run_git(["checkout", branch])
    if rc == 0:
        return True
    rc2, out2, err2 = _run_git(["checkout", "-B", branch, "origin/%s" % branch])
    if rc2 != 0:
        print("GIT: checkout failed for", branch)
        print(out + out2); print(err + err2)
        return False
    return True

def _git_ensure_upstream(branch):
    if not _git_has_origin():
        return
    rc, out, err = _run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if rc == 0 and out.strip() == ("origin/%s" % branch):
        return
    _run_git(["branch", "--set-upstream-to=origin/%s" % branch, branch])

def _git_rev(ref):
    rc, out, err = _run_git(["rev-parse", ref])
    return out.strip() if rc == 0 else ""

def _git_remote_has_new_commit(branch):
    local = _git_rev(branch)
    remote = _git_rev("origin/%s" % branch)
    if not local or not remote:
        return False, local, remote
    return (local != remote), local, remote

def _git_fast_forward(branch):
    rc, out, err = _run_git(["pull", "--ff-only", "origin", branch])
    if rc != 0:
        print("GIT: pull --ff-only failed for", branch)
        print(out); print(err)
        return False
    return True

def _git_status_porcelain():
    rc, out, err = _run_git(["status", "--porcelain"])
    if rc != 0:
        print("GIT: status failed"); print(out); print(err)
        return ""
    return out.strip()

def _git_commit_all_if_dirty(branch, message):
    """
    Commit EVERYTHING in the repo (git add -A) if there are changes.
    """
    st = _git_status_porcelain()
    if not st:
        print("GIT: working tree clean (no commit needed).")
        return True, "clean"

    print("GIT: changes detected -> committing entire repo")
    print("GIT: status porcelain:\n%s" % st)

    rc1, out1, err1 = _run_git(["add", "-A"])
    if rc1 != 0:
        print("GIT: add -A failed"); print(out1); print(err1)
        return False, "git add -A failed"

    rc2, out2, err2 = _run_git(["commit", "-m", message])
    if rc2 != 0:
        if "nothing to commit" in (out2 + err2).lower():
            return True, "nothing to commit"
        print("GIT: commit failed"); print(out2); print(err2)
        return False, "git commit failed"

    if _git_has_origin():
        rc3, out3, err3 = _run_git(["push", "origin", branch])
        if rc3 != 0:
            print("GIT: push failed"); print(out3); print(err3)
            return False, "git push failed"

    return True, "committed+push"

# -------------------------
# PLCopen normalization
# -------------------------
def normalize_plcopen_xml(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf-8-sig"); enc = "utf-8"
        except:
            text = raw.decode("latin-1"); enc = "latin-1"

        original = text
        text = re.sub(r'creationDateTime="[^"]+"', 'creationDateTime="1970-01-01T00:00:00"', text)
        text = re.sub(r'modificationDateTime="[^"]+"', 'modificationDateTime="1970-01-01T00:00:00"', text)

        m = re.search(r"<PlaceholderRedirections>.*?</PlaceholderRedirections>", text, flags=re.DOTALL)
        if m:
            block = m.group(0)
            redirs = []
            for ln in block.splitlines():
                if "<PlaceholderRedirection" in ln:
                    redirs.append(ln.strip())
            redirs = sorted(set(redirs))
            indent = "  "
            new_block = "<PlaceholderRedirections>\n" + "\n".join([indent + r for r in redirs]) + "\n</PlaceholderRedirections>" if redirs else "<PlaceholderRedirections>\n</PlaceholderRedirections>"
            text = text[:m.start()] + new_block + text[m.end():]

        if text != original:
            with open(path, "wb") as f:
                f.write(text.encode(enc, errors="replace"))
            print("PLCOPEN: normalized volatile metadata.")
        else:
            print("PLCOPEN: already stable/normalized.")
    except Exception as e:
        print("WARNING: normalize_plcopen_xml failed:", repr(e))

# -------------------------
# CODESYS helpers
# -------------------------
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

def _wait_active_app(proj, timeout_s):
    start = time.time()
    while (time.time() - start) < timeout_s:
        if hasattr(proj, "active_application"):
            app = proj.active_application
            if app is not None:
                return app
        time.sleep(1)
    return None

def _connect_and_login(app, user, pw, timeout_s):
    online_app = online.create_online_application(app)
    dev = online_app.get_online_device()

    if user and pw:
        print("Online: injecting env credentials")
        try: online.set_specific_credentials(dev, user, pw)
        except Exception as e: print("WARNING: set_specific_credentials failed:", repr(e))
        if hasattr(dev, "set_credentials_for_initial_user"):
            try: dev.set_credentials_for_initial_user(user, pw)
            except Exception as e: print("WARNING: set_credentials_for_initial_user failed:", repr(e))
    else:
        print("Online: relying on stored credentials")

    if hasattr(dev, "connected") and getattr(dev, "connected"):
        print("Online: already connected")
    else:
        print("Online: connecting...")
        dev.connect()

    start = time.time()
    while (time.time() - start) < timeout_s:
        if hasattr(dev, "connected") and getattr(dev, "connected"):
            break
        time.sleep(0.5)

    if not (hasattr(dev, "connected") and getattr(dev, "connected")):
        raise Exception("Device did not connect within timeout")

    if not online_app.is_logged_in:
        OnlineChangeOption = globals().get("OnlineChangeOption", None)
        if OnlineChangeOption is None:
            raise Exception("OnlineChangeOption not found in globals()")
        print("Online: login (Keep)")
        online_app.login(OnlineChangeOption.Keep, False)
        if not online_app.is_logged_in:
            raise Exception("Login failed")

    return online_app, dev

def _disconnect_best_effort(online_app, dev):
    try:
        if hasattr(dev, "connected") and getattr(dev, "connected") and hasattr(dev, "disconnect"):
            dev.disconnect()
    except:
        pass
    try:
        if hasattr(online_app, "logout"):
            online_app.logout()
    except:
        pass

def _try_download_to_controller(online_app):
    OnlineChangeOption = globals().get("OnlineChangeOption", None)
    if OnlineChangeOption is None:
        return False, "OnlineChangeOption missing"

    method_names = ["download", "application_download", "program_download"]
    opt_names = ["Download", "FullDownload", "All", "Keep"]
    last_err = "n/a"

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

# -------------------------
# Actions
# -------------------------
def run_capture_dev(project_path, user, pw):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    plc_name = "PLC_DEV"

    out_arch = os.path.join(EXPORTS_ROOT, "archives", "dev")
    _ensure_dir(out_arch)
    _ensure_dir(PLCOPEN_DIR)

    proj = _open_project_primary(project_path)
    app = _wait_active_app(proj, TIMEOUT_S)
    if app is None:
        return False, "active_application timeout"

    online_app, dev = _connect_and_login(app, user, pw, TIMEOUT_S)
    try:
        if hasattr(online_app, "source_download"):
            print("[dev] CAPTURE: source_download")
            online_app.source_download()

        archive_path = os.path.join(out_arch, "%s_%s.projectarchive" % (plc_name, ts))
        print("[dev] CAPTURE: saving archive -> %s" % archive_path)
        if hasattr(proj, "save_archive"):
            proj.save_archive(archive_path)

        plcopen_latest = os.path.join(PLCOPEN_DIR, PLCOPEN_ONEFILE_NAME)

        class ER(ExportReporter):
            def error(self, obj, message): print("PLCOPEN export ERROR on %s: %s" % (obj, message))
            def warning(self, obj, message): print("PLCOPEN export WARNING on %s: %s" % (obj, message))
            def nonexportable(self, obj): print("PLCOPEN not exportable: %s" % obj)
            @property
            def aborting(self): return False

        reporter = ER()

        print("[dev] CAPTURE: exporting PLCopen -> %s" % plcopen_latest)
        proj.active_application.export_xml(reporter, plcopen_latest, recursive=True)

        normalize_plcopen_xml(plcopen_latest)

        # Commit entire repo if anything changed
        msg = "DEV capture %s" % ts
        ok, note = _git_commit_all_if_dirty("dev", msg)
        return (ok, note) if ok else (False, note)

    finally:
        _disconnect_best_effort(online_app, dev)

def run_deploy(branch, project_path, user, pw):
    if not _git_has_origin():
        return False, "no origin"

    _git_fetch()
    has_new, local_sha, remote_sha = _git_remote_has_new_commit(branch)
    print("[%s] DEPLOY: local=%s remote=%s" % (
        branch,
        local_sha[:10] if local_sha else "?",
        remote_sha[:10] if remote_sha else "?"
    ))

    if not has_new:
        return True, "no remote changes"

    if not _git_fast_forward(branch):
        return False, "ff-pull failed (branch diverged)"

    proj = _open_project_primary(project_path)
    app = _wait_active_app(proj, TIMEOUT_S)
    if app is None:
        return False, "active_application timeout"

    online_app, dev = _connect_and_login(app, user, pw, TIMEOUT_S)
    try:
        ok, used = _try_download_to_controller(online_app)
        if not ok:
            return False, "download failed (%s)" % used
        return True, "deployed (%s)" % used
    finally:
        _disconnect_best_effort(online_app, dev)

# -------------------------
# Main
# -------------------------
def main():
    if len(sys.argv) < 2:
        print("ERROR: Missing project folder/path argument.")
        system.exit()

    project_arg = sys.argv[1]
    print("Project arg:", project_arg)

    projects_map = discover_projects(project_arg)
    print("Projects resolved:")
    for b in BRANCHES:
        print("  %-7s -> %s" % (b, projects_map[b]))

    user = os.environ.get("CODESYS_USER", "")
    pw = os.environ.get("CODESYS_PASS", "")

    if not _git_is_repo():
        print("ERROR: No .git folder found in", REPO_ROOT)
        system.exit()

    start_branch = _git_current_branch()
    print("Starting branch:", start_branch)

    _git_fetch()

    summary = {}
    overall_ok = True

    # DEV
    print("\n" + "=" * 70)
    print("BRANCH: dev")
    print("=" * 70)
    try:
        if not _git_checkout("dev"):
            summary["dev"] = ("FAIL", "checkout failed")
            overall_ok = False
        else:
            _git_ensure_upstream("dev")
            ok, msg = run_capture_dev(projects_map["dev"], user, pw)
            summary["dev"] = ("OK" if ok else "FAIL", msg)
            if not ok:
                overall_ok = False
    except Exception as e:
        overall_ok = False
        summary["dev"] = ("FAIL", "exception: %s" % repr(e))
        traceback.print_exc()

    # STAGING + PROD
    for branch in ["staging", "prod"]:
        print("\n" + "=" * 70)
        print("BRANCH:", branch)
        print("=" * 70)
        try:
            if not _git_checkout(branch):
                summary[branch] = ("FAIL", "checkout failed")
                overall_ok = False
                continue
            _git_ensure_upstream(branch)
            ok, msg = run_deploy(branch, projects_map[branch], user, pw)
            summary[branch] = ("OK" if ok else "FAIL", msg)
            if not ok:
                overall_ok = False
        except Exception as e:
            overall_ok = False
            summary[branch] = ("FAIL", "exception: %s" % repr(e))
            traceback.print_exc()

    try:
        if start_branch and start_branch != "unknown":
            _git_checkout(start_branch)
    except:
        pass

    print("\n" + "#" * 70)
    print("RUN SUMMARY")
    print("#" * 70)
    for b in BRANCHES:
        st, msg = summary.get(b, ("?", "no result"))
        print("%-8s : %-4s - %s" % (b, st, msg))

    print("Log:", LOG_PATH)
    print("Overall:", "OK" if overall_ok else "FAIL")
    print("===== plc_agent finished =====")

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
