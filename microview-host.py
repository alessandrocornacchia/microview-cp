import multiprocessing
import multiprocessing.resource_tracker
import os
import time
import threading
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, Tuple
import ctypes
import numpy as np
from flask import Flask, request, jsonify
from rdma.helpers import MemoryRegionPool, QueuePairPool
from rdma.cm_server import RDMAPassiveServer
from multiprocessing import shared_memory
from metrics import *
from utils import peek_shared_memory
from defaults import *
from pyverbs.pd import PD

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/microview_host_agent.log')
    ]
)
logger = logging.getLogger('MicroviewHostAgent')

class AllocationStrategy(ABC):
    @abstractmethod
    def allocate_metric(self, microservice_id: str, metric_name: str, metric_type: bool, initial_value: float) -> Tuple[str, int]:
        pass

    @abstractmethod
    def deallocate_metric(self, microservice_id: str, metric_name: str) -> bool:
        pass


class MetricsMemoryManager(AllocationStrategy):
    """
        Strategy that allocates one page per microservice. 
        All metrics are registered there
    """
        
    def __init__(self, page_size: int = DEFAULT_PAGE_SIZE, mr_size: int = DEFAULT_RDMA_MR_SIZE):
        
        self.page_size = page_size
        self.mr_size = mr_size
        
        # Calculate how many metrics fit in a page
        item_size = np.dtype(metric_dtype).itemsize
        self.metrics_per_page = page_size // item_size
        logger.info(f"Each page can hold {self.metrics_per_page} metrics of size {item_size} bytes")
        
        # Calculate how many pages we can fit
        self.max_pages = SHM_POOL_SIZE // page_size
        logger.info(f"Shared memory pool can hold {self.max_pages} pages")
        
        # Allocate shared memory for the entire application
        try:
            self.shm = shared_memory.SharedMemory(create=True, size=SHM_POOL_SIZE, name=SHM_POOL_NAME)
            logger.info(f"Created shared memory with name {self.shm.name} and size {SHM_POOL_SIZE} bytes")
        except Exception as e:
            logger.error(f"Error creating shared memory: {e}")
            raise
        
        # Track which pages are allocated
        self.allocated_pages = 0
        
        #TODO all this must be parameteric
        self.num_mr = SHM_POOL_SIZE // mr_size
        
        # Memory region pool
        self.mr_pool = None  
        # Maps pod_id to control info of page
        self.page2pod : Dict[MetricsPage, str] = {}  
        # Maps pod_id to list of pages
        self.pod2pages: Dict[str, list[MetricsPage]] = {}
        # Memory region layout (MR -> pages)
        self.mr_pages = [[] for _ in range(self.num_mr)]
        # TODO unused for single shared memory segment
        self.shm_blocks = {"microview-demo": self.shm}  
    
    
    def add_rdma_memory_regions(self, pd: PD):
        """
        Add RDMA memory regions to the shared memory
        """
        # Create memory regions
        self.mr_pool = MemoryRegionPool(pd)
        
        # TODO these parameters should be configurable with fallback to default value
        if self.mr_size % self.page_size != 0:
            raise ValueError(f"RDMA MR size {self.mr_size} must be a multiple of page size {self.page_size}")
        
        base_addr = self.get_shm_base_addr()

        for i in range(self.num_mr):
            mr_info = self.mr_pool.register_memory_region(
                name=f"RDMA-MR-{i}",
                addr=base_addr + (i * self.mr_size),
                size=self.mr_size)
    

    def _create_new_page(self, pod_id: str) -> MetricsPage:
         # Check if we have pages available
        if self.allocated_pages >= self.max_pages:
            logger.error("No more shared memory pages available")
            raise ValueError("No more shared memory pages available")
        
        # Calculate page offset in the shared memory
        page_offset = self.allocated_pages * self.page_size

        # Create metrics page object
        metrics_page = MetricsPage(buffer = self.shm.buf, 
                                   size = self.metrics_per_page,
                                   offset = page_offset)
        
        self.page2pod[metrics_page] = pod_id
        self.pod2pages.setdefault(pod_id, []).append(metrics_page)
        
        # append the newly created page to the memory region layout
        # NOTE this assumes that the page is aligned with the RDMA MR size,
        # all pages are the same size and the allocation strategy is sequential
        # i.e., fill the first MR, then the second, etc.
        mr_idx = page_offset // self.mr_size
        logger.debug(f"Adding page to memory region {mr_idx} at offset {page_offset}")
        self.mr_pages[mr_idx].append(metrics_page)

        # Increment allocated pages counter
        self.allocated_pages += 1
        logger.info(f"Created page at offset {page_offset} in shm {self.shm.name} for microservice '{pod_id}'")

        return metrics_page


    def get_page_for_pod(self, pod_id: str) -> MetricsPage:
        """
        Returns first non full page for this pod_id, or None if no page is available (i.e., all full)
        """
        if pod_id not in self.pod2pages:
            return None
        
        # Get the list of pages for this pod
        pages = self.pod2pages[pod_id]
        
        # Check if any page is available
        for page in pages:
            if not page.is_full():
                return page
        
        return None

    def allocate_metric(self, pod_id: str, metric_name: str, metric_type: bool, initial_value: float) -> Tuple[str, int]:
        """
        Allocate a new metric for a microservice
        
        Returns:
            Tuple[str, int]: Shared memory name and pointer to the value field
        """
        
        try:
            # either we already have the page, or we we created it
            metrics_page = self.get_page_for_pod(pod_id)
            if not metrics_page:
                metrics_page = self._create_new_page(pod_id)
            
            value_address_offset = metrics_page.add_metric(metric_name, metric_type, initial_value)    
            logger.debug(f"Added metrics to page with address {metrics_page.addr}, @ offset {value_address_offset}")

        except:
            logger.error(f"Error registering metric {metric_name} for microservice {pod_id}")
            raise
        
        return value_address_offset
    

    def deallocate_metric(self, microservice_id: str, metric_name: str) -> bool:
        """ This allocation strategy does not support deallocation of individual metrics.
            Metrics will be just released when the page is released.
        """
        pass

    
    def get_shm_base_addr(self) -> int:
        """ Get the base address of the shared memory segment """
        return peek_shared_memory(self.shm, 0)

    
    def cleanup(self):
        """ Cleanup shared memory and allocated pages """
        if self.mr_pool:
            # Unregister memory regions
            self.mr_pool.cleanup()
        try:
            # Close the shared memory
            logger.debug(f"Cleaning shared memory {self.shm.name}")
            self.shm.close()
            self.shm.unlink()
        except Exception as e:
            logger.error(f"Error cleaning up shared memory: {e}")
        
        # Clear the page2pod
        self.page2pod.clear()
        # Clear the pod2pages
        self.pod2pages.clear()
        self.allocated_pages = 0


class MicroviewHostAgent:
    def __init__(self, num_rdma_qps: int = 0, host: str = "0.0.0.0", port: int = 5000):
        
        self.start_rdma = num_rdma_qps > 0    # useful for debug
        self.num_qps = num_rdma_qps
        self.host = host
        self.api_port = port
        self.qp_pool = None
        
        self.mem_mgmt = MetricsMemoryManager()

        self.app = Flask(__name__)
        self.setup_routes()


    def setup_routes(self):
        @self.app.route('/metrics', methods=['POST'])
        def create_metric():
            data = request.json
            logger.debug(f"Received create_metric request: {data}")
            required_fields = ['microservice_id', 'name', 'type', 'value']
            for field in required_fields:
                if field not in data:
                    logger.warning(f"Missing required field: {field}")
                    return jsonify({"error": f"Missing required field: {field}"}), 400

            try:
            
                metric_type = bool(data['type'])
                microservice_name = data['microservice_id']
                addr_offset = self.mem_mgmt.allocate_metric(
                    microservice_name,
                    data['name'],
                    metric_type,
                    float(data['value'])
                )
                logger.info(f"Created metric '{data['name']}' for pod '{data['microservice_id']}': shm_name={self.mem_mgmt.shm.name}, value_field_offset={addr_offset}")
                return jsonify({"shm_name": self.mem_mgmt.shm.name, "addr": addr_offset})
            
            except ValueError as e:
                logger.warning(f"ValueError in create_metric: {str(e)}")
                return jsonify({"error": str(e)}), 400
            except Exception as e:
                logger.error(f"Exception in create_metric: {str(e)}", exc_info=True)
                return jsonify({"error": f"Failed to create metric: {str(e)}"}), 500
    
        @self.app.route('/metrics', methods=['GET'])
        def get_memory_layout():
            if not self.mem_mgmt:
                return jsonify({"error": "RDMA memory not yet ready"}), 503
            # TODO this logic should be moved to the memory manager, it's get_control_region logic...and correspondigly, it can be used
            # by the microview nic.
            res = []
            # iterate over MRs
            for mr_id in range(len(self.mem_mgmt.mr_pages)):
                control_region = []
                # iterate over pages in the MR
                logger.debug(f"Memory region {mr_id} has {len(self.mem_mgmt.mr_pages[mr_id])} pages")
                for metrics_page in self.mem_mgmt.mr_pages[mr_id]:
                    pod_id = self.mem_mgmt.page2pod[metrics_page]
                    page_occupancy = metrics_page.num_entries
                    # return control-info to the caller   
                    control_region.append({
                        "pod_id": pod_id,
                        "pages": page_occupancy,
                        "page_size_bytes": self.mem_mgmt.page_size,
                    })
                res.append(control_region)

            return jsonify(res)

        @self.app.route('/health', methods=['GET'])
        def health_check():
            logger.debug("Received health_check request")
            return jsonify({
                "status": "healthy",
            })

    def _setup_rdma_control_routes(self):
        """Add API routes for controlling the RDMA server"""
        
        @self.app.route('/rdma/qps', methods=['GET'])
        def get_queue_pairs():
            """Get all queue pairs"""
            if not self.qp_pool:
                return jsonify({"error": "RDMA server not running"}), 503
                
            qps = self.qp_pool.list_queue_pairs()
            return jsonify({"queue_pairs": qps})
        
        @self.app.route('/rdma/qp/<int:index>', methods=['GET'])
        def get_queue_pair_info(index):
            """Get connection info for a specific queue pair"""
            if not self.qp_pool:
                return jsonify({"error": "RDMA server not running"}), 503
                
            try:
                qp_info = self.qp_pool.get_queue_pair_info(index)
                return jsonify({"queue_pair": qp_info})
            except Exception as e:
                return jsonify({"error": str(e)}), 400
        
        @self.app.route('/rdma/qp/<int:index>/connect', methods=['POST'])
        def connect_queue_pair(index):
            """Connect a queue pair to a remote QP"""
            if not self.qp_pool:
                return jsonify({"error": "RDMA server not running"}), 503
                
            data = request.json
            if not data or "remote_info" not in data:
                return jsonify({"error": "Missing remote_info in request body"}), 400
                
            try:
                success = self.qp_pool.connect_queue_pair(index, data["remote_info"])
                if success:
                    return jsonify({"status": "connected"})
                else:
                    return jsonify({"error": "Failed to connect queue pair"}), 500
            except Exception as e:
                return jsonify({"error": str(e)}), 400

        @self.app.route('/rdma/qps/connect', methods=['POST'])
        def connect_all_queue_pairs():
            """Connect all queue pairs to remote QPs in a single operation"""
            
            if not self.qp_pool:
                return jsonify({"error": "RDMA pool not running"}), 503
                
            data = request.json.get("queue_pairs", [])
            logger.debug(f"Received connect_all_queue_pairs request: {data}")

            results = []
            try:
                # Process each remote QP in the list
                for i, remote_qp_info in enumerate(data):
                    if i < len(self.qp_pool.qps):    
                            
                        # Connect this queue pair
                        success = self.qp_pool.connect_queue_pair(i, remote_qp_info)
                        
                        if success:
                            results.append({
                                "index": i,
                                "status": "connected",
                                "qp_num": self.qp_pool.qps[i]["qp_num"]
                            })
                        else:
                            results.append({
                                "index": i,
                                "status": "error",
                                "message": "Failed to connect queue pair"
                            })
                
                # Overall status
                success_count = sum(1 for r in results if r["status"] == "connected")
                if success_count > 0:
                    return jsonify({
                        "status": "partial_success" if success_count < len(data) else "success",
                        "connected": success_count,
                        "total": len(data),
                        "results": results
                    })
                else:
                    return jsonify({
                        "status": "failed",
                        "message": "Failed to connect any queue pairs",
                        "results": results
                    }), 500
                    
            except Exception as e:
                logger.error(f"Error connecting multiple queue pairs: {e}")
                return jsonify({"error": str(e)}), 400
        
        @self.app.route('/rdma/mrs', methods=['GET'])
        def get_memory_regions():
            """Get all memory regions"""
            if not self.mem_mgmt.mr_pool:
                return jsonify({"error": "RDMA MR not yet initialized"}), 503
                
            mrs = self.mem_mgmt.mr_pool.list_memory_regions()
            logger.debug(f"Returning memory regions: {mrs}")
            return jsonify({"memory_regions": mrs})
        
        @self.app.route('/rdma/mrs/create', methods=['POST'])
        def create_memory_region():
            """Create a new memory region"""
            if not self.mem_mgmt.mr_pool:
                return jsonify({"error": "RDMA MR not yet initialized"}), 503
                        
            return jsonify({"error": "Not implemented yet"}), 400
    
    # TODO move to separate class or delete
    # This is when it uses the RDMA CM abstraction to start a server
    # def start_rdma_server(self):
    #     logger.info(f"Starting RDMA server on port {self.rdma_port}")
    #     self.qp_pool = RDMAPassiveServer(port=self.rdma_port)

    #     def run_server():
    #         try:
    #             self.qp_pool.start()
    #         except Exception as e:
    #             logger.error(f"RDMA server error: {str(e)}", exc_info=True)

    #     self.rdma_thread = threading.Thread(target=run_server)
    #     self.rdma_thread.daemon = True
    #     self.rdma_thread.start()
    #     time.sleep(1)
    #     logger.info("RDMA server started")

    
    def _init_rdma(self, qp_pool_size : int, rdma_device : str):
        
        if not self.start_rdma:
            logger.info("RDMA server not started. Set start_rdma flag to True")
            return

        # Add API routes for controlling the RDMA server
        self._setup_rdma_control_routes()
        
        try:
            
            # Initialize QP pool
            self.qp_pool = QueuePairPool(rdma_device, pool_size=qp_pool_size)
            logger.info(f"Created QP pool with size {qp_pool_size}")

            # Tell memory manager now can create memory regions
            self.mem_mgmt.add_rdma_memory_regions(self.qp_pool.pd)
                        
            logger.info(f"✅ RDMA initialized correctly")
                
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            self.cleanup()
            raise
            

    
    def start(self, rdma_device: str = DEFAULT_RDMA_DEVICE):
        """Start the Microview Host Agent"""
        try:
            self._init_rdma(self.num_qps, rdma_device)
            logger.info(f"Starting REST API on {self.host}:{self.api_port}")
            self.app.run(host=self.host, port=self.api_port)
        except KeyboardInterrupt:
            logger.info("Shutting down due to keyboard interrupt")
        except Exception as e:
            logger.error(f"Error in start: {str(e)}", exc_info=True)
        finally:
            self.cleanup()

    def cleanup(self):
        logger.info("Cleaning up resources")
        if self.qp_pool:
            self.qp_pool.cleanup()
        if self.mem_mgmt:
            self.mem_mgmt.cleanup()
        logger.info("Cleanup completed")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Microview Host Agent")
    parser.add_argument("--rdma-queues", "-q", type=int, default=DEFAULT_QP_POOL_SIZE, 
                        help="Number of RDMA queues to use. Set to 0 to disable RDMA server")
    parser.add_argument("--host", default="0.0.0.0", help="API host")
    parser.add_argument("--port", type=int, default=5000, help="API port")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")

    agent = MicroviewHostAgent(
        num_rdma_qps=args.rdma_queues,
        host=args.host,
        port=args.port,
    )

    agent.start()
    