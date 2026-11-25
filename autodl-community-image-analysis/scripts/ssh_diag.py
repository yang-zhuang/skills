#!/usr/bin/env python3
"""Layered SSH diagnosis: TCP -> host key fingerprint -> auth. Never echoes password.

Usage:
    AUTODL_SSH_PASS=<password> AUTODL_SSH_HOST=<host> AUTODL_SSH_PORT=<port> \
        python ssh_diag.py

When auth fails, stop retrying (repeated failures trigger login risk control).
"""
import base64
import hashlib
import os
import socket

import paramiko


def main():
    password = os.environ.get("AUTODL_SSH_PASS")
    host = os.environ.get("AUTODL_SSH_HOST")
    port = int(os.environ.get("AUTODL_SSH_PORT", "22"))
    if not password or not host:
        print("AUTODL_SSH_PASS and AUTODL_SSH_HOST must be set")
        return 2
    print("password_len:", len(password))

    try:
        s = socket.create_connection((host, port), timeout=15)
        banner = s.recv(128).decode("utf-8", errors="replace").strip()
        s.close()
        print("TCP:", "OK", "banner:", banner[:80])
    except Exception as exc:
        print("TCP:", "FAIL", type(exc).__name__, str(exc)[:120])
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, port=port, username=os.environ.get("AUTODL_SSH_USER", "root"),
                       password=password, timeout=20, allow_agent=False, look_for_keys=False)
        _, out, _ = client.exec_command("hostname")
        print("AUTH: OK, hostname:", out.read().decode().strip())
        key = client.get_transport().get_remote_server_key()
        fp = hashlib.sha256(key.as_bytes()).digest()
        print("hostkey_sha256:", "SHA256:" + base64.b64encode(fp).decode().rstrip("="))
        client.close()
        return 0
    except Exception as exc:
        print("AUTH:", "FAIL", type(exc).__name__, str(exc)[:160])
        print("Do not retry repeatedly: TCP is up, so this is either a transient"
              " auth state or a changed password. Ask the user to confirm.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
