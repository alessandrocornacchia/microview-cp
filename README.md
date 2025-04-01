MicroView Control Plane
======================

An implementation SmartNIC-to-host communication via RDMA for microservices metrics.

The description of the project can be found in our published research paper:

[MicroView: Cloud-Native Observability with Temporal Precision](https://dl.acm.org/doi/10.1145/3630202.3630233), ACM CONeXT-SW '23  
*Alessandro Cornacchia, Theophilus A. Benson, Muhammad Bilal, and Marco Canini*

<div align="center">
<img src="./uview-microservices.png" alt="drawing" width="500"/>
</div>
<p></p>

## Run instructions
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

## Python prototype run instructions

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

## TODO list
- Shared memory should be one large block, where RDMA memory regions are 4KB size and contiguous (done)
- How to handle close(). MicroView client should probably close nothing: shared memmory is managed by the uView agent
and deleting metrics make little sense (when pod disappear, it's page will just be released entirely)


## RDMA in Linux: primer

To list the available Channel Adapters (CA):
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

