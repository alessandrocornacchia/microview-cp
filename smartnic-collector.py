import struct
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily

import sys
sys.modules['rdma'] = __import__('mock_rdma')

import rdma  # Import your RDMA library

# Rest of your test script
class RDMAHostCollector:
    def __init__(self, host_addr, rkey):
        self.rdma_conn = rdma.connect_mr(host_addr, rkey)  # Your RDMA library
        
    def collect(self):
        # Single RDMA READ for all metrics
        buf = self.rdma_conn.read(offset=0, length=64)
        metrics = struct.unpack_from('Qdd', buf)  # Match host struct
        
        req_count = CounterMetricFamily(
            'http_requests_total', 
            'Total requests', 
            labels=['host']
        )
        req_count.add_metric(['host1'], metrics[0])
        yield req_count
        
        latency_avg = GaugeMetricFamily(
            'request_latency_seconds_avg',
            'Average latency',
            value=metrics[1]/metrics[2]
        )
        yield latency_avg


# Your main function
if __name__ == '__main__':
    from prometheus_client import start_http_server
    from prometheus_client.core import REGISTRY
    import time

    # Register your custom collector
    REGISTRY.register(RDMAHostCollector('your_smartnic_address', "mock_rkey"))
    
    # Start up the server to expose the metrics
    start_http_server(8000)
    print("Metrics available at http://localhost:8000/metrics")
    
    # Keep the script running
    while True:
        time.sleep(1)
