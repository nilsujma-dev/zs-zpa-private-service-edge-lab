"""Block until every lab component reports ZPN_STATUS_AUTHENTICATED, or time out.

    python3 scripts/wait_enrolled.py --timeout 900 --interval 30

Exit 0 when all components are authenticated, 1 on timeout. Prints one progress
line per poll so a control plane tailing the log can see movement. Enrolment from
boot is normally ~3 minutes; a Service Edge that is still not enrolled after ten
means the bootstrap fell back to OAuth -- see docs/runbook.md, failure mode 0.
"""
import argparse
import sys
import time

from status import zpa_components


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()

    deadline = time.time() + args.timeout
    poll = 0
    while True:
        poll += 1
        comps = zpa_components()
        done = [c for c in comps if c["authenticated"]]
        pending = [c["label"] for c in comps if not c["authenticated"]]
        print(f"[{poll}] {len(done)}/{len(comps)} authenticated"
              + (f"  waiting on: {', '.join(pending)}" if pending else ""), flush=True)
        if not pending:
            print("all components enrolled and authenticated")
            return 0
        if time.time() >= deadline:
            print(f"timed out after {args.timeout}s; still pending: {', '.join(pending)}")
            return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
