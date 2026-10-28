#!/usr/bin/env python3
"""Safe one-time company channel enrolment, using the existing desktop keyring.

Never imports ChatGPT OAuth tokens, personal inboxes or browser cookies.
No password/token prints to stdout, files, command args or Git. All later
provider calls are GET only and independently verified against account IDs.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from business_channels import CONFIG, ChannelError, _secret, read_config, validate_config


def company_accounts():
    """Known owner-provided business addresses; only scope support mailboxes."""
    return {"schema":"raeburn.business-channels.v1","mode":"read_only",
            "accounts":[
             {"alias":"raeburn-primary","provider":"gmail",
              "identity":"martin@theraeburngroup.com",
              "purpose":"business_read_only","enabled":True,
              "auth_secret_label":"gmail-raeburn-primary",
              "allowed_recipients":[]},
             {"alias":"raeburn-contact","provider":"gmail",
              "identity":"contact@theraeburngroup.com",
              "purpose":"business_read_only","enabled":True,
              "auth_secret_label":"gmail-raeburn-contact",
              "allowed_recipients":[]},
             {"alias":"raeburn-resend","provider":"resend",
              "identity":"raeburn-business",
              "purpose":"business_read_only","enabled":True,
              "auth_secret_label":"resend-raeburn-readonly",
              "allowed_recipients":["support@theraeburngroup.com",
                                    "security@theraeburngroup.com"]},
             {"alias":"personal-outlook-excluded","provider":"microsoft",
              "identity":"tripti.raeburn@hotmail.com",
              "purpose":"excluded_personal","enabled":False,
              "auth_secret_label":"","allowed_recipients":[]},
             {"alias":"linkedin-profile-only","provider":"linkedin",
              "identity":"profile_search_connector_only",
              "purpose":"business_read_only","enabled":False,
              "auth_secret_label":"","allowed_recipients":[]}
            ]}


def init_key(*,run=subprocess.run,lookup=_secret):
    current=lookup("channels-hmac","business-identity-hmac",run=run)
    if current:
        # Never replace a running system's HMAC key: historic holds
        # would become unmatchable and could allow duplicate contact.
        if len(current)!=64:
            raise ChannelError("Existing keyring HMAC selector is malformed; no rotation.")
        try:
            bytes.fromhex(current)
        except ValueError:
            raise ChannelError("Existing private identity key is malformed.")
        return "existing_protected_identity_key"
    key=secrets.token_hex(32)
    argv=["/usr/bin/secret-tool","store",
          "--label=Open CI Hub business channel HMAC identity",
          "application","open-ci-hub",
          "purpose","business-identity-hmac",
          "account","channels-hmac"]
    try:
        response=run(argv,input=(key+"\n").encode(),capture_output=True,
                     timeout=12,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise ChannelError("Desktop protected keyring unavailable.")
    if response.returncode:
        raise ChannelError("Desktop keyring refused protected identity creation.")
    check=lookup("channels-hmac","business-identity-hmac",run=run)
    if check!=key:
        raise ChannelError("Private identity key readback mismatch.")
    return "created_protected_identity_key"


def install(*,confirm=False, path=CONFIG,run=subprocess.run,lookup=_secret):
    if confirm is not True:
        raise ChannelError("Explicit owner approval needed for local registration.")
    data=validate_config(company_accounts())
    path=Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ChannelError("Unsafe channel configuration destination.")
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    current=None
    if path.exists():
        current=read_config(path)
        if current!=data:
            raise ChannelError("Existing channel policy differs; do not overwrite.")
    # Credential selector only; NO OAuth credentials are copied.
    key_status=init_key(run=run,lookup=lookup)
    if current is None:
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"w",encoding="utf8") as file:
            file.write(json.dumps(data,indent=2,sort_keys=True)+"\n")
            file.flush()
            os.fsync(file.fileno())
    os.chmod(path,0o600)
    return {"configured":True,"accounts":len(data["accounts"]),
            "gmail_business_mailboxes":2,"resend_scoped_account":1,
            "personal_outlook_excluded":True,"linkedin_connector_only":True,
            "identity_keyring":key_status,"api_tokens_copied":False,
            "send_permission":False}


def main(argv=None):
    import argparse
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--confirm",action="store_true")
    args=cli.parse_args(argv)
    try:
        result=install(confirm=args.confirm)
        print(json.dumps(result,indent=2,sort_keys=True))
        return 0
    except (ChannelError,ValueError,PermissionError,OSError) as exc:
        print("Channel setup blocked: "+str(exc)[:170],file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
