# encoding: utf-8
# ============================================================
# dev_capture.py  (DEV: PLC -> Git)
#
# - Pull source from PLC (source_download)
# - Save archive (timestamped) to exports\archives\dev\
# - Copy that archive to ONE stable file: exports\archives\PLC_latest.projectarchive
# - Export ONE PLCopen XML (overwritten): exports\plcopen\PLC_latest.plcopen.xml
# - Normalize PLCopen (remove volatile timestamps + CANONICALIZE PlaceholderRedirections so it won't diff)
# - Commit ENTIRE repo if dirty (git add -A) and push to origin/dev
#
# Run:
# "C:\Program Files\CODESYS 3.5.21.40\CODESYS\Common\CODESYS.exe" --noUI --profile="CODESYS V3.5 SP21 Patch 4" --runscript="C:\PLC_REPO\scripts\dev_capture.py" --scriptargs:"C:\Users\Test_bench\Documents\PLC_DEV.project"
# ============================================================

import os
import sys
import time
import datetime
import subprocess
import re
import traceback

REPO_ROOT = r"C:\PLC_REPO"
EXPORTS_ROOT = os.path.join(REPO_ROOT, "exports")
LOG_DIR = os.path.join(REPO_ROOT, "Logs")
TIMEOUT_S = 120

BRANCH = "dev"
PLC_NAME = "PLC_DEV"

ARCHIVE_DIR = os.path.join(EXPORTS_ROOT, "archives", "dev")
PLCOPEN_DIR = os.path.join(EXPORTS_ROOT, "plcopen")

ARCHIVE_LATEST = os.path.join(EXPORTS_ROOT, "archives", "PLC_latest.projectarchive")
PLCOPEN_LATEST = os.path.join(PLCOPEN_DIR, "PLC_latest.plcopen.xml")


# -------------------------
# Logging (one log per run)
# -------------------------
def _init_logging():
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, "dev_capture_%s.log" % ts)
    f = open(log_path, "a", buffering=1)

    class _Tee(object):
        def __init__(self, *streams):
            self.streams = streams
        def write(self, s):
            for st in self.streams:
                try:
                    st.write(s)
                except:
                    pass
        def flush(self):
            for st in self.streams:
                try:
                    st.flush()
                except:
                    pass

    sys.stdout = _Tee(sys.__stdout__, f)
    sys.stderr = _Tee(sys.__stderr__, f)

    print("===== dev_capture started:", ts, "=====")
    print("Repo:", REPO_ROOT)
    print("Branch:", BRANCH)
    print("Log file:", log_path)
    return log_path

LOG_PATH = _init_logging()


def _ensure_dir(p):
    if not os.path.isdir(p):
        os.makedirs(p)


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


def _git_has_origin():
    rc, out, err = _run_git(["remote"])
    return rc == 0 and ("origin" in out.split())


def _git_checkout(branch):
    rc, out, err = _run_git(["checkout", branch])
    if rc == 0:
        return True
    rc2, out2, err2 = _run_git(["checkout", "-B", branch, "origin/%s" % branch])
    if rc2 != 0:
        print("GIT: checkout failed for", branch)
        print(out + out2)
        print(err + err2)
        return False
    return True


def _git_ensure_upstream(branch):
    if not _git_has_origin():
        return
    rc, out, err = _run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if rc == 0 and out.strip() == ("origin/%s" % branch):
        return
    _run_git(["branch", "--set-upstream-to=origin/%s" % branch, branch])


def _git_status_porcelain():
    rc, out, err = _run_git(["status", "--porcelain"])
    if rc != 0:
        print("GIT: status failed")
        print(out); print(err)
        return ""
    return out.strip()


def _git_commit_all_if_dirty(branch, message):
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
# - remove timestamps
# - canonicalize PlaceholderRedirections (extract -> normalize -> sort -> rebuild)
# -------------------------
def normalize_plcopen_xml(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()

        # BOM-safe decode
        try:
            text = raw.decode("utf-8-sig"); enc = "utf-8"
        except:
            text = raw.decode("latin-1"); enc = "latin-1"

        original = text

        # 1) Ignore volatile header timestamps
        text = re.sub(r'creationDateTime="[^"]+"', 'creationDateTime="1970-01-01T00:00:00"', text)
        text = re.sub(r'modificationDateTime="[^"]+"', 'modificationDateTime="1970-01-01T00:00:00"', text)

        # 2) Canonicalize PlaceholderRedirections block
        block_re = re.compile(r"<PlaceholderRedirections\b[^>]*>.*?</PlaceholderRedirections>", re.DOTALL)
        m = block_re.search(text)

        if m:
            block = m.group(0)

            # Find all <PlaceholderRedirection ... /> tags
            tag_re = re.compile(r"<PlaceholderRedirection\b[^>]*/\s*>")
            tags = tag_re.findall(block)

            canon = []
            for t in tags:
                tt = re.sub(r"\s+", " ", t.strip())        # collapse whitespace
                tt = re.sub(r"\s*/\s*>", " />", tt)        # normalize closing
                canon.append(tt)

            # Sort by Placeholder="..."
            def _key(tag):
                mm = re.search(r'Placeholder="([^"]+)"', tag)
                return mm.group(1) if mm else tag

            canon = sorted(set(canon), key=_key)

            # Rebuild deterministically (indent does not need to match original exactly)
            inner_indent = "        "  # 8 spaces (looks nice & stable)
            if canon:
                new_block = "<PlaceholderRedirections>\n" + \
                            "\n".join([inner_indent + t for t in canon]) + \
                            "\n</PlaceholderRedirections>"
            else:
                new_block = "<PlaceholderRedirections>\n</PlaceholderRedirections>"

            text = text[:m.start()] + new_block + text[m.end():]

        # Also handle self-closing <PlaceholderRedirections/>
        text = re.sub(
            r"<PlaceholderRedirections\b[^>]*/>",
            "<PlaceholderRedirections>\n</PlaceholderRedirections>",
            text
        )

        if text != original:
            with open(path, "wb") as f:
                f.write(text.encode(enc, errors="replace"))
            print("PLCOPEN: normalized volatile metadata (timestamps + PlaceholderRedirections canonicalized).")
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


# -------------------------
# Main capture flow
# -------------------------
def main():
    if len(sys.argv) < 2:
        print("ERROR: Missing DEV project path")
        print('--scriptargs:"C:\\Users\\Test_bench\\Documents\\PLC_DEV.project"')
        system.exit()

    dev_project_path = sys.argv[1].strip().strip('"')
    print("DEV project:", dev_project_path)

    if not os.path.isdir(REPO_ROOT):
        print("ERROR: REPO_ROOT not found:", REPO_ROOT)
        system.exit()

    _ensure_dir(ARCHIVE_DIR)
    _ensure_dir(PLCOPEN_DIR)

    # Ensure on dev branch
    if not _git_checkout(BRANCH):
        print("ERROR: could not checkout dev branch")
        system.exit()
    _git_ensure_upstream(BRANCH)

    user = os.environ.get("CODESYS_USER", "")
    pw = os.environ.get("CODESYS_PASS", "")

    proj = _open_project_primary(dev_project_path)
    app = _wait_active_app(proj)
    if app is None:
        print("ERROR: active_application timeout")
        system.exit()

    online_app, dev = _connect_and_login(app, user, pw)
    try:
        # Pull source
        if hasattr(online_app, "source_download"):
            print("[dev] CAPTURE: source_download")
            online_app.source_download()
        elif hasattr(online_app, "source_upload"):
            print("[dev] CAPTURE: source_upload")
            online_app.source_upload()
        else:
            print("[dev] CAPTURE: no source pull method found")

        # Save archive (timestamped + stable latest)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = os.path.join(ARCHIVE_DIR, "%s_%s.projectarchive" % (PLC_NAME, ts))

        print("[dev] CAPTURE: saving archive ->", archive_path)
        if hasattr(proj, "save_archive"):
            proj.save_archive(archive_path)
        else:
            print("[dev] ERROR: proj.save_archive not available")
            system.exit()

        try:
            import shutil
            shutil.copyfile(archive_path, ARCHIVE_LATEST)
            print("[dev] CAPTURE: wrote latest archive ->", ARCHIVE_LATEST)
        except Exception as e:
            print("[dev] WARNING: could not write latest archive:", repr(e))

        # Export PLCopen (ONE stable file)
        class ER(ExportReporter):
            def error(self, obj, message):
                print("PLCOPEN export ERROR on %s: %s" % (obj, message))
            def warning(self, obj, message):
                print("PLCOPEN export WARNING on %s: %s" % (obj, message))
            def nonexportable(self, obj):
                print("PLCOPEN not exportable:", obj)
            @property
            def aborting(self):
                return False

        reporter = ER()

        print("[dev] CAPTURE: exporting PLCopen ->", PLCOPEN_LATEST)

        export_ok = False
        try:
            proj.active_application.export_xml(reporter, PLCOPEN_LATEST, recursive=True)
            export_ok = True
            print("[dev] CAPTURE: PLCopen export OK via app.export_xml")
        except Exception as e:
            print("[dev] CAPTURE: app.export_xml failed:", repr(e))

        if not export_ok:
            try:
                proj.export_xml(reporter, proj.get_children(False), PLCOPEN_LATEST, recursive=True)
                export_ok = True
                print("[dev] CAPTURE: PLCopen export OK via proj.export_xml")
            except Exception as e:
                print("[dev] CAPTURE: proj.export_xml failed:", repr(e))

        if not export_ok:
            print("[dev] ERROR: PLCopen export failed")
            system.exit()

        normalize_plcopen_xml(PLCOPEN_LATEST)

        # Commit/push whole repo if dirty
        msg = "DEV capture %s" % ts
        ok, note = _git_commit_all_if_dirty(BRANCH, msg)
        if not ok:
            print("[dev] ERROR: git commit failed:", note)
            system.exit()
        print("[dev] GIT:", note)

        print("===== dev_capture finished OK =====")
        print("Log:", LOG_PATH)

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
