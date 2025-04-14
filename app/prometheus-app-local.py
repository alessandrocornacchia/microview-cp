from prometheus_client import start_http_server, Summary
from flask import Flask
import random
import time
import threading
import requests

app = Flask(__name__)

# Create a metric to track time spent and requests made.
REQUEST_TIME = Summary('request_processing_seconds', 'Time spent processing request')

# Decorate function with metric.
@REQUEST_TIME.time()
@app.route('/process')
def process_request():
    """An HTTP endpoint that simulates processing time."""
    time.sleep(random.random())
    return {"status": "success"}

def workload_generator():
    """Generate synthetic workload by sending requests to the /process endpoint."""
    # Wait for the Flask server to start
    time.sleep(2)
    while True:
        try:
            response = requests.get('http://localhost:8080/process')
            if response.status_code == 200:
                print("Request successful")
            time.sleep(random.uniform(0.1, 1.0))  # Random delay between requests
        except requests.exceptions.RequestException as e:
            print(f"Error sending request: {e}")
            time.sleep(1)  # Wait before retrying on error

if __name__ == '__main__':
    # Start up the Prometheus server to expose the metrics.
    start_http_server(8000)
    print("Prometheus metrics available at http://localhost:8000/metrics")
    
    # Start the workload generator in a separate thread
    workload_thread = threading.Thread(target=workload_generator, daemon=True)
    workload_thread.start()
    
    # Start the Flask application
    print("API endpoint available at http://localhost:8080/process")
    app.run(host='0.0.0.0', port=8080)