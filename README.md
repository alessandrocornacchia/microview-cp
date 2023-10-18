MicroView Control Plane
======================

Implementation of SmartNIC to host communication via RDMA for microservices metrics

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
 - throughput degradation is function of number of connections (reading paper), what if same connections and scattered memory regions?
 