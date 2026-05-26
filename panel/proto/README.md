# XRay gRPC protobuf

This directory contains a script that fetches the required `.proto` files
from the upstream [XTLS/Xray-core](https://github.com/XTLS/Xray-core)
repository and compiles them with `grpcio-tools`. The generated
`*_pb2.py` / `*_pb2_grpc.py` modules are gitignored — regenerate them by
running:

```bash
cd panel
python -m proto.compile
```

This pulls `app/proxyman/command/command.proto` (HandlerService — used to
add/remove VLESS users at runtime) and `app/stats/command/command.proto`
(StatsService — used to read per-user uplink/downlink counters).
