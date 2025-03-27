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


## TODO list
- Measure latency and throughput on the following scenarios:
    1. many MR registered on same connection, to read metrics agent should access all of them
    2. single RDMA read of a vector

- Define page size e.g., 4KB (~ 2000 metrics), number of containers (this will determine number of memory regions)
 (actual content is not important...).

DOUBTS:
 - Does RDMA work completion for READ means that READ has been issued or that we alredy have data available? 
 - are RDMA read processed in order, ok, means next read is sent when previous read has rx data or pipelined? 
 - throughput degradation is function of number of connections (reading paper), what if same connections + scattered memory regions? Expect same performance as single connection.
 - investigate what happens when do not create a new context but reuse the same over all connections... you basicaly map the same completion queue/WQ to 
  different connections so they both rx read request and respond ??? is message sent to queue pair or to all queue pairs on the destination host??

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

