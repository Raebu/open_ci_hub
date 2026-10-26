"""Offline transfer envelope for tiny owner-approved jobs, not network transport.

Device signatures prove origin; recipient deduplicates receipts. Cloud/API
commands, shell jobs, credentials and real-money tasks are unsupported.
"""
from __future__ import annotations
import hashlib
import json
import secrets
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from autonomy_core import canonical

ALLOWED=frozenset(("json.inspect","python.syntax","hash.benchmark"))
def envelope(sender,receiver,task,body,*,signer,now=None,ttl=120):
    if (task not in ALLOWED or type(ttl)!=int or not 5<=ttl<=300 or
        not isinstance(sender,str) or not isinstance(receiver,str) or
        not sender.isidentifier() or not receiver.isidentifier() or
        sender==receiver):
        raise ValueError("Unapproved offline transfer.")
    encoded=canonical(body).encode()
    if len(encoded)>2048:
        raise ValueError("Offline task too large.")
    now=int(time.time()) if now is None else now
    payload={"schema":"open-ci-mesh.v1","sender":sender,"receiver":receiver,
             "task":task,"body":body,"issued":now,"expires":now+ttl,
             "nonce":secrets.token_hex(16)}
    signed=canonical(payload).encode()
    return {"payload":payload,"signature":signer.sign(signed).hex(),
            "digest":hashlib.sha256(signed).hexdigest()}
def verify(record,*,pubkey,receiver,seen,now=None):
    now=int(time.time()) if now is None else now
    if not isinstance(record,dict) or set(record)!={"payload","signature","digest"}:
        raise ValueError("Malformed signed transfer.")
    p=record["payload"]
    if (not isinstance(p,dict) or set(p)!={"schema","sender","receiver","task",
          "body","issued","expires","nonce"} or p["schema"]!="open-ci-mesh.v1"
          or p["task"] not in ALLOWED or p["receiver"]!=receiver
          or type(p["issued"]) is not int or type(p["expires"]) is not int
          or p["issued"]>now or p["expires"]<=now or p["expires"]-p["issued"]>300):
        raise ValueError("Unapproved or expired transfer.")
    wire=canonical(p).encode()
    if len(wire)>4096 or hashlib.sha256(wire).hexdigest()!=record["digest"]:
        raise ValueError("Offline data integrity failure.")
    Ed25519PublicKey.from_public_bytes(pubkey).verify(bytes.fromhex(record["signature"]),wire)
    key=(p["sender"],p["nonce"])
    if key in seen:
        raise ValueError("Duplicate offline delivery.")
    seen.add(key)
    return {"accepted":True,"task":p["task"],"nonce":p["nonce"],
            "notice":"Execution remains subject to recipient resource and owner policy."}
