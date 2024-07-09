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
