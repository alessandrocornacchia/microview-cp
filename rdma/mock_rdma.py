# mock_rdma.py
class MockMemoryRegion:
    def read(self, offset, length):
        # Return mock data that matches your expected format
        import struct
        # Mock values: 100 requests, 150ms total latency, 100 count
        return struct.pack('Qdd', 100, 0.15, 100)

def connect_mr(host_addr, rkey):
    return MockMemoryRegion()
