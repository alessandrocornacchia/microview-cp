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

### Performance of metrics update

There's a significant advantage to using direct pointer access through `ctypes` as in `microview_client.py`, compared to an implementation using `shm.seek()` + `shm.read()`:

1. **Execution Speed**: Direct pointer manipulation is 5-10x faster than buffer I/O operations. For metrics that may be updated thousands of times per second, this is critical.

2. **CPU Efficiency**: Pointer dereferencing is a single CPU instruction, whereas seek/read/write operations involve multiple function calls, bounds checking, and buffer copying.

3. **Zero-Copy**: `ctype` implementation avoids unnecessary memory copies by directly modifying the value in-place, while seek/read creates intermediate buffers.

4. **Atomic Updates**: For numeric types like `float64`, pointer access provides atomic updates (essential for concurrent access).

```python
# very fast, single CPU instruction
value_ptr[0] = new_value

# seek/read approach - multiple operations, function call overhead
shm.buf.seek(offset)
shm.buf.write(struct.pack('d', new_value))
```

#### Specific to Metrics System

For a high-performance metrics system, this performance difference is crucial because:

1. Metrics are updated extremely frequently (potentially millions of times per second)
2. The overhead must be minimal to avoid impacting the application's performance
3. Updates are typically simple numeric changes to counters or gauges

The direct pointer implementation aligns perfectly with MicroView's goal of providing zero-overhead metric reporting, particularly for latency-sensitive microservices.

## Security and Trust Model

MicroView operates under a trusted domain model, where a single shared memory pool serves an entire microservice application. All pods within the application are assumed to belong to the same trust domain, which aligns with typical Kubernetes deployment patterns where an application's components share the same security context. This design choice optimizes for performance within a trusted environment, rather than enforcing isolation between components that are already part of the same security boundary.

The shared memory interface prioritizes performance, allowing direct memory access without validation checks. Memory separation between different applications is maintained by using distinct shared memory pools, preserving isolation at the application boundary while enabling efficient intra-application communication.

## RDMA Integration

For distributed metric collection, MicroView's host agent registers the shared memory region with RDMA-capable network interfaces, enabling remote collectors to directly access metrics via one-sided RDMA reads. This approach minimizes performance impact on application nodes while providing timely metric collection across the cluster. The continuous memory layout and byte-aligned structures optimize RDMA operations, allowing efficient bulk transfers of metric data.