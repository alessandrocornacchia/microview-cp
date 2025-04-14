import argparse
import logging
from libmicroview import MicroViewClient, MicroViewMetric, logger

parser = argparse.ArgumentParser(description="MicroView Client Example")
parser.add_argument("--host", type=str, default="localhost", help="Host where the MicroViewHostAgent is running")
parser.add_argument("--port", type=int, default=5000, help="Port where the MicroViewHostAgent API is exposed")    
parser.add_argument("--debug", action="store_true", help="Enable debug logging")
parser.add_argument("--num-metrics", "-m", type=int, default=2, help="Number of metrics to create")
parser.add_argument("--update-metrics", action="store_true", help="Update metrics every 10 seconds")


args = parser.parse_args()

if args.debug:
    logger.setLevel(logging.DEBUG)
    logger.debug("Debug logging enabled")

# Create a MicroView client
client = MicroViewClient("example-service", host=args.host, port=args.port)

try:
    
    num_metrics = min(1,int(args.num_metrics/2))
    # create num_metrics metrics
    for i in range(num_metrics):
        
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
        
        import time
        time.sleep(10)
except KeyboardInterrupt:            
    pass
finally:
    # Clean up
    client.close()