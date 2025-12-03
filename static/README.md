# Configuration
A simple way of configuring a network impairment is to run tcgui on the server side and limit traffic to from a specific IP on one or more ports. This is useful e.g when testing mobile apps that are not easily impaired locally.

Note that the "limit" is the number of packets of the traffic control queue and should be adjusted according to the simulated link.

Example settings:
```
| Link rate                         | Recommended limit       | Notes                              |
| --------------------------------- | ----------------------- | ---------------------------------- |
| 100 Kbps                          | 5–10 packets            | keeps latency reasonable           |
| 1 Mbps                            | 20–50 packets           |                                    |
| 10 Mbps+                          | 100–200 packets         |                                    |
| Satellite-like links (high delay) | smaller limit preferred | prevents queuing delay compounding |
```


The following example limits incoming and outgoing traffic (to from server running tcgui) from IP 1.2.3.4 on TCP ports 12345-12346
```
{
    "ens5": {
        "outgoing": {
            "dst_network=1.2.3.4/32, src_port=12345, protocol=ip": {
                "filter_id": "800::800",
                "limit": 10,
                "delay": "200.0ms",
                "rate": "55Kbps"
            },
            "dst_network=1.2.3.4/32, src_port=12346, protocol=ip": {
                "filter_id": "800::801",
                "limit": 10,
                "delay": "200.0ms",
                "rate": "55Kbps"
            }
        },
        "incoming": {
            "src_network=1.2.3.4/32, dst_port=12345, protocol=ip": {
                "filter_id": "800::800",
                "limit": 10,
                "delay": "200.0ms",
                "rate": "55Kbps"
            },
            "src_network=1.2.3.4/32, dst_port=12346, protocol=ip": {
                "filter_id": "800::801",
                "limit": 10,
                "delay": "200.0ms",
                "rate": "55Kbps"
            }
        }
    }
}
```


How traffic control and netem works when used on a local machine for outgoing traffic:
```
┌──────────────────────────────────────────────┐
│          Application / TCP/IP stack          │
└──────────────────────────────────────────────┘
                     │
                     ▼
            ┌──────────────────┐
            │     TBF/HTB      │  <-- rate shaping
            │ rate 128k        │
            │ burst 16kb       │  (max burst queued)
            │ latency 400ms    │
            └──────────────────┘
                     │
                     ▼
            ┌──────────────────┐
            │      Netem       │  <-- delay, jitter, loss, reorder
            │ delay 300ms      │
            │ delay-distro 5ms │
            │ limit 50         │  (max packets in netem buffer)
            │ loss 0.2%        │
            │ corrupt 0.1%     │
            │ duplicate 0.1%   │
            │ reordering 0.1%  │
            └──────────────────┘
                     │
                     ▼
                Physical NIC
```
