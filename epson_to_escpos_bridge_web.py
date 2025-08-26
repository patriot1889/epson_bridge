#!/usr/bin/env python3
"""
Epson TM-m30 to ESC/POS Bridge with Web Interface

- Emulates an Epson TM-m30 printer (receives ESC/POS data)
- Converts ESC/POS data to PNG
- Forwards the data to a real ESC/POS printer
- Runs both the emulator and printer connection continuously
- Serves a web interface for reprinting jobs

Requirements:
- python-escpos
- Pillow

Usage:
    python3 epson_to_escpos_bridge.py --printer-ip 192.168.1.100
"""
import os
import threading
import time
import queue
import argparse
import socket
import logging
from datetime import datetime
from escpos.printer import Network, Usb
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import urllib.parse

# Set up logging
def setup_logging():
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"epson_bridge_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    
    # Remove any existing handlers to avoid duplicates
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    
    # Create formatters and handlers
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    
    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    # Configure root logger
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(file_handler)
    logging.root.addHandler(console_handler)
    
    # Log startup message
    logging.info(f"Logging initialized. Log file: {log_file}")

setup_logging()

# Import Epson emulator and graphics converter logic
from mix import epson_emulator
from mix import escpos_graphics_converter

class WebInterface(BaseHTTPRequestHandler):
    def __init__(self, bridge_instance, *args, **kwargs):
        self.bridge = bridge_instance
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """Override to add custom logging"""
        if self.path != '/api/status':  # Skip logging for status endpoint
            logging.info(f"[WebServer] {format % args}")

    def do_GET(self):
        """Handle GET requests"""
        try:
            if self.path == '/':
                self.serve_main_page()
            elif self.path == '/api/status':
                self.serve_status()
            elif self.path.startswith('/static/'):
                self.serve_static_file()
            elif self.path.startswith('/print_jobs/') and self.path.endswith('.png'):
                self.serve_image()
            else:
                self.send_error(404)
        except Exception as e:
            logging.error(f"[WebServer] GET error for {self.path}: {e}")
            self.send_error(500)
            
    def do_OPTIONS(self):
        """Handle OPTIONS requests for CORS preflight"""
        try:
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Content-Length', '0')
            self.end_headers()
        except Exception as e:
            print(f"[WebServer] OPTIONS error: {e}")
            self.send_error(500)

    def do_POST(self):
        """Handle POST requests"""
        try:
            if self.path == '/api/reprint':
                self.handle_reprint()
            else:
                self.send_error(404)
        except Exception as e:
            print(f"[WebServer] POST error for {self.path}: {e}")
            self.send_error(500)

    def serve_main_page(self):
        """Serve the main HTML page"""
        html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Epson Printer Bridge</title>
    <link rel="icon" type="image/x-icon" href="/static/favicon.ico">
    <link rel="apple-touch-icon" href="/static/favicon.ico">
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            text-align: center;
            margin-bottom: 30px;
        }
        .status {
            background: #e8f5e8;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
            border-left: 4px solid #4caf50;
        }
        .print-job {
            background: #f9f9f9;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
            border-left: 4px solid #2196f3;
        }
        .button {
            background-color: #4caf50;
            color: white;
            padding: 12px 24px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
            margin: 5px;
            transition: background-color 0.3s;
        }
        .button:hover {
            background-color: #45a049;
        }
        .button:disabled {
            background-color: #cccccc;
            cursor: not-allowed;
        }
        .button.reprint {
            background-color: #2196f3;
        }
        .button.reprint:hover {
            background-color: #1976d2;
        }
        .message {
            padding: 10px;
            margin: 10px 0;
            border-radius: 5px;
            display: none;
        }
        .success {
            background-color: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .error {
            background-color: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .preview-image {
            max-width: 100%;
            border: 1px solid #ddd;
            border-radius: 5px;
            margin-top: 10px;
        }
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid #f3f3f3;
            border-top: 3px solid #3498db;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-right: 10px;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        .uptime {
            color: #666;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Epson Printer Bridge Control</h1>

        <div class="print-job">
            <h3>Last Print Job</h3>
            <p id="last-job-info">No print jobs yet</p>
            <button id="reprint-btn" class="button reprint" onclick="reprintLastJob()" disabled>
                <span id="reprint-loading" class="loading" style="display: none;"></span>
                Re-print Last Job
            </button>
            <div id="job-preview"></div>
        </div>
        
        <div class="status">
            <h3>Status</h3>
            <p><strong>Boot Time:</strong> <span id="boot-time">Loading...</span></p>
            <p><strong>Uptime:</strong> <span id="uptime" class="uptime">Loading...</span></p>
            <p><strong>Emulator IP:</strong> <span id="emulator-ip">Loading...</span></p>
            <p><strong>Printer:</strong> <span id="printer-info">Loading...</span></p>
            <p><strong>Jobs Processed:</strong> <span id="jobs-count">0</span></p>
            <button class="button" onclick="refreshStatus()">Refresh Status</button>
        </div>

        <div id="message" class="message"></div>
    </div>

    <script>
        let lastJobData = null;

        function formatUptime(seconds) {
            const days = Math.floor(seconds / 86400);
            const hours = Math.floor((seconds % 86400) / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            const secs = Math.floor(seconds % 60);
            
            let result = '';
            if (days > 0) result += `${days}d `;
            if (hours > 0) result += `${hours}h `;
            if (minutes > 0) result += `${minutes}m `;
            result += `${secs}s`;
            
            return result;
        }

        function showMessage(text, type) {
            const messageDiv = document.getElementById('message');
            messageDiv.textContent = text;
            messageDiv.className = `message ${type}`;
            messageDiv.style.display = 'block';
            setTimeout(() => {
                messageDiv.style.display = 'none';
            }, 5000);
        }

        function refreshStatus() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('emulator-ip').textContent = data.emulator_ip || 'Not available';
                    document.getElementById('printer-info').textContent = data.printer_info || 'Not connected';
                    document.getElementById('jobs-count').textContent = data.jobs_processed || 0;
                    
                    // Update boot time and uptime
                    if (data.boot_time) {
                        document.getElementById('boot-time').textContent = new Date(data.boot_time * 1000).toLocaleString();
                    }
                    if (data.uptime) {
                        document.getElementById('uptime').textContent = formatUptime(data.uptime);
                    }
                    
                    if (data.last_job) {
                        lastJobData = data.last_job;
                        document.getElementById('last-job-info').innerHTML = 
                            `<strong>Timestamp:</strong> ${new Date(data.last_job.timestamp * 1000).toLocaleString()}<br>
                             <strong>Size:</strong> ${data.last_job.size} bytes`;
                        
                        // Show preview image if available
                        if (data.last_job.preview_path) {
                            const preview = document.getElementById('job-preview');
                            preview.innerHTML = `<img src="${data.last_job.preview_path}" class="preview-image" alt="Print preview">`;
                        }
                        
                        document.getElementById('reprint-btn').disabled = false;
                    } else {
                        document.getElementById('last-job-info').textContent = 'No print jobs yet';
                        document.getElementById('job-preview').innerHTML = '';
                        document.getElementById('reprint-btn').disabled = true;
                    }
                })
                .catch(error => {
                    console.error('Error fetching status:', error);
                    showMessage('Error fetching status', 'error');
                });
        }

        function reprintLastJob() {
            if (!lastJobData) {
                showMessage('No print job available to reprint', 'error');
                return;
            }

            const button = document.getElementById('reprint-btn');
            const loading = document.getElementById('reprint-loading');
    
            button.disabled = true;
            loading.style.display = 'inline-block';

            console.log('Sending reprint request...');

            fetch('/api/reprint', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({})
            })
            .then(response => {
                console.log('Response status:', response.status);
                console.log('Response headers:', response.headers);
        
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
        
                return response.json();
            })
            .then(data => {
                console.log('Response data:', data);
                if (data.success) {
                    showMessage('Print job queued successfully!', 'success');
                } else {
                    showMessage(data.message || 'Error reprinting job', 'error');
                }
            })
            .catch(error => {
                console.error('Fetch error details:', error);
                if (error.name === 'TypeError' && error.message.includes('fetch')) {
                    showMessage('Network error - check if server is running', 'error');
                } else if (error.message.includes('HTTP error')) {
                    showMessage(`Server error: ${error.message}`, 'error');
                } else {
                    showMessage(`Error: ${error.message}`, 'error');
                }
            })
            .finally(() => {
                button.disabled = false;
                loading.style.display = 'none';
            });
        }

        // Refresh status on page load and every 5 seconds
        refreshStatus();
        setInterval(refreshStatus, 5000);
    </script>
</body>
</html>
        """
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def serve_status(self):
        """Serve status information as JSON"""
        try:
            status = {
                'boot_time': self.bridge.boot_time,
                'uptime': time.time() - self.bridge.boot_time,
                'emulator_ip': self.bridge.get_emulator_ip(),
                'printer_info': self.bridge.get_printer_info(),
                'jobs_processed': self.bridge.jobs_processed,
                'last_job': self.bridge.get_last_job_info()
            }
            
            response_data = json.dumps(status).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Content-Length', len(response_data))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(response_data)
            
        except Exception as e:
            logging.error(f"[WebServer] Error serving status: {e}")
            self.send_error(500)

    def serve_static_file(self):
        """Serve static files from static directory"""
        filename = os.path.basename(self.path)
        filepath = os.path.join('static', filename)
        
        if os.path.exists(filepath):
            try:
                with open(filepath, 'rb') as f:
                    self.send_response(200)
                    if filename.endswith('.ico'):
                        self.send_header('Content-type', 'image/x-icon')
                    self.send_header('Cache-Control', 'public, max-age=31536000')
                    self.end_headers()
                    self.wfile.write(f.read())
            except Exception as e:
                logging.error(f"[WebServer] Error serving static file: {e}")
                self.send_error(500)
        else:
            self.send_error(404)

    def serve_image(self):
        """Serve image files from print_jobs directory"""
        filename = os.path.basename(self.path)
        filepath = os.path.join('print_jobs', filename)
        
        if os.path.exists(filepath) and filename.endswith('.png'):
            try:
                with open(filepath, 'rb') as f:
                    self.send_response(200)
                    self.send_header('Content-type', 'image/png')
                    self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
                    self.send_header('Pragma', 'no-cache')
                    self.send_header('Expires', '0')
                    self.end_headers()
                    self.wfile.write(f.read())
            except Exception as e:
                self.send_error(500)
        else:
            self.send_error(404)

    def handle_reprint(self):
        """Handle reprint request"""
        try:
            logging.info("[WebServer] Received reprint request")
            
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                post_data = self.rfile.read(content_length)
                logging.debug(f"[WebServer] Request body: {post_data}")
            
            success = self.bridge.reprint_last_job()
            response = {
                'success': success,
                'message': 'Job queued for printing' if success else 'No job available to reprint'
            }
            
            logging.info(f"[WebServer] Reprint response: {response}")
            
        except Exception as e:
            logging.error(f"[WebServer] Reprint error: {e}")
            response = {
                'success': False,
                'message': f'Error: {str(e)}'
            }
        
        # Send response
        try:
            response_data = json.dumps(response).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Content-Length', len(response_data))
            self.send_header('Access-Control-Allow-Origin', '*')  # Add CORS header
            self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()
            self.wfile.write(response_data)
        except Exception as e:
            logging.error(f"[WebServer] Error sending response: {e}")

class BridgeEpsonToESCPOS:
    def __init__(self, format="image", printer_ip=None, printer_port=9100, vendor_id=None, product_id=None, web_port=8080):
        self.print_queue = queue.Queue()
        self.printer_ip = printer_ip
        self.printer_port = printer_port
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.emulator = None
        self.running = True
        self.format = format
        self.connection_type = "network" if printer_ip else "usb"
        self.web_port = web_port
        self.jobs_processed = 0
        self.last_job_data = None
        self.last_job_timestamp = None
        self.last_job_preview = None
        self.boot_time = time.time()  # Record boot time
        self.emulator_ip = None  # Initialize emulator_ip

    def get_local_ip(self):
        """Get the local IP address of this machine"""
        try:
            # Connect to a dummy address to determine local IP
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    def get_emulator_ip(self):
        """Get the emulator IP address safely"""
        if self.emulator_ip:
            return self.emulator_ip
        elif self.emulator and hasattr(self.emulator, 'ip') and self.emulator.ip:
            return self.emulator.ip
        else:
            return self.get_local_ip()

    def on_print_job_complete(self, escpos_data):
        """Callback from Epson emulator when a print job is received"""
        logging.info("[Bridge] Received print job, queueing...")
        self.last_job_data = escpos_data
        self.last_job_timestamp = time.time()
        self.jobs_processed += 1
        
        # Generate preview PNG
        try:
            preview_path = escpos_graphics_converter.generate_merged_bitmap_png(
                escpos_data, output_dir="print_jobs", debug=False
            )
            if preview_path:
                # Rename the file to include timestamp
                timestamp = int(time.time())
                new_filename = f"preview_{timestamp}.png"
                new_path = os.path.join('print_jobs', new_filename)
                os.rename(preview_path, new_path)
                self.last_job_preview = f"/print_jobs/{new_filename}"
                logging.info(f"[Bridge] Preview generated: {new_path}")
                # Clean up old preview files
                try:
                    for old_file in os.listdir('print_jobs'):
                        if old_file.startswith('preview_') and old_file != new_filename:
                            os.remove(os.path.join('print_jobs', old_file))
                except Exception as e:
                    logging.error(f"[Bridge] Error cleaning up old previews: {e}")
        except Exception as e:
            logging.error(f"[Bridge] Error generating preview: {e}")
            self.last_job_preview = None
        
        self.print_queue.put(escpos_data)

    def get_printer_info(self):
        """Get printer connection information"""
        if self.connection_type == "network":
            return f"Network ({self.printer_ip}:{self.printer_port})"
        else:
            return f"USB (VID: {self.vendor_id:04x}, PID: {self.product_id:04x})"

    def get_last_job_info(self):
        """Get information about the last print job"""
        if not self.last_job_data:
            return None
        
        return {
            'timestamp': self.last_job_timestamp,
            'size': len(self.last_job_data),
            'preview_path': self.last_job_preview
        }

    def reprint_last_job(self):
        """Queue the last job for reprinting"""
        if not self.last_job_data:
            return False
        
        logging.info("[Bridge] Reprinting last job...")
        self.print_queue.put(self.last_job_data)
        return True

    def start_web_server(self):
        """Start the web server"""
        def create_handler(*args, **kwargs):
            return WebInterface(self, *args, **kwargs)
    
        def run_server():
            try:
                server = HTTPServer(('0.0.0.0', self.web_port), create_handler)
                server.timeout = 1  # Set timeout for serve_forever
                web_ip = self.get_emulator_ip()
                logging.info(f"[Bridge] Web interface available at http://{web_ip}:{self.web_port}")
            
                # Use serve_forever() but check running flag periodically
                while self.running:
                    server.handle_request()
                
            except Exception as e:
                logging.error(f"[Bridge] Web server error: {e}")
    
        self.web_thread = threading.Thread(target=run_server, daemon=True)
        self.web_thread.start()

    def stop(self):
        """Gracefully stop the bridge"""
        logging.info("[Bridge] Stopping...")
        self.running = False

    def start_emulator(self, ip=None, mac=None, netmask=None, gateway=None):
        """Start the Epson emulator in a thread, with print job callback"""
        def run_emulator():
            # Patch the emulator to use our callback
            class PatchedEpsonTM30Emulator(epson_emulator.EpsonTM30Emulator):
                def on_print_job_complete(self_inner, escpos_data):
                    self.on_print_job_complete(escpos_data)
            
            # Use provided IP or get local IP
            if ip:
                self.emulator_ip = ip
            else:
                self.emulator_ip = self.get_local_ip()
            
            # Create and start emulator
            emulator = PatchedEpsonTM30Emulator(self.emulator_ip, mac, netmask, gateway)
            self.emulator = emulator
            emulator.start()
            
        t = threading.Thread(target=run_emulator, daemon=True)
        t.start()

    def start_printer_worker(self):
        """Start a thread that keeps the ESC/POS printer connected and prints from the queue"""
        def run_worker():
            while self.running:
                try:
                    # Create printer connection based on connection type
                    if self.connection_type == "network":
                        printer = Network(
                            host=self.printer_ip,
                            port=self.printer_port,
                            timeout=5
                        )
                        logging.info(f"[Bridge] Connected to ESC/POS printer at {self.printer_ip}")
                    else:  # USB connection
                        printer = Usb(
                            self.vendor_id,
                            self.product_id
                        )
                        logging.info(f"[Bridge] Connected to USB ESC/POS printer (VID: {self.vendor_id:04x}, PID: {self.product_id:04x})")
                    
                    # Set printer capabilities
                    printer.profile.media_width_pixel = 576  # Standard width for 80mm thermal printers

                    # Print ready message with boot time
                    connection_method = f"Network ({self.printer_ip})" if self.connection_type == "network" else f"USB (VID: {self.vendor_id:04x}, PID: {self.product_id:04x})"
                    boot_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.boot_time))
                    ready_message = f"Connected to printer via {connection_method}.\n"
                    ready_message += f"Boot time: {boot_time_str}\n"
                    emulator_ip = self.get_emulator_ip()
                    ready_message += f"Emulator running at {emulator_ip}\n"
                    ready_message += f"Web interface: http://{emulator_ip}:{self.web_port}\n"
                    printer.text(ready_message)
                    printer.cut()
                    logging.info("[Bridge] Printed ready message")

                    while self.running:
                        try:
                            escpos_data = self.print_queue.get(timeout=1)
                        except queue.Empty:
                            time.sleep(0.1)
                            continue

                        logging.info(f"[Bridge] Printing received data using {self.format} format...")
                        try:
                            if self.format == "image":
                                # Use the same preview image for printing
                                png_path = self.last_job_preview.replace('/print_jobs/', 'print_jobs/')
                                if png_path and os.path.exists(png_path):
                                    logging.info(f"[Bridge] Using preview PNG for printing: {png_path}")
                                    # Initialize the printer
                                    printer.text("\n")  # Start with a clean line
                                    # Print the image from the PNG file
                                    printer.image(png_path)
                                    # Cut the paper
                                    printer.cut()
                            elif self.format == "raw":
                                # Convert hex string to bytes
                                raw_data = bytes.fromhex(escpos_data)
                                # Send raw ESC/POS commands directly
                                printer._raw(raw_data)
                                logging.info("[Bridge] Raw ESC/POS data sent")
                            
                            logging.info("[Bridge] Print job completed")
                        except Exception as e:
                            logging.error(f"[Bridge] Error printing: {e}")
                except Exception as e:
                    logging.error(f"[Bridge] Printer connection error: {e}")
                    time.sleep(10)  # Wait before retrying connection

        t = threading.Thread(target=run_worker, daemon=True)
        t.start()

    def run(self, ip=None, mac=None, netmask=None, gateway=None):
        # Ensure print_jobs directory exists
        os.makedirs("print_jobs", exist_ok=True)
        
        boot_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.boot_time))
        logging.info(f"[Bridge] Starting bridge at {boot_time_str}")
        
        # Start emulator first to set the IP
        self.start_emulator(ip, mac, netmask, gateway)
        
        # Give emulator a moment to initialize
        time.sleep(1)
        
        # Start web server and printer worker
        self.start_web_server()
        self.start_printer_worker()
        
        logging.info("[Bridge] Epson emulator, printer worker, and web server started.")
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("[Bridge] Shutting down...")
            self.running = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Epson TM-m30 to ESC/POS Bridge with Web Interface")
    # Printer connection options
    connection_group = parser.add_mutually_exclusive_group(required=True)
    connection_group.add_argument("--printer-ip", help="IP address of the target ESC/POS printer")
    connection_group.add_argument("--usb", action="store_true", help="Use USB printer connection")
    
    parser.add_argument("--printer-port", type=int, default=9100, help="Port of the target ESC/POS printer (network only)")
    parser.add_argument("--vendor-id", type=lambda x: int(x, 16), help="USB Vendor ID in hex (required for USB)")
    parser.add_argument("--product-id", type=lambda x: int(x, 16), help="USB Product ID in hex (required for USB)")
    parser.add_argument("--format", choices=["image", "raw"], default="image", help="Print format: 'image' (convert to image) or 'raw' (direct ESC/POS)")
    parser.add_argument("--web-port", type=int, default=8080, help="Web interface port (default: 8080)")
    parser.add_argument("--ip", default=None, help="Epson emulator IP address")
    parser.add_argument("--mac", default=None, help="Epson emulator MAC address")
    parser.add_argument("--netmask", default=None, help="Epson emulator netmask")
    parser.add_argument("--gateway", default=None, help="Epson emulator gateway")
    args = parser.parse_args()

    # Validate USB arguments if USB mode is selected
    if args.usb:
        if args.vendor_id is None or args.product_id is None:
            parser.error("--vendor-id and --product-id are required when using USB connection")
    
    bridge = BridgeEpsonToESCPOS(
        format=args.format,
        printer_ip=args.printer_ip,
        printer_port=args.printer_port,
        vendor_id=args.vendor_id,
        product_id=args.product_id,
        web_port=args.web_port
    )
    bridge.run(ip=args.ip, mac=args.mac, netmask=args.netmask, gateway=args.gateway)
