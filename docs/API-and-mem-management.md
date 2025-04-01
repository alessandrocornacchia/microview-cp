# MicroView: High-Performance Metric Collection for Microservices

MicroView is a lightweight system for low-latency metric reporting and collection across microservice environments. It leverages shared memory and RDMA for near-zero-overhead metric updates without compromising application performance.

## Architecture and Memory Management

MicroView employs a centralized host agent (`microview_host_agent.py`) that manages a contiguous shared memory pool, partitioned into fixed-size 4KB pages. Each microservice application has its own dedicated shared memory pool. Our implementation features a flexible allocation strategy framework, with our reference implementation using a microservice-per-page approach. Each service receives a dedicated memory page containing multiple metric slots implemented as a structured byte-aligned array, optimized for RDMA operations.

Memory pages are continuously allocated to maximize RDMA efficiency, with each metric structured using a byte-aligned format supporting standard types (counters, gauges) with 64-byte name fields. The system enables direct pointer manipulation for metric updates, completely bypassing syscalls after initial registration.

## Client-Host Interaction

The interaction flow proceeds as follows:
1. Microservices initialize the MicroViewClient library with their service identifier
2. The client registers metrics via REST API calls to the host agent
3. The host agent allocates memory slots and returns offset information
4. The client maps the same shared memory segment and obtains direct pointers to metric values
5. Subsequent metric updates occur via direct memory manipulation, with zero communication overhead

```python
# Sample microservice metric reporting
client = MicroViewClient("auth-service")
requests = client.create_metric("http_requests_total", False, 0)
latency = client.create_metric("request_latency_ms", True, 0)

# Zero-overhead metric updates
requests.update_value(requests.get_value() + 1)
latency.update_value(calculate_latency())
```

## Security and Trust Model

MicroView operates under a trusted domain model, where a single shared memory pool serves an entire microservice application. All pods within the application are assumed to belong to the same trust domain, which aligns with typical Kubernetes deployment patterns where an application's components share the same security context. This design choice optimizes for performance within a trusted environment, rather than enforcing isolation between components that are already part of the same security boundary.

The shared memory interface prioritizes performance, allowing direct memory access without validation checks. Memory separation between different applications is maintained by using distinct shared memory pools, preserving isolation at the application boundary while enabling efficient intra-application communication.

## RDMA Integration

For distributed metric collection, MicroView's host agent registers the shared memory region with RDMA-capable network interfaces, enabling remote collectors to directly access metrics via one-sided RDMA reads. This approach minimizes performance impact on application nodes while providing timely metric collection across the cluster. The continuous memory layout and byte-aligned structures optimize RDMA operations, allowing efficient bulk transfers of metric data.