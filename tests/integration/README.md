# Privileged integration checks

`test_routed_lab.py` implements step-04 routing, endpoint delivery, ownership and reset checks. It is skipped unless explicitly enabled as root inside the dedicated Ubuntu VM:

```bash
sudo env AQOS_RUN_LAB_INTEGRATION=1 .venv/bin/python -m pytest tests/integration/test_routed_lab.py -ra
```

Start with no project lab active. Both single-client and four-device bridge configurations are created, checked twice and removed. Main-namespace network configuration and an unrelated sentinel namespace must remain unchanged. See [the routed lab guide](../../docs/routed-lab.md). Actual QoS enforcement and recovery tests remain later work.

The ISP suite recreates the owned lab and measures sustained receiver goodput at the configured rates. It exercises both address families, timed transitions, concurrent clients, private trace access and worker cleanup. [ISP emulation guide](../../docs/isp-emulation.md)

```bash
sudo env AQOS_RUN_ISP_INTEGRATION=1 .venv/bin/python -m pytest tests/integration/test_isp_profiles.py -ra
```
