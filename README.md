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
