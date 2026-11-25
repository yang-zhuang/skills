#!/usr/bin/env python3
"""Run a batch of read-only commands on a remote host via paramiko.

Usage:
    AUTODL_SSH_PASS=<password> AUTODL_SSH_HOST=<host> AUTODL_SSH_PORT=<port> \
        python ssh_batch.py <cmd_file> <out_file>

The password is read from the environment only; it is never written to disk
or echoed. Each command is separated by a marker in the output file.
"""
import os
import sys

import paramiko


def main():
    if len(sys.argv) != 3:
        print("usage: ssh_batch.py <cmd_file> <out_file>", file=sys.stderr)
        return 2
    cmd_file, out_file = sys.argv[1], sys.argv[2]
    password = os.environ.get("AUTODL_SSH_PASS")
    host = os.environ.get("AUTODL_SSH_HOST")
    port = int(os.environ.get("AUTODL_SSH_PORT", "22"))
    if not password or not host:
        print("AUTODL_SSH_PASS and AUTODL_SSH_HOST must be set", file=sys.stderr)
        return 2

    with open(cmd_file, encoding="utf-8") as f:
        commands = [ln.rstrip("\n") for ln in f if ln.strip() and not ln.lstrip().startswith("#")]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username=os.environ.get("AUTODL_SSH_USER", "root"),
                   password=password, timeout=25, look_for_keys=False, allow_agent=False)

    lines = []
    try:
        for cmd in commands:
            lines.append("===== CMD: " + cmd + " =====")
            _, stdout, stderr = client.exec_command(cmd, timeout=120)
            out = stdout.read().decode("utf-8", errors="replace").rstrip()
            err = stderr.read().decode("utf-8", errors="replace").rstrip()
            if out:
                lines.append(out)
            if err:
                lines.append("----- STDERR -----")
                lines.append(err)
            lines.append("")
    finally:
        client.close()

    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote", out_file, "commands:", len(commands))
    return 0


if __name__ == "__main__":
    sys.exit(main())
