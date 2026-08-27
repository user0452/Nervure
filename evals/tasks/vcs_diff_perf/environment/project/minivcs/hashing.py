import hashlib

def hash_bytes(data: bytes, metrics=None):
    if metrics is not None:
        metrics.hashes += 1
    return hashlib.sha1(data).hexdigest()
