MicroView
======================

An implementation SmartNIC-to-host communication via RDMA for microservices metrics.

The description of the project can be found in our published research paper:

[MicroView: Cloud-Native Observability with Temporal Precision](https://dl.acm.org/doi/10.1145/3630202.3630233), ACM CONeXT-SW '23  
*Alessandro Cornacchia, Theophilus A. Benson, Muhammad Bilal, and Marco Canini*

<div align="center">
<img src="./uview-microservices.png" alt="drawing" width="500"/>
</div>
<p></p>


# Getting Started with MicroView

## Quick Setup

MicroView requires two components to run: a host agent and a SmartNIC collector. Follow these steps to get everything up and running.

### 1. Set Up the SmartNIC

First, configure the network interface on your SmartNIC:

```bash
# Configure network interface
sudo ip a a 10.200.0.53/24 dev enp3s0f1s0

# Verify connectivity to host
ping 10.200.0.28

# Find RDMA device info (note the device name, GID index and IB port)
show_gids | grep enp3s0f1s0
```

### 2. Start the Host Agent

On your host machine, start the MicroView agent:

```bash
python microview-host.py --rdma-queues 2 --debug
```

### 3. Generate Test Metrics

Create one or more metric generators to simulate application metrics:

```bash
python libmicroview.py --debug --num-metrics 64
```

### 4. Start the SmartNIC Collector

Using the RDMA device information from step 1, start the collector on the SmartNIC:

```bash
python microview-nic.py -c 172.18.0.39:5000 -l 2 -d "mlx5_3" \
                       --gid 1 --ib-port 1 \
                       --test with_prometheus_multithread --debug
```

When prompted, press Enter to start scraping metrics.

### 5. Access Prometheus Dashboard

Open Prometheus in your browser to visualize the collected metrics:
`http://192.168.100.2:8000`


### Test RDMA READ Pyverb
First test a simple client/server Pyverbs example:
```
python rdma/rdma_server.py
```
On another terminal:
```
python rdma/rdma_client.py --host 10.200.0.28 --port 7471
```

### Test RDMA Collector class

```
python rdma/rdma_passive.py
```

Then start test reading metrics:

```
python rdma/test_rdma_connection.py --host 10.200.0.28 --port 18515
```

## Test RDMA one-sided reader 
This test connects Queue Pairs (QP) on a RDMA passive side and a RDMA active side. 

**Requirements** For this test, the control information information is exchanged by serialization/deserialization to filesystem. Therefore, please run active/passive side on same machine.


### Start the passive side 
Open two terminals. The passive side creates the memory region to be READ()
```
(uview) (base) cornaca@mcnode28:[microview-cp]$ python rdma/helpers.py --debug
[INFO] 03 Apr 2025 13:37:38 helpers.py:828: Starting RDMA with QP pool size 1
[INFO] 03 Apr 2025 13:37:38 helpers.py:234: Opened RDMA device: mlx5_1
[DEBUG] 03 Apr 2025 13:37:38 helpers.py:268: Created Queue Pair #0 attributes
[DEBUG] 03 Apr 2025 13:37:38 helpers.py:285: Queue Pair #0 transitioned to INIT state
[INFO] 03 Apr 2025 13:37:38 helpers.py:296: Created Queue Pair #0: GID=0000:0000:0000:0000:0000:ffff:0ac8:001c, qp_num=831
[INFO] 03 Apr 2025 13:37:38 helpers.py:302: Created Queue Pair pool with 1 QPs
[INFO] 03 Apr 2025 13:37:38 helpers.py:102: Registered external memory region 'default': addr=0x559bf92ee020, rkey=2342331, size=4096
[INFO] 03 Apr 2025 13:37:38 helpers.py:147: Saved RDMA info to JSON file: mr_info.json
[INFO] 03 Apr 2025 13:37:38 helpers.py:151: Saved RDMA info to pickle file: mr_info.json
[INFO] 03 Apr 2025 13:37:38 helpers.py:350: Saved RDMA info to JSON file: rdma_passive_info
[INFO] 03 Apr 2025 13:37:38 helpers.py:354: Saved RDMA info to pickle file: rdma_passive_info
[INFO] 03 Apr 2025 13:37:38 helpers.py:701: RDMA started, check rdma_passive_info.json and rdma_passive_info.pickle for details
Enter filename with remote RDMA information, or Press Enter for default: 
```

### Start the active side 
Now repeat the same on a second terminal, with option `--client`
```
python rdma/helpers.py --debug --local-to rdma_active_info --client
```

At this point follow the instructions on the terminals to exchange control plane information. This is needed to connect the QP and prepare the QP in Ready to Send (RTS) mode. 
**NOTE** Do not try to proceed with reading the MR before both sides have successfully connected

```
Enter filename with remote RDMA information, or Press Enter for default: 
[INFO] 03 Apr 2025 13:37:50 helpers.py:377: Connecting to remote QP: 831, GID: 0000:0000:0000:0000:0000:ffff:0ac8:001c
[INFO] 03 Apr 2025 13:37:50 helpers.py:404: ✅ Queue Pair #0 (qp_num=832) connected to remote QP 831
```

### Periodic READ()

Now start reading
```
Enter MR filename or Press Enter to start RDMA reads...
[INFO] 03 Apr 2025 13:37:51 helpers.py:784: Loaded memory region info from mr_info.json, addr=0x559bf92ee020, rkey=2342331, size=4096
[INFO] 03 Apr 2025 13:37:51 helpers.py:102: Registered external memory region 'local_mr_0': addr=0x564543905020, rkey=2342074, size=4096
[INFO] 03 Apr 2025 13:37:51 helpers.py:507: Created local memory region: {'name': 'local_mr_0', 'addr': 94855486263328, 'rkey': 2342074, 'lkey': 2342074, 'size': 4096}
[INFO] 03 Apr 2025 13:37:51 helpers.py:563: Creating RDMA READ work request, remote_addr=0x559bf92ee020, rkey=2342331, length=4096
[INFO] 03 Apr 2025 13:37:51 helpers.py:576: Posted RDMA READ request: remote_addr=0x559bf92ee020, rkey=2342331, length=4096
[DEBUG] 03 Apr 2025 13:37:51 helpers.py:600: ✅ RDMA READ completed successfully: RDMA-MR-default
```



## TODO list
- Metrics have now fixed structure (only floating point support), this can be changed with a more flexible memory layout
- now you have some high level description of the code in the `.md` file, use that for next iterations (either here or in perplexity)
- how to manage threads in uView NIC
- try deploy on smart NIC
- saturation experiments:
  * uView max number of prometheus connections
  * RPC latency vs RPS when uview runs on host


## RDMA in Linux: primer

To list the available Channel Adapters (CA) and RDMA physical ports:
```
$> ibstat
CA 'mlx5_0'
        CA type: MT4129
        Number of ports: 1
        Firmware version: 28.42.1000
        Hardware version: 0
        Node GUID: 0x946dae030038817e
        System image GUID: 0x946dae030038817e
        Port 1:
                State: Down
                Physical state: Disabled
                Rate: 40
                Base lid: 0
                LMC: 0
                SM lid: 0
                Capability mask: 0x00010000
                Port GUID: 0x966daefffe38817e
                Link layer: Ethernet
..
```

### Netdev name to ibdev name
```
(uview) cornaca@mcnode28:[microview-cp]$ ibdev2netdev | grep ens1f1
mlx5_1 port 1 ==> ens1f1 (Up)
```

### Check the available GIDS to connect queue pairs
Pick the one with RoCEv2 and associated to the interface you want:

```
(uview) (base) cornaca@mcnode28:[microview-cp]$ show_gids 
DEV     PORT    INDEX   GID                                     IPv4            VER     DEV
---     ----    -----   ---                                     ------------    ---     ---
mlx5_0  1       0       fe80:0000:0000:0000:966d:aeff:fe38:817e                 v1      ens1f0
mlx5_0  1       1       fe80:0000:0000:0000:966d:aeff:fe38:817e                 v2      ens1f0
mlx5_1  1       0       fe80:0000:0000:0000:966d:aeff:fe38:817f                 v1      ens1f1
mlx5_1  1       1       fe80:0000:0000:0000:966d:aeff:fe38:817f                 v2      ens1f1
mlx5_1  1       2       0000:0000:0000:0000:0000:ffff:0ac8:001c 10.200.0.28     v1      ens1f1
mlx5_1  1       3       0000:0000:0000:0000:0000:ffff:0ac8:001c 10.200.0.28     v2      ens1f1
mlx5_2  1       0       fe80:0000:0000:0000:bace:f6ff:fe4d:cb1c                 v1      ens2f0
mlx5_2  1       1       fe80:0000:0000:0000:bace:f6ff:fe4d:cb1c                 v2      ens2f0
mlx5_2  1       2       0000:0000:0000:0000:0000:ffff:0ac8:0034 10.200.0.52     v1      ens2f0
mlx5_2  1       3       0000:0000:0000:0000:0000:ffff:0ac8:0034 10.200.0.52     v2      ens2f0
mlx5_3  1       0       fe80:0000:0000:0000:bace:f6ff:fe4d:cb1d                 v1      ens2f1
mlx5_3  1       1       fe80:0000:0000:0000:bace:f6ff:fe4d:cb1d                 v2      ens2f1
mlx5_4  1       0       fe80:0000:0000:0000:0ac0:ebff:fe15:3620                 v1      enp65s0f0np0
mlx5_4  1       1       fe80:0000:0000:0000:0ac0:ebff:fe15:3620                 v2      enp65s0f0np0
mlx5_5  1       0       fe80:0000:0000:0000:0ac0:ebff:fe15:3621                 v1      enp65s0f1np1
mlx5_5  1       1       fe80:0000:0000:0000:0ac0:ebff:fe15:3621                 v2      enp65s0f1np1
```


## Run instructions (outdated for C example)
Start agent on localhost running:
```
make
./agent
```

This will start microview agent at port 12345. Then run the following replacing `SERVER_ADDR` with the MicroView 
agent address. If MicroView runs on node use `host.docker.internal`:
```
docker run --rm --name pod --ipc=host --add-host=host.docker.internal:host-gateway acornacchia/ipc "./pod" "SERVER_ADDR"
```

The container should:
1. Open a TCP connection to the agent and ask for a shared memory segment
2. Start writing metrics to such a memory segment

On the other hand, MicroView agent:
1. allocates shared memory region and closes TCP connection
2. sends RDMA `R_key` to the microview agent counter part which sits on the SmartNIC

