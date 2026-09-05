"""Create the PRIV App Connector Group and its provisioning key.

Same rules and helpers as zpa_create.py: reuse by name, create-only, no signingCertId
on keys, assert the cert landed. Run after zpa_create.py; extends zpa-created.json.
"""
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
from zpa_create import LAT, LOC, LON, OUT, PREFIX, certs, ensure_group, ensure_key  # noqa: E402

CG_NAME = f"{PREFIX} PRIV Connector Group"
CG_KEY = f"{PREFIX} PRIV CONNECTOR_GRP key v2"


def main():
    c = certs()
    root, conn = c["Root"], c["Connector"]
    made = json.load(open(OUT)) if os.path.exists(OUT) else {}
    gid = ensure_group("appConnectorGroup", CG_NAME, {
        "name": CG_NAME,
        "description": "Standalone AWS lab PRIV connector. Serves the lab nginx server only.",
        "enabled": True, "latitude": LAT, "longitude": LON, "location": LOC,
        "upgradeDay": "SUNDAY", "upgradeTimeInSecs": "66600", "dnsQueryType": "IPV4_IPV6",
        "signingCertId": root, "enrollmentCertId": conn,
        "overrideVersionProfile": True, "versionProfileId": "0"})
    made.update(privConnectorGroupId=gid,
                privConnKeyId=ensure_key("CONNECTOR_GRP", CG_KEY, root, gid))
    json.dump(made, open(OUT, "w"), indent=2)
    print(json.dumps(made, indent=2))


if __name__ == "__main__":
    main()
