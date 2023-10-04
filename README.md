MicroView Control Plane
======================

Implementation of SmartNIC to host communication via RDMA for microservices metrics

Start agent on localhost running:
```
make
./agent
```

This will start microview agent at port 12345. Then run:
```
docker run -it --rm --name pod --ipc=host --add-host=host.docker.internal:host-gateway acornacchia/ipc
```

If everything works, the newly created container should:
1. Open a TCP connection to the agent and ask for a shared mmeory segment
2. Start writing metrics to such a memory segment at high-frequency

On the other hand, MicroView agent:
1. allocated shared memory region and closes TCP connection
2. sends RDMA `R_key` to the microview agent which sits on the SmartNIC
