import numpy as np
import requests
from multiprocessing import shared_memory
from typing import Dict, Optional, Union, Any
import ctypes 
import logging
from utils import open_untracked_shared_memory, peek_shared_memory


# >>>>>> PATCH not to let multiprocessing clean up shared memory
# This is a workaround to prevent the multiprocessing library from cleaning up shared memory
import multiprocessing.resource_tracker
original_register = multiprocessing.resource_tracker.register
def patched_register(name, rtype):
    if rtype == "shared_memory":
        # Ignore shared memory registrations
        return
    return original_register(name, rtype)
multiprocessing.resource_tracker.register = patched_register
# <<<<<< PATCH


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/microview_client.log')
    ]
)
logger = logging.getLogger('MicroViewClient')

class MicroViewMetric:
    """
    A class representing a single metric in the MicroView system.
    Abstracts away the details of shared memory access.
    """

    METRIC_TYPE_COUNTER = 0
    METRIC_TYPE_GAUGE = 1

    def __init__(self, value_ptr: int, metric_name: str, metric_type: bool):
        """
        Initialize a MicroViewMetric.
        
        Args:
            shm_name: Name of the shared memory segment
            metric_name: Name of the metric
            metric_type: Type of metric (False=counter, True=gauge)
        """
        self.value_ptr = ctypes.cast(value_ptr, ctypes.POINTER(ctypes.c_double))
        self.metric_name = metric_name
        self.metric_type = metric_type
        

    def update_value(self, value: float) -> None:
        """
        Update the value of this metric in shared memory.
        
        Args:
            value: New value to set
        """
        # self.array[self.index]['value'] = value
        # Update the value
        self.value_ptr[0] = value
        
    def get_value(self) -> float:
        """
        Get the current value of this metric.
        
        Returns:
            The current value of the metric
        """
        # return float(self.array[self.index]['value'])
        return self.value_ptr[0]

class MicroViewClient:
    """
    Client library for creating and managing metrics in the MicroView system.
    """
    def __init__(self, microservice_id: str, host: str = "localhost", port: int = 5000):
        """
        Initialize the MicroView client.
        
        Args:
            microservice_id: Identifier for the microservice
            host: Host where the MicroViewHostAgent is running
            port: Port where the MicroViewHostAgent API is exposed
        """
        self.microservice_id = microservice_id
        self.base_url = f"http://{host}:{port}"
        self.metrics = {}  # Store created metrics for reference
        self.shm = None  # Shared memory object, which will be populated at runtime
        

    def create_metric(self, name: str, metric_type: bool = False, initial_value: float = 0.0) -> MicroViewMetric:
        """
        Create a new metric in the MicroView system.
        
        Args:
            name: Name of the metric
            metric_type: Type of metric (False=counter, True=gauge)
            initial_value: Initial value for the metric
            
        Returns:
            A MicroViewMetric object for updating the metric
        """
        # Check if metric already exists locally
        if name in self.metrics:
            return self.metrics[name]
            
        # Create the payload for the API request
        payload = {
            "microservice_id": self.microservice_id,
            "name": name,
            "type": metric_type,
            "value": initial_value
        }
        
        # Make the API request to create the metric
        try:
            response = requests.post(f"{self.base_url}/metrics", json=payload)
            response.raise_for_status()  # Raise exception for HTTP errors
            
            # Parse the response
            result = response.json()
            shm_name = result["shm_name"]
            value_ptr_offset = int(result["addr"])  # Pointer to the value field
            
            # Get access to the shared memory object
            if not self.shm:
                # self.shm = open_untracked_shared_memory(shm_name)
                self.shm = shared_memory.SharedMemory(name=shm_name)
            value_ptr = peek_shared_memory(self.shm, value_ptr_offset)

            # Create a MicroViewMetric object
            metric = MicroViewMetric(
                value_ptr=value_ptr,
                metric_name=name,
                metric_type=metric_type
            )
            
            # Store the metric for reference
            self.metrics[name] = metric
            logger.info(f"Created metric '{name}' with type {metric_type} and initial value {initial_value}")

            return metric
            
        except requests.RequestException as e:
            raise ConnectionError(f"Failed to create metric: {e}")
                    
    def close(self) -> None:
        """
        Close all shared memory resources.
        """
        # for metric in self.metrics.values():
        #     metric.close()
        # self.metrics.clear()
        if self.shm:
            self.shm.close()
            self.shm = None
            logger.info("Closed shared memory resources")
        
    def __del__(self):
        """
        Clean up resources when the client is garbage collected.
        """
        self.close()


# Example usage: create two metrics with MicroView API and update their value 10 times
if __name__ == "__main__":
    
    import argparse
    import time

    parser = argparse.ArgumentParser(description="MicroView Client Example")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--num-metrics", "-m", type=int, default=2, help="Number of metrics to create")
    parser.add_argument("--update-metrics", action="store_true", help="Update metrics every 10 seconds")


    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")

    # Create a MicroView client, with unique name hasing the current nanosecond timestamp 
    client = MicroViewClient("example-service-" + str(hash(time.time_ns())))
    
    try:
        
        # create num_metrics metrics
        for i in range(int(args.num_metrics/2)):
            
            # Create a counter metric
            requests_metric = client.create_metric(f"http_requests_total_{i}", MicroViewMetric.METRIC_TYPE_COUNTER, 0)
            
            # Create a gauge metric
            latency_metric = client.create_metric(f"http_request_latency_{i}", MicroViewMetric.METRIC_TYPE_GAUGE, 0.0)
        
        # Update the metrics 10 times
        i = 0
        while True:
            
            if args.update_metrics:
                requests_metric.update_value(i)
                latency_metric.update_value(i * 0.1)
                i += 1

                logger.info(f"Requests: {requests_metric.get_value()}, Latency: {latency_metric.get_value()}")
            
            time.sleep(10)
    except KeyboardInterrupt:            
        pass
    finally:
        # Clean up
        client.close()