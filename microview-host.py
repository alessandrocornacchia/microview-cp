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
from rdma.helpers import MemoryRegionManager, QueuePairPool
from rdma.cm_server import RDMAPassiveServer
from multiprocessing import shared_memory
from metrics import *
from utils import get_ptr_in_shm
from defaults import *

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

# This is configurable, but 800KB shared memory assumes applications with:
#   100 pods x 100 metrics x 80 bytes
SHM_POOL_SIZE = 800 * 1024
SHM_POOL_NAME = "microview"
DEFAULT_RDMA_MR_SIZE = 64 * 1024  # 64KB maximum size for RDMA read groups
DEFAULT_QP_POOL_SIZE = 1

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
    
    # Constants
    PAGE_SIZE = 4096
        
    def __init__(self):
        
        # Calculate how many metrics fit in a page
        item_size = np.dtype(metric_dtype).itemsize
        self.metrics_per_page = MetricsMemoryManager.PAGE_SIZE // item_size
        logger.info(f"Each page can hold {self.metrics_per_page} metrics of size {item_size} bytes")
        
        # Calculate how many pages we can fit
        self.max_pages = SHM_POOL_SIZE // MetricsMemoryManager.PAGE_SIZE
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
        
        # Initialize registry
        self.registry = {}  # Maps microservice_id to page info
        self.shm_blocks = {"microview-demo": self.shm}  # For cleanup # TODO this can be avoided..
    
    
    def _create_new_page(self, microservice_id: str) -> MetricsPage:
         # Check if we have pages available
        if self.allocated_pages >= self.max_pages:
            logger.error("No more shared memory pages available")
            raise ValueError("No more shared memory pages available")
        
        # Calculate page offset in the shared memory
        page_offset = self.allocated_pages * MetricsMemoryManager.PAGE_SIZE
        
        # Create numpy array view for this page
        array = np.ndarray(
            (self.metrics_per_page,),
            dtype=metric_dtype,
            buffer=self.shm.buf,
            offset=page_offset
        )
        
        # Create metrics page wrapper
        metrics_page = MetricsPage(array, self.metrics_per_page, page_offset)
        
        # Register the microservice page
        self.registry[microservice_id] = metrics_page
                    
        # Increment allocated pages counter
        self.allocated_pages += 1
        logger.info(f"Created page at offset {page_offset} in shm {self.shm.name} for microservice '{microservice_id}'")

        return metrics_page

    
    def allocate_metric(self, microservice_id: str, metric_name: str, metric_type: bool, initial_value: float) -> Tuple[str, int]:
        """
        Allocate a new metric for a microservice
        
        Returns:
            Tuple[str, int]: Shared memory name and pointer to the value field
        """
        
        try:
            # --- either we already have the page, or we we created it
            if microservice_id not in self.registry:
                metrics_page = self._create_new_page(microservice_id)
            # Get the page info for this microservice
            metrics_page = self.registry[microservice_id]
            
            value_address_offset = metrics_page.add_metric(metric_name, metric_type, initial_value)    
            logger.debug(f"Allocated metric, value offset {value_address_offset} in shared memory")

        except:
            logger.error(f"Error registering metric {metric_name} for microservice {microservice_id}")
            raise
        
        return value_address_offset
    

    def deallocate_metric(self, microservice_id: str, metric_name: str) -> bool:
        """ This allocation strategy does not support deallocation of individual metrics.
            Metrics will be just released when the page is released.
        """
        pass

    
    def get_shm_base_addr(self) -> int:
        """ Get the base address of the shared memory segment """
        return get_ptr_in_shm(self.shm, 0)

    
    def cleanup(self):
        """ Cleanup shared memory and allocated pages """
        logger.info("Cleaning up shared memory and allocated pages")
        try:
            # Close the shared memory
            self.shm.close()
            self.shm.unlink()
            logger.info(f"Shared memory {self.shm.name} cleaned up")
        except Exception as e:
            logger.error(f"Error cleaning up shared memory: {e}")
        
        # Clear the registry
        self.registry.clear()
        self.allocated_pages = 0


class MicroviewHostAgent:
    def __init__(self, start_rdma: bool = False, rdma_port: str = "18515", host: str = "0.0.0.0", port: int = 5000):
        
        self.start_rdma = start_rdma    # useful for debug
        self.host = host
        self.api_port = port
        self.qp_pool = None
        
        self.mem_mgmt = MetricsMemoryManager()
        self.mr_mgmt = None

        self.app = Flask(__name__)
        self.setup_routes()
        
        logger.info(f"MicroviewHostAgent initialized with RDMA port {rdma_port} and HTTP port {port}")

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
                microservice_name = data['microservice_id'] + f"-{int(time.time())}"
                addr_offset = self.mem_mgmt.allocate_metric(
                    microservice_name,
                    data['name'],
                    metric_type,
                    float(data['value'])
                )
                logger.info(f"Created metric '{data['name']}' for microservice '{data['microservice_id']}': shm_name={self.mem_mgmt.shm.name}, index={addr_offset}")
                return jsonify({"shm_name": self.mem_mgmt.shm.name, "addr": addr_offset})
            
            except ValueError as e:
                logger.warning(f"ValueError in create_metric: {str(e)}")
                return jsonify({"error": str(e)}), 400
            except Exception as e:
                logger.error(f"Exception in create_metric: {str(e)}", exc_info=True)
                return jsonify({"error": f"Failed to create metric: {str(e)}"}), 500

        @self.app.route('/metrics', methods=['GET'])
        def get_memory_layout():
            """
            Return the layour with which microservices metrics are stored in shared memory
            This endpoint provides visibility into which microservices have registered
            and how many metrics each one has.
            """
            logger.debug("Received get_metrics_registry request")
            
            # Create a serializable version of the registry
            registry_json = {}
            
            # For each microservice and its page in the registry
            for microservice_id, metrics_page in self.mem_mgmt.registry.items():
                # Include only the number of entries for each page
                registry_json[microservice_id] = {
                    "page_offset": metrics_page.page_offset,
                    "num_entries": metrics_page.num_entries,
                    "max_metrics": metrics_page.max_metrics
                }
            
            logger.debug(f"Returning registry with {len(registry_json)} microservices")
            return jsonify(registry_json)

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
            if not self.qp_pool:
                return jsonify({"error": "RDMA server not running"}), 503
                
            mrs = self.qp_pool.mr_manager.list_memory_regions()
            return jsonify({"memory_regions": mrs})
        
        @self.app.route('/rdma/mr', methods=['POST'])
        def create_memory_region():
            """Create a new memory region"""
            if not self.qp_pool:
                return jsonify({"error": "RDMA server not running"}), 503
                
            data = request.json
            if not data or "name" not in data:
                return jsonify({"error": "Missing name in request body"}), 400
                
            try:
                size = data.get("size", None)
                mr_info = self.qp_pool.mr_manager.register_memory_region(data["name"], size)
                return jsonify({"memory_region": mr_info})
            except Exception as e:
                return jsonify({"error": str(e)}), 400
    
    
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

    
    def _init_rdma(self, qp_pool_size=DEFAULT_QP_POOL_SIZE):
        
        # Add API routes for controlling the RDMA server
        self._setup_rdma_control_routes()
        
        try:
            
            # Initialize QP pool
            self.qp_pool = QueuePairPool(DEFAULT_RDMA_DEVICE, pool_size=qp_pool_size)
            logger.info(f"Created QP pool with size {qp_pool_size}")

            # Create memory regions
            self.mr_mgmt = MemoryRegionManager(self.qp_pool.pd)
            
            # TODO these parameters should be configurable with fallback to default value
            if DEFAULT_RDMA_MR_SIZE % MetricsMemoryManager.PAGE_SIZE != 0:
                raise ValueError(f"RDMA MR size {DEFAULT_RDMA_MR_SIZE} must be a multiple of page size {MetricsMemoryManager.PAGE_SIZE}")
            
            num_mr = SHM_POOL_SIZE // DEFAULT_RDMA_MR_SIZE
            base_addr = self.mem_mgmt.get_shm_base_addr()

            for i in range(num_mr):
                mr_info = self.mr_mgmt.register_memory_region(
                    name=f"RDMA-MR-{i}",
                    addr=base_addr + (i * DEFAULT_RDMA_MR_SIZE),
                    size=DEFAULT_RDMA_MR_SIZE)
                        
            logger.info(f"✅ RDMA initialized correctly")
                
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            self.cleanup()
            raise
            

    
    def start(self):
        try:
            if self.start_rdma:
                self._init_rdma()
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
        if self.mr_mgmt:
            self.mr_mgmt.cleanup()
        if self.mem_mgmt:
            self.mem_mgmt.cleanup()
        logger.info("Cleanup completed")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Microview Host Agent")
    parser.add_argument("--rdma-port", default="18515", help="RDMA server port")
    parser.add_argument("--host", default="0.0.0.0", help="API host")
    parser.add_argument("--port", type=int, default=5000, help="API port")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")

    agent = MicroviewHostAgent(
        start_rdma=True,
        rdma_port=args.rdma_port,
        host=args.host,
        port=args.port
    )

    try:
        agent.start()
    except KeyboardInterrupt:
        logger.info("Exiting due to keyboard interrupt")
    finally:
        agent.cleanup()