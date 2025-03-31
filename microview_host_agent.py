import os
import time
import threading
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, Tuple

import numpy as np
from flask import Flask, request, jsonify
from rdma.rdma_passive import RDMAPassiveServer
from multiprocessing import shared_memory
from metrics import metric_dtype

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('microview_host_agent.log')
    ]
)
logger = logging.getLogger('MicroviewHostAgent')

# This is configurable, but 800KB shared memory assumes applications with:
#   100 pods x 100 metrics x 80 bytes
SHM_POOL_SIZE = 800000


class AllocationStrategy(ABC):
    @abstractmethod
    def allocate_metric(self, microservice_id: str, metric_name: str, metric_type: bool, initial_value: float) -> Tuple[str, int]:
        pass

    @abstractmethod
    def deallocate_metric(self, microservice_id: str, metric_name: str) -> bool:
        pass


class MetricsPage:
    """Wrapper class for a page of metrics in shared memory"""
    def __init__(self, array, max_metrics):
        self.array = array  # Numpy array for this page
        self.max_metrics = max_metrics  # Max metrics this page can hold
        self.num_entries = 0  # Current number of metrics
        self.page_offset = 0  # Offset in shared memory
        # self.metrics = []  # List of metric metadata (name, etc.)


class MicroservicePageStrategy(AllocationStrategy):
    """
        Strategy that allocates one page per microservice. 
        All metrics are registered there
    """
    def __init__(self):
        # Constants
        self.PAGE_SIZE = 4096
        
        # Calculate how many metrics fit in a page
        item_size = np.dtype(metric_dtype).itemsize
        self.metrics_per_page = self.PAGE_SIZE // item_size
        logger.info(f"Each page can hold {self.metrics_per_page} metrics of size {item_size} bytes")
        
        # Calculate how many pages we can fit
        self.max_pages = SHM_POOL_SIZE // self.PAGE_SIZE
        logger.info(f"Shared memory pool can hold {self.max_pages} pages")
        
        # Allocate shared memory for the entire application
        self.shm = shared_memory.SharedMemory(create=True, size=SHM_POOL_SIZE, name="microview-demo")
        
        # Track which pages are allocated
        self.allocated_pages = 0
        
        # Initialize registry
        self.registry = {}  # Maps microservice_id to page info
        self.shm_blocks = {"microview-demo": self.shm}  # For cleanup
    
    
    def _create_new_page(self, microservice_id: str) -> MetricsPage:
         # Check if we have pages available
        if self.allocated_pages >= self.max_pages:
            logger.error("No more shared memory pages available")
            raise ValueError("No more shared memory pages available")
        
        # Calculate page offset in the shared memory
        page_offset = self.allocated_pages * self.PAGE_SIZE
        
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
        logger.info(f"Created page at offset {page_offset} for microservice '{microservice_id}'")

        return metrics_page

    
    def allocate_metric(self, microservice_id: str, metric_name: str, metric_type: bool, initial_value: float) -> Tuple[str, int]:
        """
        Allocate a new metric for a microservice
        
        Returns:
            Tuple[str, int]: Shared memory name and pointer to the value field
        """
        
        # --- either we already have the page, or we we created it
        if microservice_id not in self.registry:
           self._create_new_page(microservice_id)
        
        
        # Get the page info for this microservice
        metrics_page = self.registry[microservice_id]
        
        # Check if the page is full
        if metrics_page.num_entries >= metrics_page.max_metrics:
            logger.error(f"Maximum metrics ({metrics_page.max_metrics}) reached for microservice {microservice_id}")
            raise ValueError(f"Maximum metrics ({metrics_page.max_metrics}) reached for microservice {microservice_id}")
        
        # Add the new metric
        next_index = metrics_page.num_entries
        metrics_page.array[next_index] = (metric_name.encode('utf-8'), metric_type, initial_value)
        
        # Increment the number of entries
        metrics_page.num_entries += 1
        
        # Get the direct memory address of the value field
        # First get the address of the record
        record_address = metrics_page.array[next_index].ctypes.data
        # Add the offset to the value field
        value_offset = metric_dtype.fields['value'][1]
        value_address = record_address + value_offset
        
        logger.debug(f"Created metric '{metric_name}' at address {hex(value_address)} for microservice '{microservice_id}'")
        
        return int(value_address)
    


# class MicroservicePageStrategy(AllocationStrategy):
#     """ This strategy just allocates one moemory page (4KB) per microservice """
#     def __init__(self):

#         # allocate shared memory for the entire application
#         self.shm = shared_memory.SharedMemory(create=True, size=SHM_POOL_SIZE, name="microview-demo")

#         self.registry = {}  
        
#         # buckets for metrics
#         self.metrics_buckets = {}
#         self.metrics_per_page = SHM_POOL_SIZE // np.dtype(metric_dtype).itemsize
#         logger.info(f"Each page can hold {self.metrics_per_page} metrics")

#     def allocate_metric(self, microservice_id: str, metric_name: str, metric_type: bool, initial_value: float) -> Tuple[str, int]:
#         if microservice_id not in self.registry:
#             shm_name = f"microview-{microservice_id}-{int(time.time())}"
            
#             array = np.ndarray((self.metrics_per_page,), dtype=metric_dtype, buffer=shm.buf)
#             # TODO think if this data structure is what we want
#             self.registry[shm_name] = {
#                 "metrics": []
#             }
#             self.shm_blocks[shm_name] = shm
#             self.metrics_buckets[shm_name] = array
#             logger.info(f"Created new shared memory page '{shm_name}' for microservice '{microservice_id}'")

#         page_info = self.registry[microservice_id]
#         shm_name = page_info["shm_name"]
#         metrics = page_info["metrics"]

#         if len(metrics) >= self.metrics_per_page:
#             logger.error(f"Maximum metrics ({self.metrics_per_page}) reached for microservice {microservice_id}")
#             raise ValueError(f"Maximum metrics ({self.metrics_per_page}) reached for microservice {microservice_id}")

#         if any(m["name"] == metric_name for m in metrics):
#             logger.warning(f"Metric '{metric_name}' already exists for microservice '{microservice_id}'")
#             raise ValueError(f"Metric '{metric_name}' already exists for microservice '{microservice_id}'")

#         array = self.metrics_buckets[shm_name]
#         next_index = len(metrics)
#         array[next_index] = (metric_name.encode('utf-8'), metric_type, initial_value)
#         metrics.append({"name": metric_name, "index": next_index})
#         logger.debug(f"Created metric '{metric_name}' at index {next_index} for microservice '{microservice_id}'")

#         return shm_name, next_index

    def deallocate_metric(self, microservice_id: str, metric_name: str) -> bool:
        # if microservice_id not in self.registry:
        #     logger.warning(f"Microservice '{microservice_id}' not found in registry")
        #     return False

        # page_info = self.registry[microservice_id]
        # metrics = page_info["metrics"]

        # metric_idx = next((i for i, m in enumerate(metrics) if m["name"] == metric_name), None)
        # if metric_idx is None:
        #     logger.warning(f"Metric '{metric_name}' not found for microservice '{microservice_id}'")
        #     return False

        # metrics.pop(metric_idx)
        # logger.info(f"Deallocated metric '{metric_name}' for microservice '{microservice_id}'")

        # if not metrics:
        #     shm_name = page_info["shm_name"]
        #     if shm_name in self.shm_blocks:
        #         self.shm_blocks[shm_name].close()
        #         self.shm_blocks[shm_name].unlink()
        #         del self.shm_blocks[shm_name]
        #         del self.arrays[shm_name]
        #         logger.info(f"Cleaned up shared memory '{shm_name}' for microservice '{microservice_id}'")
        #     del self.registry[microservice_id]

        # return True
        pass


class MicroviewHostAgent:
    def __init__(self, start_rdma: bool = False, rdma_port: str = "18515", host: str = "0.0.0.0", port: int = 5000):
        self.start_rdma = start_rdma
        self.rdma_port = rdma_port
        self.host = host
        self.api_port = port
        self.rdma_server = None
        self.rdma_thread = None
        self.allocation_strategy = MicroservicePageStrategy()
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
                shm_name, index = self.allocation_strategy.allocate_metric(
                    data['microservice_id'],
                    data['name'],
                    metric_type,
                    float(data['value'])
                )
                logger.info(f"Created metric '{data['name']}' for microservice '{data['microservice_id']}': shm_name={shm_name}, index={index}")
                return jsonify({"shm_name": shm_name, "index": index})
            except ValueError as e:
                logger.warning(f"ValueError in create_metric: {str(e)}")
                return jsonify({"error": str(e)}), 400
            except Exception as e:
                logger.error(f"Exception in create_metric: {str(e)}", exc_info=True)
                return jsonify({"error": f"Failed to create metric: {str(e)}"}), 500

        @self.app.route('/delete', methods=['POST'])
        def delete_metric():
            data = request.json
            logger.debug(f"Received delete_metric request: {data}")
            required_fields = ['microservice_id', 'name']
            for field in required_fields:
                if field not in data:
                    logger.warning(f"Missing required field: {field}")
                    return jsonify({"error": f"Missing required field: {field}"}), 400

            try:
                success = self.allocation_strategy.deallocate_metric(data['microservice_id'], data['name'])
                if success:
                    logger.info(f"Deleted metric '{data['name']}' for microservice '{data['microservice_id']}'")
                    return jsonify({"status": "ok"})
                else:
                    logger.warning(f"Metric '{data['name']}' not found for microservice '{data['microservice_id']}'")
                    return jsonify({"error": f"Metric '{data['name']}' not found for microservice '{data['microservice_id']}'"}), 404
            except Exception as e:
                logger.error(f"Exception in delete_metric: {str(e)}", exc_info=True)
                return jsonify({"error": f"Failed to delete metric: {str(e)}"}), 500

        @self.app.route('/health', methods=['GET'])
        def health_check():
            logger.debug("Received health_check request")
            return jsonify({
                "status": "healthy",
                "rdma_server_running": self.rdma_server is not None and hasattr(self.rdma_server, 'running') and self.rdma_server.running
            })

    def start_rdma_server(self):
        logger.info(f"Starting RDMA server on port {self.rdma_port}")
        self.rdma_server = RDMAPassiveServer(port=self.rdma_port)

        def run_server():
            try:
                self.rdma_server.start()
            except Exception as e:
                logger.error(f"RDMA server error: {str(e)}", exc_info=True)

        self.rdma_thread = threading.Thread(target=run_server)
        self.rdma_thread.daemon = True
        self.rdma_thread.start()
        time.sleep(1)
        logger.info("RDMA server started")

    def start(self):
        try:
            if self.start_rdma:
                self.start_rdma_server()
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
        if self.rdma_server:
            self.rdma_server.cleanup()
        if isinstance(self.allocation_strategy, MicroservicePageStrategy):
            for shm_name, shm in self.allocation_strategy.shm_blocks.items():
                try:
                    shm.close()
                    shm.unlink()
                except Exception as e:
                    logger.error(f"Error cleaning up shared memory '{shm_name}': {str(e)}")
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