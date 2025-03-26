import numpy as np
import struct
import socket
import time
from typing import Dict, Any, Tuple, Optional

import pyverbs.device as d
import pyverbs.enums as e
from pyverbs.pd import PD
from pyverbs.mr import MR
from pyverbs.qp import QP, QPInitAttr, QPAttr
from pyverbs.cq import CQ
from pyverbs.addr import GID


class RDMACollector:
    def __init__(self, host_addr, rkey, remote_addr, length, port=18515, gid_index=0, 
                 qp_num=None, remote_gid=None):
        """
        Initialize an RDMA collector to read metrics from remote memory.
        
        Args:
            host_addr: IP address of the remote host
            rkey: Remote key for the memory region
            remote_addr: Remote memory region address
            length: Length of the memory region to read
            port: Port number for RDMA connection
            gid_index: GID index to use
            qp_num: Remote QP number
            remote_gid: Remote GID for RoCEv2
        """
        self.host_addr = host_addr
        self.rkey = rkey
        self.remote_addr = remote_addr
        self.length = length
        self.port = port
        self.gid_index = gid_index
        self.remote_qp_num = qp_num,
        self.remote_gid = remote_gid
        
        # RDMA connection components
        self.ctx = None
        self.pd = None
        self.cq = None
        self.qp = None
        self.mr = None
        self.buffer = None
        self.connected = False
        
        # Initialize RDMA connection
        self._init_rdma_connection()



    def _init_rdma_connection(self):
        """
        Initialize RDMA connection with remote host
        """
        try:
            # Open device context - use first available device
            devices = d.get_device_list()
            if not devices:
                raise RuntimeError("No RDMA devices found")
            
            self.ctx = d.Context(name=devices[0].name.decode())
            
            # Create Protection Domain
            self.pd = PD(self.ctx)
            
            # Create Completion Queue
            self.cq = CQ(self.ctx, 100)  # 100 is the CQ size
            
            # Create QP Init Attributes
            qp_init_attr = QPInitAttr(
                qp_type=e.IBV_QPT_RC,  # Reliable Connection
                sq_sig_all=1,  # Generate completion for all work requests
                cap=QPInitAttr.cap_param(max_send_wr=10, max_recv_wr=10, 
                                         max_send_sge=1, max_recv_sge=1),
                send_cq=self.cq,
                recv_cq=self.cq
            )
            
            # Create Queue Pair
            self.qp = QP(self.pd, qp_init_attr)
            
            # Allocate local buffer for RDMA read results
            self.buffer = bytearray(self.length)
            
            # Register Memory Region
            self.mr = MR(self.pd, self.buffer, 
                          e.IBV_ACCESS_LOCAL_WRITE | e.IBV_ACCESS_REMOTE_WRITE)
            
            # Connect to remote QP
            self._connect_to_remote()
            
            print(f"RDMA connection established to {self.host_addr}")
            self.connected = True
            
        except Exception as e:
            print(f"Error initializing RDMA connection: {e}")
            self._cleanup()
            raise
    
    

    def _connect_to_remote(self):

        """
        Establish connection with the remote QP
        """
        # Transition QP to INIT state
        attr = QPAttr()
        attr.qp_state = e.IBV_QPS_INIT
        attr.pkey_index = 0
        attr.port_num = 1
        attr.qp_access_flags = e.IBV_ACCESS_REMOTE_READ
        self.qp.modify(attr, e.IBV_QP_STATE | e.IBV_QP_PKEY_INDEX | 
                    e.IBV_QP_PORT | e.IBV_QP_ACCESS_FLAGS)
        
        # Get local QP attributes to exchange with remote
        local_qp_num = self.qp.qp_num
        local_lid = self.ctx.query_port(1).lid
        
        # For RoCEv2, get GID
        local_gid = self.ctx.query_gid(1, self.gid_index)
        
        # Transition QP to RTR (Ready to Receive) state
        attr = QPAttr()
        attr.qp_state = e.IBV_QPS_RTR
        attr.path_mtu = e.IBV_MTU_1024
        attr.dest_qp_num = self.remote_qp_num,
        attr.rq_psn = 0
        attr.max_dest_rd_atomic = 1
        attr.min_rnr_timer = 12
        
        # Set up primary path
        attr.ah_attr.is_global = 1
        attr.ah_attr.port_num = 1
        
        # For RoCEv2, set up GID-based routing using the provided function
        attr.ah_attr.grh.dgid = self.remote_gid
        attr.ah_attr.grh.sgid_index = self.gid_index
        attr.ah_attr.grh.hop_limit = 1
    
    
        self.qp.modify(attr, e.IBV_QP_STATE | e.IBV_QP_AV | e.IBV_QP_PATH_MTU | 
                      e.IBV_QP_DEST_QPN | e.IBV_QP_RQ_PSN | e.IBV_QP_MAX_DEST_RD_ATOMIC | 
                      e.IBV_QP_MIN_RNR_TIMER)
        
        # Transition QP to RTS (Ready to Send) state
        attr = QPAttr()
        attr.qp_state = e.IBV_QPS_RTS
        attr.timeout = 14
        attr.retry_cnt = 7
        attr.rnr_retry = 7
        attr.sq_psn = 0
        attr.max_rd_atomic = 1
        
        self.qp.modify(attr, e.IBV_QP_STATE | e.IBV_QP_TIMEOUT | e.IBV_QP_RETRY_CNT | 
                      e.IBV_QP_RNR_RETRY | e.IBV_QP_SQ_PSN | e.IBV_QP_MAX_QP_RD_ATOMIC)
    


   
    
    def read_metrics(self) -> bytearray:
        """
        Read metrics from remote memory using RDMA READ.
        
        Returns:
            bytearray: Buffer containing the read data
        """
        if not self.connected:
            raise RuntimeError("RDMA connection not established")
        
        try:
            # Post RDMA READ work request
            wr_id = 0x1234  # Arbitrary work request ID
            
            # Create scatter/gather element for the local buffer
            sge = struct.Struct("@QQQQ").pack(
                self.mr.buf, self.length, self.mr.lkey, 0
            )
            
            # Create work request for RDMA READ
            wr = struct.Struct("@QIQQQQQQ").pack(
                wr_id,                 # wr_id
                e.IBV_WR_RDMA_READ,    # opcode
                e.IBV_SEND_SIGNALED,   # send_flags
                0,                     # Next WR (none)
                self.remote_addr,      # Remote address
                self.rkey,             # Remote key
                self.mr.buf,           # Local address
                self.length            # Length
            )
            
            # Post the work request
            self.qp.post_send(wr, sge)
            
            # Wait for completion
            while True:
                wc = self.cq.poll()
                if wc:
                    if wc[0].status != e.IBV_WC_SUCCESS:
                        raise RuntimeError(f"RDMA READ failed with status: {wc[0].status}")
                    break
                time.sleep(0.001)  # Small sleep to avoid busy waiting
            
            # Return the data read from remote memory
            return self.buffer
            
        except Exception as e:
            print(f"Error reading metrics via RDMA: {e}")
            raise
    
    def _cleanup(self):
        """Clean up RDMA resources"""
        if self.qp:
            self.qp.close()
        if self.mr:
            self.mr.close()
        if self.cq:
            self.cq.close()
        if self.pd:
            self.pd.close()
        if self.ctx:
            self.ctx.close()
    
    def __del__(self):
        """Destructor to clean up resources"""
        self._cleanup()


# class RDMACollectorManager:
#     """
#     Manager for RDMA collectors that handles creation and tracking of collectors
#     for different services.
#     """
#     def __init__(self):
#         self.collectors = {}  # Map of service_id -> RDMAHostCollector
    
#     def register_collector(self, service_id: str, host_addr: str, rkey: int, 
#                           remote_addr: int, length: int, port: int = 18515):
#         """
#         Register a new RDMA collector for a service.
        
#         Args:
#             service_id: Unique identifier for the service
#             host_addr: IP address of the remote host
#             rkey: Remote key for the memory region
#             remote_addr: Remote memory region address
#             length: Length of the memory region to read
#             port: Port number for RDMA connection
#         """
#         if service_id in self.collectors:
#             print(f"Collector for service {service_id} already exists, replacing")
#             # Clean up existing collector
#             del self.collectors[service_id]
        
#         # Create new collector
#         self.collectors[service_id] = RDMAHostCollector(
#             host_addr, rkey, remote_addr, length, port
#         )
        
#         return self.collectors[service_id]
    
#     def get_collector(self, service_id: str) -> Optional[RDMAHostCollector]:
#         """Get a collector by service ID"""
#         return self.collectors.get(service_id)
    
#     def read_metrics(self, service_id: str) -> Optional[bytearray]:
#         """Read metrics for a specific service"""
#         collector = self.get_collector(service_id)
#         if collector:
#             return collector.read_metrics()
#         return None
    
#     def deregister_collector(self, service_id: str):
#         """Remove a collector for a service"""
#         if service_id in self.collectors:
#             del self.collectors[service_id]
    
#     def __del__(self):
#         """Clean up all collectors"""
#         for collector in self.collectors.values():
#             del collector
#         self.collectors.clear()



"""
For unit test only
"""
if __name__ == "__main__":
    try:
        # Mock functions for QP exchange and remote GID
        def mock_exchange_qp_info(local_qp_num, local_lid, local_gid):
            print(f"Mock QP exchange: Local QP={local_qp_num}, Local LID={local_lid}")
            return 12345  # Mock remote QP number
        
        def mock_get_remote_gid():
            gid = GID()
            # Set mock GID values
            gid_raw = bytearray(16)
            gid_raw[0] = 0xfe
            gid_raw[1] = 0x80
            gid.gid_raw = gid_raw
            return gid
        
        # Create a mock collector with the mock functions
        print("Creating RDMAHostCollector...")
        collector = RDMACollector(
            host_addr="192.168.1.100",  # Mock host address
            rkey=0x12345678,            # Mock remote key
            remote_addr=0x1000,         # Mock remote address
            length=64,                  # Mock buffer length
            qp_num=mock_exchange_qp_info(),
            remote_gid=mock_get_remote_gid()
        )

        print("Connection successful...")

        # Read metrics
        print("Reading metrics...")
        buffer = collector.read_metrics()
        
        # Parse metrics
        metrics = struct.unpack_from('Qdd', buffer)
        print(f"Metrics read: requests={metrics[0]}, latency_total={metrics[1]}, count={metrics[2]}")
        
        # Calculate average latency
        avg_latency = metrics[1] / metrics[2] if metrics[2] > 0 else 0
        print(f"Average latency: {avg_latency:.6f} seconds")
        
    except Exception as e:
        print(f"Test failed: {e}")
